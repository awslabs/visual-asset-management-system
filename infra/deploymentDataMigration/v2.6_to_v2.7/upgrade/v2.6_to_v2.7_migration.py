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
     one system-workflow execution per file, at most ``app.vectorSearch.indexingConcurrency`` running at once.
     ``--clear-vectors`` deletes every stored vector first (``operation: both``); the default
     (``operation: enqueue``) leaves existing vectors in place and re-embeds every file. The step
     reads the deployment's VECTORSEARCH feature switch first: without it vector search is off and
     the step is skipped (exit 0), so the step also embeds the existing files on a 2.7 deployment
     whose vector search is turned on later.
     ``metadataBackfill`` (opt-in: ``generate_metadata_for_existing_files`` or
     ``--generate-metadata-for-existing-files``) is the same reindexer ``enqueue`` for a deployment with
     the system pipeline on and vector search off: the system workflow analyzes every existing latest
     live file and writes its GenAI metadata (no embeddings). It is skipped with vector search on, where
     ``vectorBackfill`` already analyzes every file, and when the pipeline is not enabled.
  3. Executions of the retired workflows that were RUNNING during the deploy lose their inner state
     machine and stay RUNNING until the workflow task times out (18 000 s for GenAI labeling, 900 s
     for CAD/Mesh extraction), then turn FAILED. User workflows that reference a retired pipeline id
     fail at execute because the pipeline is archived. The ``systemPipelineRetirement`` step reports
     both sets and prints guidance. It writes nothing.

Configuration: set ``resource_names_ssm_param_prefix`` (from the core stack output
``ResourceNamesSSMParamPrefixOutput``); every table and function name is then resolved from SSM
Parameter Store. Explicit ``*_table_name`` / ``vector_reindexer_function_name`` values remain
supported as optional overrides.

Safety: every run is a DRY RUN unless ``--execute`` is given and the config's ``dry_run`` is false (the
shipped config sets it true). A real run that deletes trigger rows, deletes vectors, or launches
Bedrock-billed executions is confirmed against the resolved AWS account id - typed at a prompt or
passed as ``--confirm-account`` - and refused when the identity cannot be resolved or confirmed.

Usage:
    # Dry run (the default; nothing is deleted or launched)
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json

    # Real run, all steps (asks for the account id)
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute

    # Real run without a prompt: the resolved account must equal the id given
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --confirm-account <account-id>

    # One step
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps orphanedTriggers

    # Rebuild the vector index from scratch (deletes every stored vector first)
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps vectorBackfill --clear-vectors

    # Fire-and-forget backfill for very large deployments
    python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps vectorBackfill --async

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
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

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

