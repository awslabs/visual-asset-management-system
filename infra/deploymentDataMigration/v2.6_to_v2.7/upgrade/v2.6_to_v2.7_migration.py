#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.6 to v2.7 - orphaned workflow-trigger cleanup, vector index
backfill, and the system-pipeline retirement report.

The v2.7 release introduces:
  1. A consolidated SYSTEM GenAI metadata pipeline (``system-genai-metadata``) that replaces the
     GenAI 3D Metadata Labeling and CAD/Mesh Metadata Extraction built-ins. The v2.7 deploy removes
     the two retired nested stacks, and the schema importer archives their pipeline and workflow
     rows (``genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd`` and ``metadata-extraction-cad-mesh``
     under ``GLOBAL``). Archiving a workflow leaves its ``fileUpload`` trigger rows in place, and the
     upload dispatcher keeps matching them: every matching upload then pays one executeWorkflow
     invocation that ends ``400 Workflow is archived and cannot be executed.`` The ``orphanedTriggers``
     step deletes every trigger row whose workflow is archived or missing - the retired built-ins'
     rows and the rows of any user workflow archived through the API.
  2. Optional natural-language search over files, backed by a DynamoDB vector index. Embeddings are
     produced by the system pipeline's upload trigger, so a file that existed before the upgrade has
     no vector until it is re-processed. The ``vectorBackfill`` step invokes the deployed
     ``vectorReindexer`` Lambda, which enumerates the latest live version of every file and enqueues
     one system-workflow execution per file, paced by ``app.vectorSearch.indexingConcurrency``.
     ``--clear-vectors`` deletes every stored vector first (``operation: both``); the default
     (``operation: enqueue``) leaves existing vectors in place and re-embeds every file.
  3. Executions of the retired workflows that were RUNNING during the deploy lose their inner state
     machine and stay RUNNING until the workflow task times out (18 000 s for GenAI labeling, 900 s
     for CAD/Mesh extraction), then turn FAILED. User workflows that reference a retired pipeline id
     fail at execute because the pipeline is archived. The ``systemPipelineRetirement`` step reports
     both sets and prints guidance. It writes nothing.

Configuration: set ``resource_names_ssm_param_prefix`` (from the core stack output
``ResourceNamesSSMParamPrefixOutput``); every table and function name is then resolved from SSM
Parameter Store. Explicit ``*_table_name`` / ``vector_reindexer_function_name`` values remain
supported as optional overrides.

Usage:
    # Dry run (recommended first step)
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --dry-run

    # Production migration, all steps
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json

    # One step
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps orphanedTriggers

    # Rebuild the vector index from scratch (deletes every stored vector first)
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps vectorBackfill --clear-vectors

    # Fire-and-forget backfill for very large deployments
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps vectorBackfill --async

Requirements:
    - Python 3.9+
    - boto3
    - AWS credentials with the IAM actions listed in v2.6_to_v2.7_migration_README.md
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

_HERE = os.path.dirname(os.path.abspath(__file__))

# Shared migration tooling (infra/deploymentDataMigration/tools)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "tools"))
from ssm_resource_lookup import ResourceParamKeys, SsmResourceLookup  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

GLOBAL_DATABASE = 'GLOBAL'

# The two built-ins v2.7 retires. Pipeline and workflow share an id; both live under GLOBAL.
RETIRED_PIPELINE_IDS = (
    'genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd',
    'metadata-extraction-cad-mesh',
)
RETIRED_WORKFLOW_IDS = RETIRED_PIPELINE_IDS
# taskTimeout each retired pipeline shipped with: how long an execution caught mid-flight by the
# deploy stays RUNNING before the workflow records it FAILED.
RETIRED_TASK_TIMEOUT_SECONDS = {
    'genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd': 18000,
    'metadata-extraction-cad-mesh': 900,
}
# The pipeline that replaces them, named in the report's guidance.
REPLACEMENT_PIPELINE_COMPOSITE = 'GLOBAL:system-genai-metadata'
REPLACEMENT_TEMPLATE_ID = 'system-genai-metadata-default'

# Workflow trigger base types (mirrors common.workflows.workflowRecords.TRIGGER_TYPES).
TRIGGER_BASE_TYPES = ('fileUpload',)
TRIGGERS_BY_BASE_TYPE_GSI = 'TriggersByBaseTypeGSI'
EXECUTIONS_BY_WORKFLOW_GSI = 'WorkflowExecutionsByWorkflowGSI'

# Mirrors common.workflows.executionOutputs.TERMINAL_STATUSES.
TERMINAL_EXECUTION_STATUSES = ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED_OUT')

# Config values the shipped template leaves as placeholders are treated as unset.
_PLACEHOLDER_PREFIXES = ('<', 'YOUR-')

_STS_CLIENT_CONFIG = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
_DYNAMODB_CLIENT_CONFIG = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
# One attempt: a synchronous invoke that outlives the read timeout is reported as "continues in the
# background" and never re-issued (a retry would start a second clear or a second enqueue pass).
_LAMBDA_CLIENT_CONFIG = Config(connect_timeout=10, read_timeout=900,
                               retries={'total_max_attempts': 1, 'mode': 'adaptive'})


def confirm_migration_target(
    profile: Optional[str],
    region: Optional[str],
    base_param_prefix: Optional[str],
    dry_run: bool,
    destructive: bool,
    destructive_summary: str,
    confirm_account: Optional[str],
    assume_yes: bool,
) -> bool:
    """Echo the resolved target and, for a destructive run, require the operator to confirm it.

    Returns True to proceed, False to abort.

    The target comes entirely from the config file and CLI flags, and the shipped template leaves
    aws_profile and aws_region null, so boto3 falls back to whatever is in the environment. The SSM
    prefix carries no account or Region either - it is /{configName}-{baseStackName}/resourceNames -
    so two deployments sharing a config name and stack name resolve the SAME parameter paths and the
    migration would read valid table names out of the wrong account without anything looking unusual.

    Printing sts:GetCallerIdentity is the cheap half and always runs. The prompt is required only when
    the run will DELETE something (``destructive_summary`` names what). --confirm-account lets an
    automated run assert the expected account instead of answering a prompt; --yes skips the prompt
    for an operator who has already checked.
    """
    session_kwargs = {}
    if profile:
        session_kwargs['profile_name'] = profile
    if region:
        session_kwargs['region_name'] = region

    try:
        session = boto3.Session(**session_kwargs)
        identity = session.client('sts', config=_STS_CLIENT_CONFIG).get_caller_identity()
        account = identity.get('Account')
        caller_arn = identity.get('Arn')
    except Exception as e:
        # Not fatal on its own: a principal may be denied sts:GetCallerIdentity yet hold every
        # permission the migration needs. Reported so the operator knows the echo below is incomplete
        # rather than reassuring.
        logger.warning(f"Could not resolve the caller identity (sts:GetCallerIdentity failed: {e}).")
        account, caller_arn = None, None

    resolved_region = session_kwargs.get('region_name') or getattr(
        boto3.Session(**session_kwargs), 'region_name', None
    )

    logger.info("")
    logger.info("##### MIGRATION TARGET #####")
    logger.info(f"  Account:      {account or 'UNRESOLVED'}")
    logger.info(f"  Region:       {resolved_region or 'UNRESOLVED (boto3 default)'}")
    logger.info(f"  Caller:       {caller_arn or 'UNRESOLVED'}")
    logger.info(f"  Profile:      {profile or 'none (ambient credentials)'}")
    logger.info(f"  SSM prefix:   {base_param_prefix or 'not configured'}")
    logger.info(f"  Dry run:      {dry_run}")
    logger.info(f"  Deletes data: {destructive and not dry_run}"
                f"{' (' + destructive_summary + ')' if destructive and not dry_run else ''}")
    logger.info("############################")
    logger.info("")

    if confirm_account:
        if account is None:
            logger.error(
                "--confirm-account was given but the caller identity could not be resolved, so the "
                "account cannot be checked. Grant sts:GetCallerIdentity or drop the flag."
            )
            return False
        if account != confirm_account:
            logger.error(
                f"Refusing to run: --confirm-account {confirm_account} does not match the resolved "
                f"account {account}."
            )
            return False
        logger.info(f"Account {account} matches --confirm-account.")
        return True

    if dry_run or not destructive:
        # Nothing is deleted, so an interactive gate would only train the operator to dismiss it.
        return True

    if assume_yes:
        logger.info("Proceeding without a prompt (--yes).")
        return True

    if not sys.stdin.isatty():
        logger.error(
            f"This run deletes {destructive_summary} and stdin is not a terminal, so it cannot be "
            "confirmed interactively. Re-run with --confirm-account <id> (preferred, it verifies the "
            "target) or --yes."
        )
        return False

    answer = input(
        f"This will DELETE {destructive_summary} in account {account or 'UNKNOWN'} "
        f"({resolved_region or 'UNKNOWN region'}). Type the account id to continue: "
    ).strip()
    if account and answer != account:
        logger.error("Aborted: the value entered does not match the resolved account id.")
        return False
    if not account and answer.lower() not in ('yes', 'y'):
        logger.error("Aborted.")
        return False
    return True


def load_config_from_file(config_file: str) -> dict:
    """Load configuration from a JSON file, stripping comment fields."""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        config = {k: v for k, v in config.items()
                  if not k.startswith('_comment') and k not in ('comments', '_instructions')}
        logger.info(f"Loaded configuration from {config_file}")
        return config
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading configuration from {config_file}: {e}")
        sys.exit(1)


def _is_unset(value) -> bool:
    """True for None, blanks, and the '<...>' / 'YOUR-...' placeholders the config template ships."""
    if value is None:
        return True
    text = str(value).strip()
    return text == '' or text.startswith(_PLACEHOLDER_PREFIXES)


def _session(profile: Optional[str], region: Optional[str]) -> boto3.Session:
    kwargs = {}
    if profile:
        kwargs['profile_name'] = profile
    if region:
        kwargs['region_name'] = region
    return boto3.Session(**kwargs)


def make_resolver(config: dict, base_param_prefix: Optional[str], profile: Optional[str],
                  region: Optional[str]) -> Callable[[str, str], str]:
    """A resolve(cfg_key, param_key) closure: explicit config override first, else the SSM lookup.

    Raises ValueError when neither is available and KeyError (from SsmResourceLookup) when the
    parameter is not published for this deployment."""
    lookup = (SsmResourceLookup(base_param_prefix, profile=profile, region=region)
              if base_param_prefix else None)

    def resolve(cfg_key: str, param_key: str) -> str:
        override = config.get(cfg_key)
        if not _is_unset(override):
            return str(override)
        if not lookup:
            raise ValueError(
                f"Config '{cfg_key}' is unset and no resource_names_ssm_param_prefix is configured "
                "to resolve it from SSM.")
        return lookup.resolve(param_key)

    return resolve


def _attr_s(item: Optional[Dict], name: str, default: str = '') -> str:
    """A wire-format String attribute, or ``default``."""
    return ((item or {}).get(name) or {}).get('S', default)