# Mirrors common.workflows.executionOutputs.TERMINAL_STATUSES. SKIPPED is the record of a trigger launch a
# concurrency restriction refused: it never ran, so it is never in flight.
TERMINAL_EXECUTION_STATUSES = ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED_OUT', 'SKIPPED')

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
    gated: bool,
    gated_summary: str,
    confirm_account: Optional[str],
    args_dry_run: Optional[bool] = None,
    step_plan: Optional[List[str]] = None,
) -> bool:
    """Echo the resolved target and, for a gated run, require the operator to confirm the account.

    ``dry_run`` is the effective mode (the command line or the config can each force a dry run);
    ``args_dry_run`` is what the command line alone said, so the echo can name the config when it is the
    config that kept an ``--execute`` run dry. It defaults to ``dry_run``. ``step_plan`` lists the steps
    this run takes, each with the reason it is skipped when it will be (printed as ``Steps:``).

    Returns True to proceed, False to refuse.

    The target comes entirely from the config file and CLI flags, and the shipped template leaves
    aws_profile and aws_region null, so boto3 falls back to whatever is in the environment. The SSM
    prefix carries no account either - it is /{configName}-{baseStackName}-{region}/resourceNames -
    so two deployments in different accounts that share a config name, stack name and Region resolve
    the SAME parameter paths and the migration would read valid table names out of the wrong account
    without anything looking unusual.

    Printing sts:GetCallerIdentity is the cheap half and always runs. A run is *gated* when it is not
    a dry run and it deletes trigger rows, deletes vectors, or launches Bedrock-billed executions
    (``gated_summary`` names what). A gated run proceeds only when the resolved account id is
    confirmed: ``--confirm-account`` equals it, or the operator types it at an interactive prompt.
    Nothing skips the check - when the identity cannot be resolved, or stdin is not a terminal and
    --confirm-account is absent, the run is refused. ``--confirm-account`` is checked whenever it is
    given, dry run or not, so a mismatch is caught on the first (dry) run.
    """
    if args_dry_run is None:
        args_dry_run = dry_run
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
        # Not fatal for a dry run or a read-only run: a principal may be denied
        # sts:GetCallerIdentity yet hold every permission those need. A gated run is refused below,
        # because an account it cannot name is an account it cannot confirm.
        logger.warning(f"Could not resolve the caller identity (sts:GetCallerIdentity failed: {e}).")
        account, caller_arn = None, None

    resolved_region = session_kwargs.get('region_name') or getattr(
        boto3.Session(**session_kwargs), 'region_name', None
    )
    confirmation_needed = gated and not dry_run

    logger.info("")
    logger.info("##### MIGRATION TARGET #####")
    logger.info(f"  Account:      {account or 'UNRESOLVED'}")
    logger.info(f"  Region:       {resolved_region or 'UNRESOLVED (boto3 default)'}")
    logger.info(f"  Caller:       {caller_arn or 'UNRESOLVED'}")
    logger.info(f"  Profile:      {profile or 'none (ambient credentials)'}")
    logger.info(f"  SSM prefix:   {base_param_prefix or 'not configured'}")
    if step_plan:
        logger.info(f"  Steps:        {', '.join(step_plan)}")
    if not dry_run:
        dry_run_note = ""
    elif args_dry_run:
        dry_run_note = "   (pass --execute for a real run)"
    else:
        dry_run_note = '   (--execute was given; the config\'s "dry_run": true keeps this a dry run)'
    logger.info(f"  Dry run:      {dry_run}{dry_run_note}")
    logger.info(f"  This run will: {gated_summary if confirmation_needed else 'delete nothing and bill nothing'}")
    logger.info("############################")
    logger.info("")

    if confirm_account:
        if account is None:
            logger.error(
                "--confirm-account was given but the caller identity could not be resolved, so the "
                "account cannot be checked. Grant sts:GetCallerIdentity to the principal and re-run."
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

    if not confirmation_needed:
        # Nothing is deleted or billed, so an interactive gate would only train the operator to
        # dismiss it.
        return True

    if account is None:
        logger.error(
            f"Refusing to run: this run would {gated_summary}, and the caller identity could not be "
            "resolved, so the target account cannot be confirmed. Grant sts:GetCallerIdentity to the "
            "principal and re-run."
        )
        return False

    if not sys.stdin.isatty():
        logger.error(
            f"Refusing to run: this run would {gated_summary} and stdin is not a terminal, so the "
            "account cannot be confirmed interactively. Re-run with --confirm-account <account-id> "
            "(the resolved account must equal it)."
        )
        return False

    answer = input(
        f"This run will {gated_summary} in account {account} "
        f"({resolved_region or 'UNKNOWN region'}). Type the account id to continue: "
    ).strip()
    if answer != account:
        logger.error("Refusing to run: the value entered does not match the resolved account id.")
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
# Deployment state: vector search
#######################

# The app feature switch the core stack writes to the app feature table when app.vectorSearch.enabled
# resolves true (infra/common/vamsAppFeatures.ts VAMS_APP_FEATURES.VECTORSEARCH). A deploy replaces
# the table's rows, so the row is absent on a deployment with vector search off.
VECTOR_SEARCH_FEATURE = 'VECTORSEARCH'
# The table's partition key (one row per enabled feature).
APP_FEATURE_KEY = 'featureName'

VECTOR_BACKFILL_SKIP_MESSAGE = ("Vector search is not enabled on this deployment, so there is nothing to "
                                "re-index; vectorBackfill skipped.")
VECTOR_BACKFILL_SKIPPED_OUTCOME = 'skipped: vector search is not enabled'

# metadataBackfill: the system workflow analyzes the existing files with vector search off. Each skip has
# its INFO line and its closing-summary outcome.
METADATA_BACKFILL_SKIPS = {
    'vector-on': ("Vector search is enabled on this deployment, and vectorBackfill analyzes every file "
                  "when vector search is on; metadataBackfill skipped.",
                  'skipped: vectorBackfill analyzes every file when vector search is on'),
    'not-requested': ("Metadata generation for existing files was not requested "
                      "(generate_metadata_for_existing_files is false); metadataBackfill skipped.",
                      'skipped: metadata generation for existing files not requested'),
    'pipeline-off': ("The SYSTEM GenAI metadata pipeline is not enabled on this deployment (no "
                     "vectorReindexer is published; the deploy publishes it whenever the pipeline is on "
                     "and registers its workflow); metadataBackfill skipped.",
                     'skipped: the SYSTEM GenAI metadata pipeline is not enabled'),
}


class VectorSearchState(NamedTuple):
    """What the deployment says about vector search, read once per run and shared by every step that
    depends on it.

    ``enabled``: the VECTORSEARCH feature switch row is present. ``reindexer_function``: the
    vectorReindexer name published under the SSM prefix, or None when none is published (or no
    prefix is configured). ``error``: why the state could not be read; when set, ``enabled`` is None
    and no step may treat vector search as off."""
    enabled: Optional[bool]
    reindexer_function: Optional[str]
    error: Optional[str] = None


class VectorBackfillPlan(NamedTuple):
    """``action`` is 'run' (with ``function_name``), 'skip' (with ``skip`` naming the case for a step that
    has more than one) or 'error' (with ``reason``)."""
    action: str
    function_name: Optional[str] = None
    reason: Optional[str] = None
    skip: Optional[str] = None


class StepResult(NamedTuple):
    """A step's exit code and the outcome the run's closing summary prints for it."""
    rc: int
    outcome: str


def read_vector_search_state(config: dict, base_param_prefix: Optional[str], profile: Optional[str],
                             region: Optional[str]) -> VectorSearchState:
    """Read whether vector search is on, from the deployment itself (read-only).

    The app feature table's name is resolved like every other name (the
    ``app_feature_enabled_storage_table_name`` override, else the SSM prefix); its VECTORSEARCH row is
    read with a consistent GetItem. ``reindexer_function`` is the vectorReindexer name the prefix
    publishes; the ``vector_reindexer_function_name`` override is not part of the state (a step that
    invokes the reindexer applies it). Each way the state can fail to be read returns an ``error``
    naming it: no prefix or override, SSM unreadable, a prefix that publishes no app feature table
    (usually a wrong prefix), or the table unreadable. A missing vectorReindexer parameter is not an
    error here: whether it matters depends on the switch, which is the caller's decision."""
    lookup = (SsmResourceLookup(base_param_prefix, profile=profile, region=region)
              if base_param_prefix else None)
    override = config.get('app_feature_enabled_storage_table_name')
    try:
        if not _is_unset(override):
            feature_table = str(override)
        elif lookup is None:
            return VectorSearchState(None, None, (
                "resource_names_ssm_param_prefix is not set, so the app feature table that holds the "
                "VECTORSEARCH feature switch cannot be resolved. Set the prefix from the core stack "
                "output ResourceNamesSSMParamPrefixOutput."))
        else:
            feature_table = lookup.resolve(ResourceParamKeys.APP_FEATURE_ENABLED_STORAGE_TABLE)
        reindexer = None
        if lookup is not None:
            try:
                reindexer = lookup.resolve(ResourceParamKeys.VECTOR_REINDEXER_FUNCTION)
            except KeyError:
                reindexer = None
    except KeyError:
        return VectorSearchState(None, None, (
            f"no app feature table name is published under {base_param_prefix} (SSM key "
            f"{ResourceParamKeys.APP_FEATURE_ENABLED_STORAGE_TABLE}). Check the prefix against the core "
            "stack output ResourceNamesSSMParamPrefixOutput."))
    except Exception as e:
        return VectorSearchState(None, None, (
            f"the SSM parameters under {base_param_prefix} could not be read ({e})."))

    try:
        dynamodb = _session(profile, region).client('dynamodb', config=_DYNAMODB_CLIENT_CONFIG)
        response = dynamodb.get_item(TableName=feature_table,
                                     Key={APP_FEATURE_KEY: {'S': VECTOR_SEARCH_FEATURE}},
                                     ConsistentRead=True)
    except Exception as e:
        return VectorSearchState(None, None, (
            f"the app feature table {feature_table} could not be read ({e})."))
    return VectorSearchState('Item' in response, reindexer)


def plan_vector_backfill(state: VectorSearchState, config: dict) -> VectorBackfillPlan:
    """Decide the vectorBackfill step from the deployment's state.

    Switch absent: skip, whether or not a vectorReindexer is published (the reindexer may exist for
    another reason, and with vector search off there is nothing to re-index); the
    ``vector_reindexer_function_name`` override is not used. Switch present: run on the override when
    it is set, else on the published reindexer; neither is an inconsistent deployment and an error. A
    state that could not be read is an error, never a skip."""
    if state.error:
        return VectorBackfillPlan('error', reason=(
            f"Cannot tell whether vector search is enabled on this deployment: {state.error} "
            "vectorBackfill did not run."))
    if not state.enabled:
        return VectorBackfillPlan('skip')
    override = config.get('vector_reindexer_function_name')
    if not _is_unset(override):
        return VectorBackfillPlan('run', function_name=str(override))
    if not state.reindexer_function:
        source = ("no resource_names_ssm_param_prefix is configured to resolve the vectorReindexer from"
                  if _is_unset(config.get('resource_names_ssm_param_prefix'))
                  else f"no vectorReindexer function is published (SSM key "
                       f"{ResourceParamKeys.VECTOR_REINDEXER_FUNCTION})")
        return VectorBackfillPlan('error', reason=(
            "Vector search is enabled on this deployment (the VECTORSEARCH feature switch is set), but "
            f"{source}. Redeploy so the search stack publishes it, or set vector_reindexer_function_name "
            "in the config. vectorBackfill did not run."))
    return VectorBackfillPlan('run', function_name=state.reindexer_function)


def plan_metadata_backfill(state: VectorSearchState, config: dict, requested: bool) -> VectorBackfillPlan:
    """Decide the metadataBackfill step from the same state as vectorBackfill.

    The deploy publishes the vectorReindexer whenever the system GenAI metadata pipeline is on and
    registers its workflow, so with the VECTORSEARCH switch absent a published reindexer means the
    pipeline is on. Switch present: skip (vectorBackfill analyzes every file), unless the reindexer is
    missing, which is the inconsistency vectorBackfill reports too. Switch absent and no reindexer: the
    pipeline is off, skip. Switch absent and a reindexer: run when requested (the
    ``vector_reindexer_function_name`` override replaces the published name), else skip. A state that
    could not be read is an error, never a skip."""
    if state.error:
        return VectorBackfillPlan('error', reason=(
            f"Cannot tell whether vector search or the SYSTEM GenAI metadata pipeline is enabled on this "
            f"deployment: {state.error} metadataBackfill did not run."))
    if state.enabled:
        if not state.reindexer_function and _is_unset(config.get('vector_reindexer_function_name')):
            return VectorBackfillPlan('error', reason=(
                "Vector search is enabled on this deployment (the VECTORSEARCH feature switch is set), but "
                "no vectorReindexer function is published, so the deployment is inconsistent. Redeploy, "
                "or set vector_reindexer_function_name in the config. metadataBackfill did not run."))
        return VectorBackfillPlan('skip', skip='vector-on')
    if not state.reindexer_function:
        return VectorBackfillPlan('skip', skip='pipeline-off')
    if not requested:
        return VectorBackfillPlan('skip', skip='not-requested')
    override = config.get('vector_reindexer_function_name')
    function_name = str(override) if not _is_unset(override) else state.reindexer_function
    return VectorBackfillPlan('run', function_name=function_name)


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
            logger.error(f"Lambda function '{function_name}' not found. Check the SSM parameter "
                         "lambdaFunctions/vectorReindexer under the deployment's resourceNames prefix "
                         "and the vector_reindexer_function_name override, then redeploy if the "
                         "parameter names a function that no longer exists.")
        elif error_code == 'AccessDeniedException':
            logger.error(f"Access denied. Ensure your IAM principal has lambda:InvokeFunction on the "
                         f"function '{function_name}' (the vectorReindexer name published at "
                         "lambdaFunctions/vectorReindexer under the deployment's resourceNames prefix).")
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
                                    dry_run: bool, title: str = 'VECTOR BACKFILL SUMMARY') -> None:
    """Summarise the invoke outcome and print how to watch the backfill it started. The reindexer's
    ``reindexRunId`` is the run id: its executions carry ``executionGroupId = vec-{reindexRunId}-{chunk}``
    (chunks numbered from 0, one per 1,000 files), the value ``vamscli execution list --group-id``
    filters on. A dry run deletes, enqueues and launches nothing: its counts are what a real run would
    do, and it prints no watch commands."""
    run_id = None
    chunks = 0
    deleted_label = 'Would delete' if dry_run else 'Deleted'
    enqueued_label = 'Would enqueue' if dry_run else 'Enqueued'
    logger.info("=" * 80)
    logger.info(title)
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
        logger.info(f"  Phase: {body['phase']}   {deleted_label}: {body['deleted']}   "
                    f"{enqueued_label}: {body['enqueued']}   Chunks: {chunks}   "
                    f"Invocations: {body['invocations']}")
        if body['continued']:
            logger.info("  Continued:       the reindexer re-invoked itself to finish; the counts above "
                        "cover the invocations so far")
        if 'tableEmpty' in body:
            logger.info(f"  Table empty:     {body['tableEmpty']}")
    if dry_run:
        logger.info("  Dry run:         nothing was deleted, enqueued or launched, so there is no "
                    "progress to watch")
        logger.info("=" * 80)
        return
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
    logger.info("    The SystemWorkflowLaunchQueue depth drains as executions launch; at most "
                "app.vectorSearch.indexingConcurrency of them run at once, and the rest wait in the queue.")
    logger.info("=" * 80)


def run_vector_backfill_step(config: dict, args, base_param_prefix, profile, region, dry_run,
                             clear_vectors: bool,
                             state: Optional[VectorSearchState] = None) -> StepResult:
    """Invoke the deployed vectorReindexer to (clear and) re-embed every latest live file, or skip it.

    Whether to run is decided from the deployment itself (``read_vector_search_state``): with the
    VECTORSEARCH feature switch absent, vector search is off and there is nothing to re-index, so the
    step is skipped and succeeds - under ``--steps all`` and ``--steps vectorBackfill`` alike, and
    whether or not a vectorReindexer is published. With the switch set, the step runs on the published
    reindexer (or the ``vector_reindexer_function_name`` override); a switch set with no reindexer to
    invoke, or a state that cannot be read, is an error and nothing is skipped. ``state`` is the state
    main() already read for the run's plan; it is read here when not given.

    The step runs on any 2.7 deployment, as often as needed: on an upgrade, and when vector search is
    turned on later (or off and on again), the reindexer enumerates the files as they are then."""
    if state is None:
        state = read_vector_search_state(config, base_param_prefix, profile, region)
    plan = plan_vector_backfill(state, config)
    if plan.action == 'skip':
        logger.info(VECTOR_BACKFILL_SKIP_MESSAGE)
        if not _is_unset(config.get('vector_reindexer_function_name')):
            logger.info("vector_reindexer_function_name is set in the config but is not used while vector "
                        "search is not enabled.")
        return StepResult(0, VECTOR_BACKFILL_SKIPPED_OUTCOME)
    if plan.action == 'error':
        logger.error(plan.reason)
        return StepResult(1, f"failed: {plan.reason}")
    function_name = plan.function_name

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
        return StepResult(0, "completed: the invocation timed out on the client; the reindexer "
                             "continues in the background")
    if 'error' in result:
        return StepResult(1, f"failed: {result.get('error')}")
    return StepResult(0, "completed (dry run: nothing launched)" if dry_run else "completed")