def _attr_bool(item: Optional[Dict], name: str, default: bool = False) -> bool:
    """A wire-format Boolean attribute; a String 'true'/'false' is accepted for rows written by
    tooling that stored the flag as text."""
    value = (item or {}).get(name) or {}
    if 'BOOL' in value:
        return bool(value['BOOL'])
    if 'S' in value:
        return value['S'].strip().lower() == 'true'
    return default


def _paginate(read: Callable[..., Dict], **kwargs) -> Iterator[Dict]:
    """Yield every item of a paged Query/Scan. Continues on the PRESENCE of LastEvaluatedKey, which is
    the only end-of-set signal DynamoDB gives (a filtered page may be empty and still carry a key)."""
    while True:
        response = read(**kwargs)
        for item in response.get('Items', []):
            yield item
        if 'LastEvaluatedKey' not in response:
            return
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


#######################
# STEP: orphanedTriggers
#######################

def _trigger_workflow_identity(row: Dict) -> Tuple[str, str]:
    """(workflowDatabaseId, workflowId) of a trigger row, from the plain attributes or, for a row
    that lacks them, from the composite partition key."""
    database_id = _attr_s(row, 'workflowDatabaseId')
    workflow_id = _attr_s(row, 'workflowId')
    if not database_id or not workflow_id:
        database_id, _, workflow_id = _attr_s(row, 'workflowDatabaseId:workflowId').partition(':')
    return database_id, workflow_id


def is_retired_builtin_workflow(database_id: str, workflow_id: str) -> bool:
    """True for the GLOBAL rows of the two built-ins v2.7 removes. A user workflow that shares the id
    under another database is not a built-in."""
    return database_id == GLOBAL_DATABASE and workflow_id in RETIRED_WORKFLOW_IDS


def classify_trigger_row(row: Dict, workflow_lookup: Callable[[str, str], Optional[Dict]]) -> Tuple[str, str]:
    """('delete' | 'keep', reason) for one trigger row.

    ``workflow_lookup(database_id, workflow_id)`` returns the WorkflowStorageTableV2 item (wire
    format) or None. A retired built-in's trigger is deleted even when its workflow row is present and
    unarchived: the deploy-time archive is best-effort, and the pipeline resources behind the row are
    gone either way."""
    database_id, workflow_id = _trigger_workflow_identity(row)
    if is_retired_builtin_workflow(database_id, workflow_id):
        return 'delete', 'retired-builtin'
    workflow = workflow_lookup(database_id, workflow_id)
    if workflow is None:
        return 'delete', 'workflow-missing'
    if _attr_bool(workflow, 'archived'):
        return 'delete', 'workflow-archived'
    return 'keep', 'workflow-live'


def _workflow_lookup(dynamodb_client, workflow_table: str) -> Callable[[str, str], Optional[Dict]]:
    """Memoised GetItem on the workflow V2 table, keyed on (databaseId, workflowId)."""
    cache: Dict[Tuple[str, str], Optional[Dict]] = {}

    def lookup(database_id: str, workflow_id: str) -> Optional[Dict]:
        key = (database_id, workflow_id)
        if key not in cache:
            response = dynamodb_client.get_item(
                TableName=workflow_table,
                Key={'databaseId': {'S': database_id}, 'workflowId': {'S': workflow_id}},
                ConsistentRead=True,
            )
            cache[key] = response.get('Item')
        return cache[key]

    return lookup


def _list_trigger_rows(dynamodb_client, triggers_table: str, limit: Optional[int]) -> List[Dict]:
    """Every trigger row, by base type through TriggersByBaseTypeGSI, capped at ``limit`` rows.
    Collected before any delete so a delete never races the index pagination."""
    rows: List[Dict] = []
    for base_type in TRIGGER_BASE_TYPES:
        for row in _paginate(
            dynamodb_client.query,
            TableName=triggers_table,
            IndexName=TRIGGERS_BY_BASE_TYPE_GSI,
            KeyConditionExpression='triggerBaseType = :t',
            ExpressionAttributeValues={':t': {'S': base_type}},
        ):
            if limit is not None and len(rows) >= limit:
                logger.info(f"Limit of {limit} trigger row(s) reached; stopping the enumeration.")
                return rows
            rows.append(row)
    return rows


def delete_orphaned_triggers(dynamodb_client, cfg: Dict, dry_run: bool,
                             limit: Optional[int] = None) -> Dict[str, int]:
    """Delete every trigger row whose workflow is archived, missing, or a retired built-in.

    Returns counts. Under ``dry_run`` the ``deleted`` count is the number of rows that WOULD be
    deleted, so a dry-run summary is comparable to a real one; the log line names the mode."""
    triggers_table = cfg['workflow_triggers_storage_table_name']
    workflow_table = cfg['workflow_storage_table_name_v2']
    lookup = _workflow_lookup(dynamodb_client, workflow_table)
    counts = {'scanned': 0, 'deleted': 0, 'kept': 0, 'workflow_missing': 0,
              'workflow_archived': 0, 'retired_builtin': 0, 'errors': 0}
    reason_counter = {'workflow-missing': 'workflow_missing',
                      'workflow-archived': 'workflow_archived',
                      'retired-builtin': 'retired_builtin'}

    for row in _list_trigger_rows(dynamodb_client, triggers_table, limit):
        counts['scanned'] += 1
        pk = _attr_s(row, 'workflowDatabaseId:workflowId')
        sk = _attr_s(row, 'triggerType')
        decision, reason = classify_trigger_row(row, lookup)
        if decision == 'keep':
            counts['kept'] += 1
            logger.debug(f"  keep    {pk} / {sk} ({reason})")
            continue
        counts[reason_counter[reason]] += 1
        if dry_run:
            counts['deleted'] += 1
            logger.info(f"  [DRY RUN] Would delete trigger {pk} / {sk} ({reason})")
            continue
        try:
            dynamodb_client.delete_item(
                TableName=triggers_table,
                Key={'workflowDatabaseId:workflowId': {'S': pk}, 'triggerType': {'S': sk}},
            )
            counts['deleted'] += 1
            logger.info(f"  deleted trigger {pk} / {sk} ({reason})")
        except ClientError as e:
            counts['errors'] += 1
            logger.error(f"  failed deleting trigger {pk} / {sk}: {e}")
    return counts