#######################
# STEP: metadataBackfill (opt-in)
#######################

def run_metadata_backfill_step(config: dict, args, base_param_prefix, profile, region, dry_run,
                               clear_vectors: bool, requested: bool,
                               state: Optional[VectorSearchState] = None) -> StepResult:
    """With the system pipeline on and vector search off, and only when requested, invoke the reindexer
    with ``enqueue`` so the system workflow analyzes every existing latest live file (GenAI metadata, no
    embeddings). Skipped with vector search on (vectorBackfill analyzes every file), when not requested,
    and when the pipeline is not enabled; an unreadable or inconsistent state is an error. Honours
    ``dry_run`` (the reindexer counts instead of launching), ``limit`` and ``--async`` like vectorBackfill.
    ``clear_vectors`` does not apply: there are no vectors with vector search off."""
    if state is None:
        state = read_vector_search_state(config, base_param_prefix, profile, region)
    plan = plan_metadata_backfill(state, config, requested)
    if plan.action == 'skip':
        line, outcome = METADATA_BACKFILL_SKIPS[plan.skip]
        logger.info(line)
        return StepResult(0, outcome)
    if plan.action == 'error':
        logger.error(plan.reason)
        return StepResult(1, f"failed: {plan.reason}")
    if clear_vectors:
        logger.info("--clear-vectors (clear_vectors) does not apply to metadataBackfill: vector search is "
                    "off, so there are no vectors to clear. It is ignored for this step.")

    limit = args.limit if args.limit is not None else config.get('limit')
    invocation_type = 'Event' if args.async_invoke else 'RequestResponse'
    payload = build_vector_reindexer_payload(False, dry_run, limit)

    logger.info("=" * 80)
    logger.info("VAMS v2.6 -> v2.7 METADATA BACKFILL (existing files, vector search off)")
    logger.info(f"Function: {plan.function_name}")
    logger.info(f"Operation: {payload['operation']}   Dry Run: {dry_run}   Limit: {limit}")
    logger.info(f"Invocation Type: {invocation_type}")
    logger.info("=" * 80)

    lambda_client = _session(profile, region).client('lambda', config=_LAMBDA_CLIENT_CONFIG)
    result = invoke_vector_reindexer(lambda_client, plan.function_name, payload, invocation_type)
    describe_vector_backfill_result(result, plan.function_name, False, dry_run,
                                    title='METADATA BACKFILL SUMMARY')
    if result.get('timeout'):
        return StepResult(0, "completed: the invocation timed out on the client; the reindexer "
                             "continues in the background")
    if 'error' in result:
        return StepResult(1, f"failed: {result.get('error')}")
    return StepResult(0, "completed (dry run: nothing launched)" if dry_run else "completed")


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
    logger.info(f"Limit: {limit}   (this step only reads; dry run or not, it changes nothing)")
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