def run_orphaned_triggers_step(config: dict, args, base_param_prefix, profile, region, dry_run) -> int:
    """Delete trigger rows of archived / missing / retired workflows. Returns 0 on success."""
    try:
        resolve = make_resolver(config, base_param_prefix, profile, region)
        cfg = {
            'workflow_triggers_storage_table_name': resolve(
                'workflow_triggers_storage_table_name', ResourceParamKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE),
            'workflow_storage_table_name_v2': resolve(
                'workflow_storage_table_name_v2', ResourceParamKeys.WORKFLOW_STORAGE_TABLE_V2),
        }
    except Exception as e:
        logger.error(f"Failed resolving table names for the orphanedTriggers step: {e}")
        return 1

    limit = args.limit if args.limit is not None else config.get('limit')
    dynamodb_client = _session(profile, region).client('dynamodb', config=_DYNAMODB_CLIENT_CONFIG)

    logger.info("=" * 80)
    logger.info("VAMS v2.6 -> v2.7 ORPHANED WORKFLOW-TRIGGER CLEANUP")
    logger.info(f"Triggers table: {cfg['workflow_triggers_storage_table_name']}")
    logger.info(f"Workflow table: {cfg['workflow_storage_table_name_v2']}")
    logger.info(f"Dry Run: {dry_run}   Limit: {limit}")
    logger.info("=" * 80)

    start = datetime.now(timezone.utc)
    try:
        counts = delete_orphaned_triggers(dynamodb_client, cfg, dry_run, limit)
    except Exception as e:
        logger.error(f"orphanedTriggers step failed: {e}")
        return 1
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info("=" * 80)
    logger.info("ORPHANED TRIGGER CLEANUP SUMMARY")
    logger.info(f"  Duration: {duration:.1f}s   Dry Run: {dry_run}")
    logger.info(f"  Trigger rows examined:            {counts['scanned']}")
    logger.info(f"  Rows {'that would be ' if dry_run else ''}deleted:  {counts['deleted']}")
    logger.info(f"    workflow archived:              {counts['workflow_archived']}")
    logger.info(f"    workflow missing:               {counts['workflow_missing']}")
    logger.info(f"    retired built-in:               {counts['retired_builtin']}")
    logger.info(f"  Rows kept (workflow live):        {counts['kept']}")
    logger.info(f"  Errors:                           {counts['errors']}")
    logger.info("=" * 80)

    return 0 if counts['errors'] == 0 else 1


#######################
# STEP: vectorBackfill
#######################

def build_vector_reindexer_payload(clear_vectors: bool, dry_run: bool,
                                   limit: Optional[int] = None) -> Dict:
    """The vectorReindexer direct-invoke payload: ``both`` (clear every stored vector, then enqueue)
    when ``clear_vectors`` is set, else ``enqueue``. Keys are the reindexer's own camelCase contract;
    it rejects unknown top-level keys."""
    payload = {'operation': 'both' if clear_vectors else 'enqueue', 'dryRun': bool(dry_run)}
    if limit is not None:
        payload['limit'] = int(limit)
    return payload


def invoke_vector_reindexer(lambda_client, function_name: str, payload: Dict,
                            invocation_type: str = 'RequestResponse') -> Dict:
    """Invoke the deployed vectorReindexer once and classify the outcome (never raises for an AWS
    error or a read timeout; both are returned so the step can report them). A synchronous answer is
    API-shaped, ``{statusCode, body: <JSON string>}``, and ``body`` decodes to the reindexer's response
    fields ``{operation, reindexRunId, phase, dryRun, deleted, enqueued, chunks, continued,
    invocations, tableEmpty?}``; an unhandled exception arrives as the Lambda runtime's error object
    instead, flagged by ``FunctionError``."""
    payload_json = json.dumps(payload)
    logger.info(f"Invoking {function_name} ({invocation_type}) with payload {payload_json}")
    start_time = time.time()
    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=payload_json,
        )
    except ReadTimeoutError as e:
        logger.warning("=" * 80)
        logger.warning("LAMBDA INVOCATION TIMED OUT")
        logger.warning(f"The invocation of '{function_name}' outlived the client read timeout. The "
                       "function keeps running (it re-invokes itself to continue past its own "
                       "15-minute cap). Follow progress with the commands in the summary below.")
        logger.warning("=" * 80)
        return {'timeout': True, 'warning': str(e), 'function_name': function_name}
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        logger.error(f"AWS Error ({error_code}): {error_message}")
        if error_code == 'ResourceNotFoundException':
            logger.error(f"Lambda function '{function_name}' not found. The vectorReindexer exists "
                         "only when app.vectorSearch.enabled is true; check the SSM parameter "
                         "lambdaFunctions/vectorReindexer under the deployment's resourceNames prefix.")
        elif error_code == 'AccessDeniedException':
            logger.error("Access denied. Ensure your IAM principal has lambda:InvokeFunction on the "
                         "vectorReindexer function.")
        return {'error': error_message, 'error_code': error_code}

    elapsed = time.time() - start_time
    status_code = response.get('StatusCode')

    if invocation_type == 'Event':
        if status_code == 202:
            logger.info("=" * 80)
            logger.info("LAMBDA INVOCATION SUBMITTED (ASYNCHRONOUS)")
            logger.info(f"Function: {function_name}")
            logger.info("=" * 80)
            return {'statusCode': 202, 'message': 'Vector backfill submitted asynchronously',
                    'function_name': function_name}
        error_msg = f"Lambda invocation failed with status code: {status_code}"
        logger.error(error_msg)
        return {'statusCode': status_code, 'error': error_msg, 'body': {}}

    payload_out = json.loads(response['Payload'].read())

    if status_code != 200 or 'FunctionError' in response:
        # The payload is the runtime's {errorMessage, errorType, ...} object, not a reindexer response.
        logger.error(f"Lambda invocation failed (status {status_code}, "
                     f"FunctionError={response.get('FunctionError')}): "
                     f"{json.dumps(payload_out, indent=2, default=str)}")
        return {'statusCode': status_code,
                'error': response.get('FunctionError') or f"status {status_code}",
                'body': payload_out}

    inner_status = payload_out['statusCode']
    body = json.loads(payload_out['body'])
    if inner_status >= 400:
        logger.error(f"vectorReindexer returned {inner_status}: {json.dumps(body, default=str)}")
        return {'statusCode': inner_status, 'error': f"vectorReindexer returned {inner_status}",
                'body': body}

    logger.info("=" * 80)
    logger.info("LAMBDA INVOCATION SUCCESSFUL")
    logger.info(f"Execution Time: {elapsed:.2f} seconds")
    logger.info("=" * 80)
    return {'statusCode': 200, 'body': body}


def describe_vector_backfill_result(result: Dict, function_name: str, clear_vectors: bool,
                                    dry_run: bool) -> None:
    """Summarise the invoke outcome and print how to watch the backfill it started. The reindexer's
    ``reindexRunId`` is the run id: its executions carry ``executionGroupId = vec-{reindexRunId}-{chunk}``
    (chunks numbered from 0, one per 1,000 files), the value ``vamscli execution list --group-id``
    filters on."""
    run_id = None
    chunks = 0
    logger.info("=" * 80)
    logger.info("VECTOR BACKFILL SUMMARY")
    logger.info(f"  Operation:       "
                f"{'both (clear every vector, then enqueue)' if clear_vectors else 'enqueue'}"
                f"   Dry run: {dry_run}")
    if result.get('timeout'):
        logger.warning("  Outcome:         invocation timed out; the reindexer continues in the "
                       "background and logs its run id to CloudWatch")
    elif result.get('statusCode') == 202:
        logger.info("  Outcome:         submitted asynchronously; the run id and counts are in the "
                    "function's CloudWatch Logs")
    elif 'error' in result:
        logger.error(f"  Outcome:         FAILED - {result.get('error')}")
        if result.get('body'):
            logger.error(f"  Response:        {json.dumps(result['body'], indent=2, default=str)}")
    else:
        body = result['body']
        run_id = body['reindexRunId']
        chunks = body['chunks']
        logger.info(f"  Reindex run id:  {run_id}")
        logger.info(f"  Phase: {body['phase']}   Deleted: {body['deleted']}   "
                    f"Enqueued: {body['enqueued']}   Chunks: {chunks}   "
                    f"Invocations: {body['invocations']}")
        if body['continued']:
            logger.info("  Continued:       the reindexer re-invoked itself to finish; the counts above "
                        "cover the invocations so far")
        if 'tableEmpty' in body:
            logger.info(f"  Table empty:     {body['tableEmpty']}")
    run_label = run_id or '<runId>'
    logger.info("  Watch progress:")
    if run_id is None:
        logger.info("    Execution group ids take the form vec-<runId>-<chunk> (chunks from 0, one per "
                    "1,000 files); the run id is in the reindexer's CloudWatch Logs")
    elif chunks:
        span = f"vec-{run_id}-0" + (f" .. vec-{run_id}-{chunks - 1}" if chunks > 1 else "")
        logger.info(f"    Execution group ids for this run: {span} (one per 1,000 files)")
    else:
        logger.info("    Execution group ids for this run: none (this invocation enqueued nothing)")
    logger.info(f"    vamscli execution list --group-id vec-{run_label}-0 --auto-paginate")
    logger.info("    vamscli execution list --workflow-database-id GLOBAL "
                "--workflow-id system-genai-metadata --trigger-type System-Reindex --status RUNNING")
    logger.info(f"    vamscli execution abort <executionId> --group-id vec-{run_label}-<chunk> --yes")
    logger.info(f"    CloudWatch Logs: /aws/lambda/{function_name} (enumeration and continuation) "
                "and the systemWorkflowLauncher function (one log line per launched execution)")
    logger.info("    The SystemWorkflowLaunchQueue depth drains as executions launch; the launcher "
                "runs at most app.vectorSearch.indexingConcurrency executions at a time.")
    logger.info("=" * 80)