STEP_CHOICES = ('orphanedTriggers', 'vectorBackfill', 'metadataBackfill', 'systemPipelineRetirement', 'all')


def _gated_summary(deletes_triggers: bool, backfills_vectors: bool, clears_vectors: bool,
                   backfills_metadata: bool = False) -> str:
    """What a real run deletes or bills, phrased to follow "This run will"."""
    parts = []
    if deletes_triggers:
        parts.append('delete orphaned workflow-trigger rows (orphanedTriggers)')
    if clears_vectors:
        parts.append('delete every stored vector (vectorBackfill --clear-vectors)')
    if backfills_vectors:
        parts.append('launch one Amazon Bedrock-billed execution per file (vectorBackfill)')
    if backfills_metadata:
        parts.append('launch one Amazon Bedrock-billed execution per file (metadataBackfill)')
    if not parts:
        return 'nothing'
    if len(parts) == 1:
        return parts[0]
    return ', '.join(parts[:-1]) + ' and ' + parts[-1]


def print_run_summary(outcomes: List[Tuple[str, str]], dry_run: bool) -> None:
    """The run's closing summary: one line per selected step with its outcome. A step skipped on
    purpose says why (``vectorBackfill: skipped: vector search is not enabled``)."""
    logger.info("")
    logger.info(f"##### MIGRATION SUMMARY{' (DRY RUN)' if dry_run else ''} #####")
    for step, outcome in outcomes:
        logger.info(f"  {step}: {outcome}")
    logger.info("############################")