def run_vector_backfill_step(config: dict, args, base_param_prefix, profile, region, dry_run,
                             clear_vectors: bool, explicit: bool) -> int:
    """Invoke the deployed vectorReindexer to (clear and) re-embed every latest live file.

    ``explicit`` is True when ``--steps vectorBackfill`` named this step. The vectorReindexer is
    published to SSM only when app.vectorSearch.enabled is true, so on a deployment without it an
    explicit run is an error (return 1) while ``--steps all`` skips the step with a warning (0). The
    same rule applies when nothing can resolve the name at all (no SSM prefix and no explicit
    override): SsmResourceLookup raises KeyError for the first case, make_resolver ValueError for the
    second."""
    try:
        resolve = make_resolver(config, base_param_prefix, profile, region)
        function_name = resolve('vector_reindexer_function_name',
                                ResourceParamKeys.VECTOR_REINDEXER_FUNCTION)
    except (KeyError, ValueError) as e:
        message = ("No vectorReindexer function is published for this deployment (SSM key "
                   "lambdaFunctions/vectorReindexer) or its name cannot be resolved. The function "
                   "exists only when app.vectorSearch.enabled is true; enable vector search and "
                   "redeploy, or set vector_reindexer_function_name explicitly in the config.")
        if explicit:
            logger.error(message)
            logger.error(str(e))
            return 1
        logger.warning(message)
        logger.warning(str(e))
        logger.warning("Skipping the vectorBackfill step.")
        return 0
    except Exception as e:
        logger.error(f"Failed resolving the vectorReindexer function name: {e}")
        return 1

    limit = args.limit if args.limit is not None else config.get('limit')
    invocation_type = 'Event' if args.async_invoke else 'RequestResponse'
    payload = build_vector_reindexer_payload(clear_vectors, dry_run, limit)

    logger.info("=" * 80)
    logger.info("VAMS v2.6 -> v2.7 VECTOR INDEX BACKFILL")
    logger.info(f"Function: {function_name}")
    logger.info(f"Operation: {payload['operation']}   Dry Run: {dry_run}   Limit: {limit}")
    logger.info(f"Invocation Type: {invocation_type}")
    logger.info("=" * 80)

    lambda_client = _session(profile, region).client('lambda', config=_LAMBDA_CLIENT_CONFIG)
    result = invoke_vector_reindexer(lambda_client, function_name, payload, invocation_type)
    describe_vector_backfill_result(result, function_name, clear_vectors, dry_run)

    if result.get('timeout'):
        # The reindexer self-continues; a timed-out client read is not a failed backfill.
        return 0
    return 1 if 'error' in result else 0


#######################
# STEP: systemPipelineRetirement (report only)
#######################

def list_inflight_retired_executions(dynamodb_client, executions_table: str,
                                     limit: Optional[int] = None) -> List[Dict]:
    """Executions of the retired GLOBAL workflows whose status is not terminal, via
    WorkflowExecutionsByWorkflowGSI. ``limit`` caps the rows returned per retired workflow."""
    found: List[Dict] = []
    for workflow_id in RETIRED_WORKFLOW_IDS:
        composite = f"{GLOBAL_DATABASE}:{workflow_id}"
        per_workflow = 0
        for row in _paginate(
            dynamodb_client.query,
            TableName=executions_table,
            IndexName=EXECUTIONS_BY_WORKFLOW_GSI,
            KeyConditionExpression='#wk = :w',
            ExpressionAttributeNames={'#wk': 'workflowDatabaseId:workflowId'},
            ExpressionAttributeValues={':w': {'S': composite}},
        ):
            status = _attr_s(row, 'executionStatus')
            if status in TERMINAL_EXECUTION_STATUSES:
                continue
            found.append({
                'executionId': _attr_s(row, 'workflowExecutionId'),
                'workflowId': workflow_id,
                'status': status or 'UNKNOWN',
                'startDate': _attr_s(row, 'executionStartDate'),
                'taskTimeoutSeconds': RETIRED_TASK_TIMEOUT_SECONDS.get(workflow_id),
            })
            per_workflow += 1
            if limit is not None and per_workflow >= limit:
                break
    return found


def _specified_pipeline_refs(row: Dict) -> List[str]:
    """The 'pipelineDatabaseId:pipelineId' composites a workflow row references, in step order."""
    refs: List[str] = []
    for entry in ((row.get('specifiedPipelines') or {}).get('L') or []):
        item = entry.get('M') or {}
        composite = _attr_s(item, 'pipelineDatabaseId:pipelineId')
        if not composite:
            composite = f"{_attr_s(item, 'pipelineDatabaseId')}:{_attr_s(item, 'pipelineId')}"
        refs.append(composite)
    return refs


def find_workflows_referencing_retired_pipelines(dynamodb_client, workflow_table: str,
                                                 limit: Optional[int] = None) -> List[Dict]:
    """Workflow V2 rows (other than the retired built-ins themselves) whose specifiedPipelines name a
    retired GLOBAL pipeline. ``limit`` caps the rows scanned."""
    retired = {f"{GLOBAL_DATABASE}:{pipeline_id}" for pipeline_id in RETIRED_PIPELINE_IDS}
    found: List[Dict] = []
    scanned = 0
    for row in _paginate(dynamodb_client.scan, TableName=workflow_table):
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        database_id = _attr_s(row, 'databaseId')
        workflow_id = _attr_s(row, 'workflowId')
        if is_retired_builtin_workflow(database_id, workflow_id):
            continue
        hits = [ref for ref in _specified_pipeline_refs(row) if ref in retired]
        if hits:
            found.append({
                'databaseId': database_id,
                'workflowId': workflow_id,
                'archived': _attr_bool(row, 'archived'),
                'retiredReferences': hits,
            })
    return found


def describe_retired_definitions(dynamodb_client, workflow_table: str, pipeline_table: str) -> List[Dict]:
    """Presence and archive state of the retired GLOBAL pipeline and workflow rows."""
    rows: List[Dict] = []
    for retired_id in RETIRED_PIPELINE_IDS:
        for kind, table, key_name in (('pipeline', pipeline_table, 'pipelineId'),
                                      ('workflow', workflow_table, 'workflowId')):
            item = dynamodb_client.get_item(
                TableName=table,
                Key={'databaseId': {'S': GLOBAL_DATABASE}, key_name: {'S': retired_id}},
                ConsistentRead=True,
            ).get('Item')
            rows.append({
                'kind': kind,
                'id': retired_id,
                'present': item is not None,
                'archived': _attr_bool(item, 'archived') if item else None,
                'enabled': _attr_bool(item, 'enabled', True) if item else None,
            })
    return rows


def build_system_pipeline_retirement_report(dynamodb_client, cfg: Dict,
                                            limit: Optional[int] = None) -> Dict:
    """Read-only: the three listings the operator needs after retiring the two built-ins."""
    return {
        'definitions': describe_retired_definitions(
            dynamodb_client, cfg['workflow_storage_table_name_v2'], cfg['pipeline_storage_table_name_v2']),
        'inflightExecutions': list_inflight_retired_executions(
            dynamodb_client, cfg['workflow_executions_storage_table_name_v2'], limit),
        'referencingWorkflows': find_workflows_referencing_retired_pipelines(
            dynamodb_client, cfg['workflow_storage_table_name_v2'], limit),
    }


def print_system_pipeline_retirement_report(report: Dict) -> None:
    definitions = report['definitions']
    inflight = report['inflightExecutions']
    referencing = report['referencingWorkflows']

    logger.info("=" * 80)
    logger.info("SYSTEM PIPELINE RETIREMENT REPORT (read-only; nothing was written)")
    logger.info("=" * 80)
    logger.info("Retired built-in definitions (GLOBAL):")
    for definition in definitions:
        if not definition['present']:
            state = 'absent'
        elif definition['archived']:
            state = 'archived (expected after the v2.7 deploy)'
        else:
            route = 'pipelines' if definition['kind'] == 'pipeline' else 'workflows'
            flag = '-p' if definition['kind'] == 'pipeline' else '-w'
            state = ('PRESENT AND NOT ARCHIVED - the deploy-time archive did not reach this row. '
                     f"Archive it: DELETE /database/GLOBAL/{route}/{definition['id']} "
                     f"(vamscli {definition['kind']} delete -d GLOBAL {flag} {definition['id']})")
        logger.info(f"  {definition['kind']:<8} {definition['id']}: {state}")

    logger.info("")
    logger.info(f"In-flight executions of retired workflows: {len(inflight)}")
    for execution in inflight:
        timeout = execution['taskTimeoutSeconds'] or 0
        logger.info(f"  {execution['executionId']}  {execution['workflowId']}  {execution['status']}  "
                    f"started {execution['startDate'] or 'unknown'}  "
                    f"(recorded FAILED after taskTimeout {timeout} s ~ {timeout / 3600:.2f} h)")
    if inflight:
        logger.warning("  ACTION REQUIRED: abort each with `vamscli execution abort <executionId>` or wait "
                       "out the task timeout. Their inner state machines were deleted with the retired "
                       "nested stacks, so no result will arrive and their full-mode sub-process logs are "
                       "gone; only the truncated log stored on the execution row remains.")

    logger.info("")
    logger.info(f"User workflows referencing a retired pipeline: {len(referencing)}")
    for workflow in referencing:
        logger.info(f"  {workflow['databaseId']}:{workflow['workflowId']}  archived={workflow['archived']}"
                    f"  -> {', '.join(workflow['retiredReferences'])}")
    if referencing:
        logger.warning("  ACTION REQUIRED: these workflows fail at execute because the referenced pipeline "
                       f"is archived. Edit each one to replace the step with {REPLACEMENT_PIPELINE_COMPOSITE} "
                       f"(template {REPLACEMENT_TEMPLATE_ID}) or remove the step. A fileUpload trigger whose "
                       "defaultTemplateIds names a retired pipeline resolves nothing until then.")

    if not inflight and not referencing:
        logger.info("Nothing to do: no in-flight retired executions and no referencing workflows.")
    logger.info("=" * 80)


def run_system_pipeline_retirement_step(config: dict, args, base_param_prefix, profile, region,
                                        dry_run) -> int:
    """Print the retirement report. Reads only; returns 0 unless a read or name resolution fails."""
    try:
        resolve = make_resolver(config, base_param_prefix, profile, region)
        cfg = {
            'workflow_executions_storage_table_name_v2': resolve(
                'workflow_executions_storage_table_name_v2',
                ResourceParamKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2),
            'workflow_storage_table_name_v2': resolve(
                'workflow_storage_table_name_v2', ResourceParamKeys.WORKFLOW_STORAGE_TABLE_V2),
            'pipeline_storage_table_name_v2': resolve(
                'pipeline_storage_table_name_v2', ResourceParamKeys.PIPELINE_STORAGE_TABLE_V2),
        }
    except Exception as e:
        logger.error(f"Failed resolving table names for the systemPipelineRetirement step: {e}")
        return 1

    limit = args.limit if args.limit is not None else config.get('limit')
    dynamodb_client = _session(profile, region).client('dynamodb', config=_DYNAMODB_CLIENT_CONFIG)

    logger.info("=" * 80)
    logger.info("VAMS v2.6 -> v2.7 SYSTEM PIPELINE RETIREMENT REPORT")
    logger.info(f"Executions table: {cfg['workflow_executions_storage_table_name_v2']}")
    logger.info(f"Workflow table:   {cfg['workflow_storage_table_name_v2']}")
    logger.info(f"Pipeline table:   {cfg['pipeline_storage_table_name_v2']}")
    logger.info(f"Limit: {limit}   (this step only reads; --dry-run changes nothing about it)")
    logger.info("=" * 80)

    try:
        report = build_system_pipeline_retirement_report(dynamodb_client, cfg, limit)
    except Exception as e:
        logger.error(f"systemPipelineRetirement step failed: {e}")
        return 1
    print_system_pipeline_retirement_report(report)
    return 0


#######################
# CLI
#######################