def _account_id(value: str) -> str:
    """argparse type for --confirm-account: exactly twelve digits."""
    if len(value) != 12 or not value.isdigit():
        raise argparse.ArgumentTypeError('expected a 12-digit AWS account id')
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='VAMS v2.6 to v2.7 data migration (orphaned trigger cleanup, vector index '
                    'backfill, system-pipeline retirement report). Dry run by default.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run, all steps (the default: nothing is deleted or launched)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json

  # Real run, all steps (asks for the account id: orphanedTriggers deletes rows, vectorBackfill bills Bedrock)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute

  # Real run without a prompt (automation): the resolved account must equal the id given
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --confirm-account <account-id>

  # Delete orphaned trigger rows only
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps orphanedTriggers

  # Rebuild the vector index from scratch (clears every stored vector first)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps vectorBackfill --clear-vectors

  # Vector search off: analyze every existing file with the system pipeline (GenAI metadata, no vectors)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute \\
      --steps metadataBackfill --generate-metadata-for-existing-files

  # Report in-flight retired executions and workflows that still reference a retired pipeline (reads only)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps systemPipelineRetirement

Notes:
  - Every run is a DRY RUN unless --execute is given AND the config's dry_run is false. The shipped
    config sets "dry_run": true; set it to false once the dry run's counts look right.
  - A real run that deletes rows (orphanedTriggers), deletes vectors (--clear-vectors), or launches
    Bedrock-billed executions (vectorBackfill, with or without --clear-vectors) is confirmed against
    the resolved AWS account id: type it at the prompt, or pass --confirm-account <account-id>. No
    flag skips the check; an unresolved identity or a non-interactive run without --confirm-account
    is refused.
  - Run after the v2.7 CDK deploy: the deploy archives the retired built-ins (orphanedTriggers relies
    on it), creates the vector table and index, and publishes the vectorReindexer function name to SSM.
  - vectorBackfill reads the deployment's VECTORSEARCH feature switch first. Without it, vector search is
    off, the step is skipped (exit 0) and the summary says so; with it, a missing vectorReindexer is an
    error. Run --steps vectorBackfill again on any 2.7 deployment after turning vector search on.
  - metadataBackfill is opt-in (generate_metadata_for_existing_files, or
    --generate-metadata-for-existing-files): with the system pipeline on and vector search off it runs the
    system workflow over every existing file. It is skipped with vector search on (vectorBackfill does
    it) and when the pipeline is not enabled.
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
    parser.add_argument('--execute', dest='dry_run', action='store_false',
                        help='Run for real. Without it the migration is a dry run that reports what '
                             'would change and deletes or launches nothing. The config\'s dry_run '
                             'must also be false (the shipped config sets it true).')
    parser.add_argument('--limit', type=int,
                        help='Cap on rows examined / files enqueued (testing)')
    parser.add_argument('--clear-vectors', dest='clear_vectors', action='store_true',
                        help='vectorBackfill: delete every stored vector before re-embedding '
                             '(reindexer operation "both" instead of "enqueue"). Default: false.')
    parser.add_argument('--generate-metadata-for-existing-files', dest='generate_metadata',
                        action='store_true',
                        help='metadataBackfill: with the system pipeline on and vector search off, analyze '
                             'every existing latest live file with the system workflow for this run (sets '
                             'the config\'s generate_metadata_for_existing_files). Default: false.')
    parser.add_argument('--async', dest='async_invoke', action='store_true',
                        help='vectorBackfill: invoke the reindexer asynchronously and return at once '
                             '(recommended for very large deployments)')
    parser.add_argument('--profile',
                        help='AWS profile name')
    parser.add_argument('--region',
                        help='AWS region')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')
    parser.add_argument('--confirm-account', dest='confirm_account', type=_account_id,
                        help='Expected 12-digit AWS account id. The migration refuses to run when the '
                             'resolved account differs; when it matches, a real run needs no '
                             'interactive confirmation (required for automated real runs, where stdin '
                             'is not a terminal).')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    config = load_config_from_file(args.config)

    run_orphaned_triggers = args.steps in ('orphanedTriggers', 'all')
    run_vector_backfill = args.steps in ('vectorBackfill', 'all')
    run_metadata_backfill = args.steps in ('metadataBackfill', 'all')
    run_retirement_report = args.steps in ('systemPipelineRetirement', 'all')

    # Dry run unless --execute was given (args.dry_run is False only then) AND the config does not
    # pin dry_run: true. Either source can force a dry run; neither can force a real one alone.
    dry_run = args.dry_run or bool(config.get('dry_run', False))
    if dry_run and not args.dry_run:
        logger.warning(
            "--execute was given but the config sets dry_run: true, so this remains a DRY RUN. Set "
            f"dry_run to false in {args.config} to run for real."
        )
    # CLI flag wins; otherwise fall back to config (default false)
    clear_vectors = args.clear_vectors or bool(config.get('clear_vectors', False))
    generate_metadata = args.generate_metadata or bool(config.get('generate_metadata_for_existing_files', False))
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')

    base_param_prefix = config.get('resource_names_ssm_param_prefix')
    if _is_unset(base_param_prefix):
        base_param_prefix = None

    # Whether vector search is on decides whether vectorBackfill runs, so it is read before the plan is
    # printed and before the account gate names what the run bills. The read only reads (SSM and one
    # DynamoDB GetItem); nothing is deleted or launched before the target is echoed and confirmed.
    # metadataBackfill reads the same state, once.
    vector_state = None
    vector_plan = None
    metadata_plan = None
    if run_vector_backfill or run_metadata_backfill:
        vector_state = read_vector_search_state(config, base_param_prefix, profile, region)
    if run_vector_backfill:
        vector_plan = plan_vector_backfill(vector_state, config)
    if run_metadata_backfill:
        metadata_plan = plan_metadata_backfill(vector_state, config, generate_metadata)
    backfills_vectors = run_vector_backfill and vector_plan.action == 'run'
    backfills_metadata = run_metadata_backfill and metadata_plan.action == 'run'

    step_plan = []
    if run_orphaned_triggers:
        step_plan.append('orphanedTriggers')
    if run_vector_backfill:
        if vector_plan.action == 'skip':
            step_plan.append(f"vectorBackfill ({VECTOR_BACKFILL_SKIPPED_OUTCOME})")
        elif vector_plan.action == 'error':
            step_plan.append("vectorBackfill (fails: the vector search state is inconsistent or unreadable)")
        else:
            step_plan.append('vectorBackfill')
    if run_metadata_backfill:
        if metadata_plan.action == 'skip':
            step_plan.append(f"metadataBackfill ({METADATA_BACKFILL_SKIPS[metadata_plan.skip][1]})")
        elif metadata_plan.action == 'error':
            step_plan.append("metadataBackfill (fails: the deployment state is inconsistent or unreadable)")
        else:
            step_plan.append('metadataBackfill')
    if run_retirement_report:
        step_plan.append('systemPipelineRetirement')

    # Echo the resolved target before anything is deleted or billed. The shipped config template leaves
    # aws_profile and aws_region null, so boto3 falls back to the ambient environment, and the SSM
    # prefix is /{configName}-{baseStackName}-{region}/resourceNames with no account in it. Two
    # deployments in different accounts that share a config name, stack name and Region therefore
    # resolve identical parameter paths, and nothing else in this script would reveal which account
    # it had reached.
    #
    # The gate covers every step that deletes or bills: orphanedTriggers deletes rows, and a real
    # vectorBackfill launches one Bedrock-billed execution per file whether or not it clears the
    # stored vectors first. systemPipelineRetirement only reads.
    clears_vectors = backfills_vectors and clear_vectors
    if not confirm_migration_target(
        profile=profile,
        region=region,
        base_param_prefix=base_param_prefix,
        dry_run=dry_run,
        args_dry_run=args.dry_run,
        gated=run_orphaned_triggers or backfills_vectors or backfills_metadata,
        gated_summary=_gated_summary(run_orphaned_triggers, backfills_vectors, clears_vectors,
                                     backfills_metadata),
        confirm_account=args.confirm_account,
        step_plan=step_plan,
    ):
        return 1

    exit_code = 0
    outcomes: List[Tuple[str, str]] = []

    def _record(step: str, rc: int) -> None:
        outcomes.append((step, "completed" if rc == 0 else f"failed (exit {rc})"))

    if run_orphaned_triggers:
        logger.info("")
        logger.info("##### STEP: Orphaned workflow-trigger cleanup #####")
        rc = run_orphaned_triggers_step(config, args, base_param_prefix, profile, region, dry_run)
        _record('orphanedTriggers', rc)
        if rc != 0:
            exit_code = rc

    if run_vector_backfill:
        logger.info("")
        logger.info("##### STEP: Vector index backfill #####")
        result = run_vector_backfill_step(config, args, base_param_prefix, profile, region, dry_run,
                                          clear_vectors=clear_vectors, state=vector_state)
        outcomes.append(('vectorBackfill', result.outcome))
        if result.rc != 0:
            exit_code = result.rc

    if run_metadata_backfill:
        logger.info("")
        logger.info("##### STEP: Metadata backfill for existing files (vector search off) #####")
        result = run_metadata_backfill_step(config, args, base_param_prefix, profile, region, dry_run,
                                            clear_vectors=clear_vectors, requested=generate_metadata,
                                            state=vector_state)
        outcomes.append(('metadataBackfill', result.outcome))
        if result.rc != 0:
            exit_code = result.rc

    if run_retirement_report:
        logger.info("")
        logger.info("##### STEP: System-pipeline retirement report #####")
        rc = run_system_pipeline_retirement_step(config, args, base_param_prefix, profile, region,
                                                 dry_run)
        _record('systemPipelineRetirement', rc)
        if rc != 0:
            exit_code = rc

    print_run_summary(outcomes, dry_run)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