STEP_CHOICES = ('orphanedTriggers', 'vectorBackfill', 'systemPipelineRetirement', 'all')


def _destructive_summary(deletes_triggers: bool, clears_vectors: bool) -> str:
    """What a run deletes, for the confirmation prompt."""
    parts = []
    if deletes_triggers:
        parts.append('orphaned workflow-trigger rows (orphanedTriggers)')
    if clears_vectors:
        parts.append('every stored vector before re-embedding (vectorBackfill --clear-vectors)')
    return ' and '.join(parts) if parts else 'nothing'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='VAMS v2.6 to v2.7 data migration (orphaned trigger cleanup, vector index '
                    'backfill, system-pipeline retirement report).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run, all steps
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --dry-run

  # Production migration, all steps (prompts for the account id because orphanedTriggers deletes rows)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json

  # Delete orphaned trigger rows only
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps orphanedTriggers

  # Rebuild the vector index from scratch (clears every stored vector first)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps vectorBackfill --clear-vectors

  # Report in-flight retired executions and workflows that still reference a retired pipeline
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps systemPipelineRetirement

Notes:
  - Run after the v2.7 CDK deploy: the deploy archives the retired built-ins (orphanedTriggers relies
    on it), creates the vector table and index, and publishes the vectorReindexer function name to SSM.
  - vectorBackfill finishes when the reindexer has ENQUEUED the work; the executions it launches run on
    afterwards. The summary prints how to watch them.
  - --clear-vectors defaults to FALSE: the default re-embeds every latest file in place. Pass it only
    when the stored vectors must go first (a model change, or a rebuild from scratch).
  - --limit caps trigger rows examined (orphanedTriggers), is forwarded as the reindexer's limit
    (vectorBackfill), and caps rows listed per section (systemPipelineRetirement).
        """
    )

    parser.add_argument('--config', required=True,
                        help='Path to the migration JSON configuration file')
    parser.add_argument('--steps', choices=list(STEP_CHOICES), default='all',
                        help="Which release migration step(s) to run (default: all)")
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would change without deleting or launching anything '
                             '(also configurable in JSON)')
    parser.add_argument('--limit', type=int,
                        help='Cap on rows examined / files enqueued (testing)')
    parser.add_argument('--clear-vectors', dest='clear_vectors', action='store_true',
                        help='vectorBackfill: delete every stored vector before re-embedding '
                             '(reindexer operation "both" instead of "enqueue"). Default: false.')
    parser.add_argument('--async', dest='async_invoke', action='store_true',
                        help='vectorBackfill: invoke the reindexer asynchronously and return at once '
                             '(recommended for very large deployments)')
    parser.add_argument('--profile',
                        help='AWS profile name')
    parser.add_argument('--region',
                        help='AWS region')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')
    parser.add_argument('--confirm-account', dest='confirm_account',
                        help='Expected AWS account id. The migration refuses to run if the resolved '
                             'account differs, and no interactive confirmation is then required. '
                             'Preferred over --yes for automated runs, because it verifies the target '
                             'rather than only skipping the prompt.')
    parser.add_argument('--yes', action='store_true',
                        help='Skip the interactive confirmation before a step that deletes data '
                             '(orphanedTriggers, vectorBackfill --clear-vectors). Does not verify the '
                             'target account.')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    config = load_config_from_file(args.config)

    run_orphaned_triggers = args.steps in ('orphanedTriggers', 'all')
    run_vector_backfill = args.steps in ('vectorBackfill', 'all')
    run_retirement_report = args.steps in ('systemPipelineRetirement', 'all')

    dry_run = args.dry_run or bool(config.get('dry_run', False))
    # CLI flag wins; otherwise fall back to config (default false)
    clear_vectors = args.clear_vectors or bool(config.get('clear_vectors', False))
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')

    base_param_prefix = config.get('resource_names_ssm_param_prefix')
    if _is_unset(base_param_prefix):
        base_param_prefix = None

    # Echo the resolved target before touching anything. The shipped config template leaves
    # aws_profile and aws_region null, so boto3 falls back to the ambient environment, and the SSM
    # prefix is /{configName}-{baseStackName}/resourceNames with no account or Region in it. Two
    # deployments that share a config name and stack name therefore resolve identical parameter
    # paths, and nothing else in this script would reveal which account it had reached.
    clears_vectors = run_vector_backfill and clear_vectors
    if not confirm_migration_target(
        profile=profile,
        region=region,
        base_param_prefix=base_param_prefix,
        dry_run=dry_run,
        destructive=run_orphaned_triggers or clears_vectors,
        destructive_summary=_destructive_summary(run_orphaned_triggers, clears_vectors),
        confirm_account=args.confirm_account,
        assume_yes=args.yes,
    ):
        return 1

    exit_code = 0

    if run_orphaned_triggers:
        logger.info("")
        logger.info("##### STEP: Orphaned workflow-trigger cleanup #####")
        rc = run_orphaned_triggers_step(config, args, base_param_prefix, profile, region, dry_run)
        if rc != 0:
            exit_code = rc

    if run_vector_backfill:
        logger.info("")
        logger.info("##### STEP: Vector index backfill #####")
        rc = run_vector_backfill_step(config, args, base_param_prefix, profile, region, dry_run,
                                      clear_vectors=clear_vectors,
                                      explicit=(args.steps == 'vectorBackfill'))
        if rc != 0:
            exit_code = rc

    if run_retirement_report:
        logger.info("")
        logger.info("##### STEP: System-pipeline retirement report #####")
        rc = run_system_pipeline_retirement_step(config, args, base_param_prefix, profile, region,
                                                 dry_run)
        if rc != 0:
            exit_code = rc

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
