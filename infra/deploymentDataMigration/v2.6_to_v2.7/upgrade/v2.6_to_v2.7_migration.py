#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.6 to v2.7 - asset location folder keys, orphaned workflow-trigger
cleanup, vector index backfill, the system-pipeline retirement report, and the asset location index
backfill.

The v2.7 release introduces:
  0. One meaning for an asset location key: a key ending in '/' is a folder, any other key names one
     Amazon S3 object (a single-file asset). Before v2.7 a folder key written without the '/' still
     listed its files. The ``assetLocationFolderKeys`` step finds asset rows whose key has no trailing
     '/', no object at the key and objects under ``{key}/``, reports them, and on a real run appends the
     '/'. It runs first, before the backfills that walk every asset's location; it saves its scan
     position after every page (``--resume``) and writes one report line per finding.
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
  4. A location index on the asset table (``AssetLocationGSI``: bucket name and stored location to
     database and asset). The ``assetLocationIndex`` step writes the two indexed attributes,
     ``locationBucket`` and ``locationKey``, on every asset row that lacks them (one conditional
     ``UpdateItem`` each, paced, resumable from a checkpoint file), verifies every row with strongly
     consistent passes, writes a report of the rows it could not index, and writes the completion
     marker (an SSM parameter under the resource-name prefix) last. Nothing reads the index before the
     marker exists. No S3 call is made.
  5. Comments and subscriptions stored by database and asset id. The v2.7 deploy adds the V2 comment and
     subscription tables and keeps the v2.6 tables, keyed by the asset id alone, as sources. The
     ``commentSubscriptionKeys`` step copies every v2.6 row into the key of the asset it belongs to: the one
     database holding an asset with that id, or, when several do, the one the evidence names (the comment's
     asset version exists there and predates the comment; each subscriber goes to the database whose asset
     topic it is on), or the one an operator mapping file names. A comment or subscriber it cannot place is
     reported and left where it is; no v2.6 row is ever written or deleted. Each copy is recorded with it,
     so a repeat copies only what earlier runs left and never undoes what users changed since. Required
     right after the deploy on a deployment with comments or subscriptions; resumable.

Configuration: set ``resource_names_ssm_param_prefix`` (from the core stack output
``ResourceNamesSSMParamPrefixOutput``); every table and function name is then resolved from SSM
Parameter Store. Explicit ``*_table_name`` / ``vector_reindexer_function_name`` values remain
supported as optional overrides.

Safety: every run is a DRY RUN unless ``--execute`` is given and the config's ``dry_run`` is false (the
shipped config sets it true). A real run that changes asset location keys, deletes trigger rows, deletes vectors,
or launches
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
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
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
_S3_CLIENT_CONFIG = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
_SSM_CLIENT_CONFIG = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
_ASSET_LOCATION_DESERIALIZER = TypeDeserializer()
_COMMENT_SUBSCRIPTION_SERIALIZER = TypeSerializer()
_SNS_CLIENT_CONFIG = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
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
# STEP: assetLocationFolderKeys
#######################

# An asset location key ending in '/' is a folder; any other key names one Amazon S3 object (a
# single-file asset). Before v2.7 a folder key written without the trailing '/' still listed its files,
# and v2.7 reads such a key as a single object. This step finds the asset rows whose key has no
# trailing '/', no object at the key and objects under '{key}/', reports them and, on a real run,
# appends the '/' (part a). It reads one object (HeadObject) and, when there is none, the newest version
# of the key and at most one listed key under '{key}/' (MaxKeys=1) per such row; it never walks a bucket.
#
# Part (b): before v2.7 the companion copy '{key}/{name}' of a single-file location had the asset-relative
# path '/{name}', and the records that describe a file are keyed by that path. v2.7 lists the object at
# '/{name}' and the copy at '/{name}/{name}', so for each single-file location whose copy exists the copy's
# file metadata, file attributes, version snapshot entries, metadata version snapshots and file version
# history move to '/{name}/{name}'. A version snapshot entry moves only when its S3 version id is one of
# the copy's; a history record stays when its version id is one of the object's. Vectors (a reindex rewrites
# them) and execution history (what past runs read) stay as recorded. A record already at the new path is
# never overwritten: the clash is reported and the old record stays.
LOCATION_STEP = 'assetLocationFolderKeys'
LOCATION_CLASSES = ('folder', 'single-file', 'folder-without-slash', 'no-objects', 'unreadable')
LOCATION_PAGE_SIZE = 100
LOCATION_PACE_SECONDS = 0.05
_NOT_FOUND_CODES = ('404', 'NoSuchKey', 'NotFound')
COPY_COUNTERS = ('copy-locations', 'copy-already-moved', 'records-moved', 'records-clash',
                 'records-left-object', 'records-left-unknown-version', 'records-gone')
# (config key, ResourceParamKeys name) of the tables part (b) moves records in; the legacy V1 file
# versions table is optional and skipped when the deployment does not publish it.
COPY_TABLE_KEYS = (
    ('asset_file_metadata_storage_table_name', 'ASSET_FILE_METADATA_STORAGE_TABLE'),
    ('file_attribute_storage_table_name', 'FILE_ATTRIBUTE_STORAGE_TABLE'),
    ('asset_versions_storage_table_name', 'ASSET_VERSIONS_STORAGE_TABLE'),
    ('asset_file_versions_storage_table_name', 'ASSET_FILE_VERSIONS_STORAGE_TABLE'),
    ('asset_file_metadata_versions_storage_table_name', 'ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE'),
    ('asset_file_version_history_storage_table_name', 'ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE'),
)
LEGACY_COPY_TABLE_KEY = ('legacy_asset_file_versions_storage_table_name', 'LEGACY_ASSET_FILE_VERSIONS_STORAGE_TABLE_V1')
_FILE_PATH_INDEX = 'DatabaseIdAssetIdFilePathIndex'
# Set on an asset row once part (b) finished for it; a row carrying it is skipped by every later run.
COPY_DONE_ATTRIBUTE = 'assetLocationCopyRecordsMovedAt'
_VERSIONS_BY_ASSET_INDEX = 'databaseIdAssetIdIndex'
_HISTORY_VERSION_ID_PREFIXES = ('unarchive:',)


class LocationFinding(NamedTuple):
    database_id: str
    asset_id: str
    bucket: str
    key: str
    location_class: str
    detail: str


def _location_key(row: Dict) -> str:
    """The wire-format row's assetLocation.Key, or ''."""
    return ((row.get('assetLocation') or {}).get('M') or {}).get('Key', {}).get('S', '') or ''


def classify_asset_location(s3_client, bucket: str, key: str) -> Tuple[str, str]:
    """Return (class, detail) for one asset location key. Reads nothing for a key ending in '/'."""
    if not key or key.endswith('/'):
        return 'folder', ''
    if not bucket:
        return 'unreadable', 'the asset bucket is not registered in the S3 asset buckets table'
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return 'single-file', ''
    except ClientError as e:
        code = str(e.response.get('Error', {}).get('Code', ''))
        if code not in _NOT_FOUND_CODES:
            return 'unreadable', f"HeadObject: {code or e}"
    # An archived object answers HeadObject with 404 (its newest version is a delete marker) and is still
    # the asset's file, so a key with any version of its own is a single file, never a folder.
    try:
        if exact_key_versions(s3_client, bucket, key, first_page_only=True):
            return 'single-file', 'archived'
    except ClientError as e:
        return 'unreadable', f"ListObjectVersions: {e.response.get('Error', {}).get('Code', '') or e}"
    try:
        listed = s3_client.list_objects_v2(Bucket=bucket, Prefix=f"{key}/", MaxKeys=1)
    except ClientError as e:
        return 'unreadable', f"ListObjectsV2: {e.response.get('Error', {}).get('Code', '') or e}"
    if listed.get('KeyCount', 0) > 0 or listed.get('Contents'):
        return 'folder-without-slash', ''
    return 'no-objects', ''


def exact_key_versions(s3_client, bucket: str, key: str, first_page_only: bool = False) -> set:
    """The version ids, delete markers included, of exactly ``key``. The listing names ``key`` as its prefix,
    so it starts at ``key`` (the shortest key under it) and stops at the first other key."""
    ids = set()
    kwargs = {'Bucket': bucket, 'Prefix': key, 'MaxKeys': 1 if first_page_only else 1000}
    while True:
        page = s3_client.list_object_versions(**kwargs)
        entries = page.get('Versions', []) + page.get('DeleteMarkers', [])
        ids.update(entry['VersionId'] for entry in entries if entry['Key'] == key)
        if first_page_only or any(entry['Key'] != key for entry in entries) or not page.get('IsTruncated'):
            return ids
        kwargs['KeyMarker'] = page.get('NextKeyMarker')
        kwargs['VersionIdMarker'] = page.get('NextVersionIdMarker')


def append_location_slash(dynamodb_client, asset_table: str, row: Dict, key: str) -> str:
    """Append '/' to the row's assetLocation.Key. The write is conditional on the key still being
    ``key``, so a repeat or a concurrent edit changes nothing. Returns 'appended' or 'changed'."""
    try:
        dynamodb_client.update_item(
            TableName=asset_table,
            Key={'databaseId': row['databaseId'], 'assetId': row['assetId']},
            UpdateExpression='SET #loc.#key = :new',
            ConditionExpression='#loc.#key = :old',
            ExpressionAttributeNames={'#loc': 'assetLocation', '#key': 'Key'},
            ExpressionAttributeValues={':new': {'S': f"{key}/"}, ':old': {'S': key}},
        )
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            return 'changed'
        raise
    # A row the assetLocationIndex step already indexed keeps its locationKey in step with the key.
    try:
        dynamodb_client.update_item(
            TableName=asset_table,
            Key={'databaseId': row['databaseId'], 'assetId': row['assetId']},
            UpdateExpression='SET #lk = :lk',
            ConditionExpression='attribute_exists(#lk) AND #loc.#key = :new',
            ExpressionAttributeNames={'#lk': ASSET_LOCATION_KEY_ATTRIBUTE, '#loc': 'assetLocation', '#key': 'Key'},
            ExpressionAttributeValues={':lk': {'S': f"{key.lstrip('/')}/"}, ':new': {'S': f"{key}/"}},
        )
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
    return 'appended'


def _location_files(output_dir: str, dry_run: bool) -> Tuple[str, str]:
    """The report (JSON Lines) and checkpoint paths. A dry run and a real run keep separate files."""
    mode = 'dry-run' if dry_run else 'execute'
    return (os.path.join(output_dir, f"{LOCATION_STEP}-{mode}-report.jsonl"),
            os.path.join(output_dir, f"{LOCATION_STEP}-{mode}-checkpoint.json"))


def _read_checkpoint(path: str, asset_table: str) -> Optional[Dict]:
    """The saved scan position, or None. A checkpoint of another table is refused."""
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        checkpoint = json.load(f)
    if checkpoint.get('assetTable') != asset_table:
        raise ValueError(f"The checkpoint {path} belongs to table '{checkpoint.get('assetTable')}', not "
                         f"'{asset_table}'. Remove it or point the step at its table.")
    return checkpoint


def _write_checkpoint(path: str, asset_table: str, start_key: Optional[Dict], counts: Dict[str, int]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'assetTable': asset_table, 'exclusiveStartKey': start_key, 'counts': counts}, f)
    os.replace(tmp, path)


def _query_all(dynamodb_client, table: str, key_name: str, value: str, index: Optional[str] = None) -> List[Dict]:
    kwargs = {'TableName': table, 'KeyConditionExpression': '#p = :v',
              'ExpressionAttributeNames': {'#p': key_name}, 'ExpressionAttributeValues': {':v': {'S': value}}}
    if index:
        kwargs['IndexName'] = index
    return list(_paginate(dynamodb_client.query, **kwargs))


def _version_owner(version_id: str, copy_versions: set, object_versions: set) -> str:
    """'copy' or 'object' when the S3 version id belongs to exactly one of the two keys, else 'unknown'."""
    for prefix in _HISTORY_VERSION_ID_PREFIXES:
        if version_id.startswith(prefix):
            version_id = version_id[len(prefix):]
    in_copy, in_object = version_id in copy_versions, version_id in object_versions
    if in_copy and not in_object:
        return 'copy'
    if in_object and not in_copy:
        return 'object'
    return 'unknown'


def move_record(dynamodb_client, table: str, item: Dict, key_names: Tuple[str, ...], new_item: Dict,
                dry_run: bool) -> str:
    """Move one record to its new key: the put is conditional on no record at the new key and the delete on
    the old record still being there, in one transaction. Returns 'moved' (or 'would move' on a dry run),
    'clash' when a record is already at the new key (nothing changes), or 'gone' when the old record was
    removed meanwhile."""
    target_key = {name: new_item[name] for name in key_names}
    names = {'#k': key_names[0]}
    if dry_run:
        found = dynamodb_client.get_item(TableName=table, Key=target_key)
        return 'clash' if 'Item' in found else 'would move'
    try:
        dynamodb_client.transact_write_items(TransactItems=[
            {'Put': {'TableName': table, 'Item': new_item, 'ConditionExpression': 'attribute_not_exists(#k)',
                     'ExpressionAttributeNames': names}},
            {'Delete': {'TableName': table, 'Key': {name: item[name] for name in key_names},
                        'ConditionExpression': 'attribute_exists(#k)', 'ExpressionAttributeNames': names}},
        ])
        return 'moved'
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') != 'TransactionCanceledException':
            raise
    return 'clash' if 'Item' in dynamodb_client.get_item(TableName=table, Key=target_key) else 'gone'


def mark_copy_records_done(dynamodb_client, asset_table: str, row: Dict) -> str:
    """Record on the asset row that part (b) finished for it. Conditional on the row existing and not being
    marked yet; returns 'marked' or 'already marked'. The marker travels with the row (asset archive and
    unarchive write the whole row back) and is not part of any API response model."""
    try:
        dynamodb_client.update_item(
            TableName=asset_table,
            Key={'databaseId': row['databaseId'], 'assetId': row['assetId']},
            UpdateExpression='SET #m = :now',
            ConditionExpression='attribute_exists(assetId) AND attribute_not_exists(#m)',
            ExpressionAttributeNames={'#m': COPY_DONE_ATTRIBUTE},
            ExpressionAttributeValues={':now': {'S': datetime.now(timezone.utc).isoformat()}},
        )
        return 'marked'
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            return 'already marked'
        raise


def move_companion_copy_records(dynamodb_client, s3_client, cfg: Dict, row: Dict, bucket: str, key: str,
                                dry_run: bool, counts: Dict[str, int], report: Callable[[Dict], None]) -> None:
    """Part (b) for one single-file location: move the records of its copy '{key}/{name}' from '/{name}' to
    '/{name}/{name}', then, on a real run without errors, mark the asset row done (with or without a copy) so
    no later run, from any directory, reads or moves its records again. A marked row costs no read."""
    database_id = _attr_s(row, 'databaseId')
    database_id = database_id[:-len('#deleted')] if database_id.endswith('#deleted') else database_id
    asset_id = _attr_s(row, 'assetId')
    asset = f"{database_id}:{asset_id}"
    base = {'part': 'companion-copy', 'database_id': database_id, 'asset_id': asset_id, 'bucket': bucket,
            'key': key}
    if _attr_s(row, COPY_DONE_ATTRIBUTE):
        counts['copy-already-moved'] += 1
        return
    errors_before = counts['errors']

    def finish() -> None:
        if dry_run or counts['errors'] != errors_before:
            return
        try:
            mark_copy_records_done(dynamodb_client, cfg['asset_storage_table_name'], row)
        except ClientError as e:
            counts['errors'] += 1
            report(dict(base, table=None, action='failed to mark the asset done', detail=str(e)))

    name = key.rsplit('/', 1)[-1]
    copy_versions = exact_key_versions(s3_client, bucket, f"{key}/{name}")
    if not copy_versions:
        finish()
        return
    counts['copy-locations'] += 1
    object_versions = exact_key_versions(s3_client, bucket, key)
    sources = (f"/{name}", name)

    def target(path: str) -> str:
        return f"{path}/{name}"

    def move(label: str, table: str, item: Dict, key_names: Tuple[str, ...], changes: Dict[str, str],
             source: str) -> None:
        new_item = dict(item)
        new_item.update({attr: {'S': value} for attr, value in changes.items()})
        try:
            outcome = move_record(dynamodb_client, table, item, key_names, new_item, dry_run)
        except ClientError as e:
            counts['errors'] += 1
            report(dict(base, table=label, **{'from': source, 'to': target(source)}, action='failed', detail=str(e)))
            return
        counts[{'moved': 'records-moved', 'would move': 'records-moved', 'clash': 'records-clash',
                'gone': 'records-gone'}[outcome]] += 1
        report(dict(base, table=label, **{'from': source, 'to': target(source)}, action=outcome))

    def leave(label: str, source: str, owner: str) -> None:
        counts['records-left-object' if owner == 'object' else 'records-left-unknown-version'] += 1
        report(dict(base, table=label, **{'from': source}, action=f"left: the version is the {owner}'s"
                    if owner == 'object' else 'left: the version id matches neither file'))

    def path_changes(item: Dict, composite_name: str, source: str) -> Dict[str, str]:
        changes = {composite_name: f"{asset}:{target(source)}"}
        if _attr_s(item, 'filePath') == source:
            changes['filePath'] = target(source)
        return changes

    for label, table_key, item_key in (('file metadata', 'asset_file_metadata_storage_table_name', 'metadataKey'),
                                       ('file attributes', 'file_attribute_storage_table_name', 'attributeKey')):
        table = cfg.get(table_key)
        if not table:
            continue
        for source in sources:
            for item in _query_all(dynamodb_client, table, 'databaseId:assetId:filePath', f"{asset}:{source}",
                                   _FILE_PATH_INDEX):
                move(label, table, item, (item_key, 'databaseId:assetId:filePath'),
                     path_changes(item, 'databaseId:assetId:filePath', source), source)

    # Version snapshot entries move when their S3 version is the copy's; the decision per asset version
    # also decides that version's metadata snapshot rows.
    decisions: Dict[str, str] = {}
    table = cfg.get('asset_file_versions_storage_table_name')
    if table:
        for item in _query_all(dynamodb_client, table, 'databaseId:assetId', asset, _VERSIONS_BY_ASSET_INDEX):
            file_key = _attr_s(item, 'fileKey')
            if file_key not in sources:
                continue
            version = _attr_s(item, 'databaseId:assetId:assetVersionId')[len(asset) + 1:]
            owner = _version_owner(_attr_s(item, 'versionId'), copy_versions, object_versions)
            decisions[version] = owner
            if owner == 'copy':
                move('asset version files', table, item, ('databaseId:assetId:assetVersionId', 'fileKey'),
                     {'fileKey': target(file_key)}, file_key)
            else:
                leave('asset version files', file_key, owner)
    legacy = cfg.get('legacy_asset_file_versions_storage_table_name')
    versions_table = cfg.get('asset_versions_storage_table_name')
    if legacy and versions_table:
        for version_row in _query_all(dynamodb_client, versions_table, 'databaseId:assetId', asset):
            version = _attr_s(version_row, 'assetVersionId')
            for file_key in sources:
                item = dynamodb_client.get_item(TableName=legacy, Key={
                    'assetId:assetVersionId': {'S': f"{asset_id}:{version}"}, 'fileKey': {'S': file_key}}).get('Item')
                if not item:
                    continue
                owner = _version_owner(_attr_s(item, 'versionId'), copy_versions, object_versions)
                decisions.setdefault(version, owner)
                if owner == 'copy':
                    move('asset version files (legacy)', legacy, item, ('assetId:assetVersionId', 'fileKey'),
                         {'fileKey': target(file_key)}, file_key)
                else:
                    leave('asset version files (legacy)', file_key, owner)
    table = cfg.get('asset_file_metadata_versions_storage_table_name')
    if table:
        sort_name = 'type:filePath:metadataKey'
        for item in _query_all(dynamodb_client, table, 'databaseId:assetId', asset, _VERSIONS_BY_ASSET_INDEX):
            kind, _, rest = _attr_s(item, sort_name).partition(':')
            source = next((path for path in sources if rest.startswith(path + ':')), None)
            if source is None:
                continue
            metadata_key = rest[len(source) + 1:]
            owner = decisions.get(_attr_s(item, 'databaseId:assetId:assetVersionId')[len(asset) + 1:], 'copy')
            if owner == 'copy':
                move('metadata versions', table, item, ('databaseId:assetId:assetVersionId', sort_name),
                     {sort_name: f"{kind}:{target(source)}:{metadata_key}"}, source)
            else:
                leave('metadata versions', source, owner)
    # History records describe the copy unless their version is the object's (written after the upgrade).
    table = cfg.get('asset_file_version_history_storage_table_name')
    if table:
        for source in sources:
            for item in _query_all(dynamodb_client, table, 'databaseId:assetId:filePath', f"{asset}:{source}"):
                owner = _version_owner(_attr_s(item, 'versionId'), copy_versions, object_versions)
                if owner == 'object':
                    leave('file version history', source, owner)
                    continue
                move('file version history', table, item, ('databaseId:assetId:filePath', 'versionId'),
                     path_changes(item, 'databaseId:assetId:filePath', source), source)

    finish()


def fix_asset_location_folder_keys(dynamodb_client, s3_client, cfg: Dict, dry_run: bool,
                                   limit: Optional[int] = None, resume: bool = False,
                                   pace_seconds: float = LOCATION_PACE_SECONDS,
                                   page_size: int = LOCATION_PAGE_SIZE) -> Dict[str, int]:
    """Scan every asset row (archived rows included), classify each key without a trailing '/', and
    append the '/' to each 'folder-without-slash' key on a real run. Each finding is appended to the
    report; after every page the scan position is saved, so ``resume`` continues where a stopped run
    ended. A finished run removes its checkpoint. A dry run counts what a real run would change."""
    asset_table = cfg['asset_storage_table_name']
    report_path, checkpoint_path = _location_files(cfg['output_dir'], dry_run)
    os.makedirs(cfg['output_dir'], exist_ok=True)
    counts = {'scanned': 0, 'appended': 0, 'changed': 0, 'errors': 0}
    counts.update({c: 0 for c in LOCATION_CLASSES})
    counts.update({c: 0 for c in COPY_COUNTERS})

    start_key = None
    checkpoint = _read_checkpoint(checkpoint_path, asset_table) if resume else None
    if checkpoint:
        start_key = checkpoint.get('exclusiveStartKey')
        counts.update(checkpoint.get('counts') or {})
        logger.info(f"Resuming from {checkpoint_path} after {counts['scanned']} row(s).")
    elif resume:
        logger.info(f"No checkpoint at {checkpoint_path}; starting from the first row.")
    report_mode = 'a' if checkpoint else 'w'

    buckets = {}
    for item in _paginate(dynamodb_client.scan, TableName=cfg['s3_asset_buckets_storage_table_name'],
                          ProjectionExpression='bucketId, bucketName'):
        if _attr_s(item, 'bucketId') and _attr_s(item, 'bucketName'):
            buckets[_attr_s(item, 'bucketId')] = _attr_s(item, 'bucketName')

    with open(report_path, report_mode, encoding='utf-8') as report:
        def write_report(record: Dict) -> None:
            report.write(json.dumps(record) + '\n')
            report.flush()
            if record.get('table'):
                logger.info(f"  {'[DRY RUN] ' if dry_run else ''}{record['database_id']} / {record['asset_id']}: "
                            f"{record['table']} {record.get('from')} -> {record.get('to', '')} ({record['action']})")

        while True:
            scan_kwargs = {'TableName': asset_table, 'Limit': page_size,
                           'ProjectionExpression': ('databaseId, assetId, bucketId, assetLocation, '
                                                    f'{COPY_DONE_ATTRIBUTE}')}
            if start_key:
                scan_kwargs['ExclusiveStartKey'] = start_key
            response = dynamodb_client.scan(**scan_kwargs)
            for row in response.get('Items', []):
                if limit is not None and counts['scanned'] >= limit:
                    logger.info(f"Limit of {limit} asset row(s) reached; stopping the scan.")
                    return counts
                counts['scanned'] += 1
                key = _location_key(row)
                bucket = buckets.get(_attr_s(row, 'bucketId'), '')
                location_class, detail = classify_asset_location(s3_client, bucket, key)
                counts[location_class] += 1
                if location_class == 'folder':
                    continue
                if pace_seconds:
                    time.sleep(pace_seconds)
                if location_class == 'single-file':
                    try:
                        move_companion_copy_records(dynamodb_client, s3_client, cfg, row, bucket, key, dry_run,
                                                    counts, write_report)
                    except ClientError as e:
                        counts['errors'] += 1
                        write_report({'part': 'companion-copy', 'database_id': _attr_s(row, 'databaseId'),
                                      'asset_id': _attr_s(row, 'assetId'), 'bucket': bucket, 'key': key,
                                      'table': None, 'action': 'failed', 'detail': str(e)})
                    continue
                action = 'report only'
                if location_class == 'folder-without-slash':
                    if dry_run:
                        action = 'would append /'
                        counts['appended'] += 1
                    else:
                        try:
                            outcome = append_location_slash(dynamodb_client, asset_table, row, key)
                            counts[outcome] += 1
                            action = 'appended /' if outcome == 'appended' else 'skipped: the key changed'
                        except ClientError as e:
                            counts['errors'] += 1
                            action = 'failed'
                            detail = str(e)
                finding = LocationFinding(_attr_s(row, 'databaseId'), _attr_s(row, 'assetId'), bucket, key,
                                          location_class, detail)
                record = dict(finding._asdict(), action=action)
                report.write(json.dumps(record) + '\n')
                report.flush()
                logger.info(f"  {'[DRY RUN] ' if dry_run else ''}{record['database_id']} / {record['asset_id']}: "
                            f"'{key}' is {location_class} ({action}){f' - {detail}' if detail else ''}")
            start_key = response.get('LastEvaluatedKey')
            if not start_key:
                break
            _write_checkpoint(checkpoint_path, asset_table, start_key, counts)
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)
    return counts


def run_asset_location_folder_keys_step(config: dict, args, base_param_prefix, profile, region,
                                        dry_run) -> int:
    """Append the trailing '/' to asset location keys that name a folder. Returns 0 on success."""
    try:
        resolve = make_resolver(config, base_param_prefix, profile, region)
        cfg = {
            'asset_storage_table_name': resolve(
                'asset_storage_table_name', ResourceParamKeys.ASSET_STORAGE_TABLE),
            's3_asset_buckets_storage_table_name': resolve(
                's3_asset_buckets_storage_table_name', ResourceParamKeys.S3_ASSET_BUCKETS_STORAGE_TABLE),
            'output_dir': config.get('asset_location_output_dir') or os.getcwd(),
        }
        for cfg_key, param_name in COPY_TABLE_KEYS:
            cfg[cfg_key] = resolve(cfg_key, getattr(ResourceParamKeys, param_name))
    except Exception as e:
        logger.error(f"Failed resolving table names for the {LOCATION_STEP} step: {e}")
        return 1
    try:
        cfg[LEGACY_COPY_TABLE_KEY[0]] = resolve(LEGACY_COPY_TABLE_KEY[0],
                                                getattr(ResourceParamKeys, LEGACY_COPY_TABLE_KEY[1]))
    except Exception:
        cfg[LEGACY_COPY_TABLE_KEY[0]] = None
        logger.info("No legacy asset file versions table is published for this deployment; part (b) skips it.")

    limit = args.limit if args.limit is not None else config.get('limit')
    pace = config.get('asset_location_pace_seconds')
    pace = LOCATION_PACE_SECONDS if pace is None else float(pace)
    session = _session(profile, region)
    dynamodb_client = session.client('dynamodb', config=_DYNAMODB_CLIENT_CONFIG)
    s3_client = session.client('s3', config=_S3_CLIENT_CONFIG)
    report_path, _ = _location_files(cfg['output_dir'], dry_run)

    logger.info("=" * 80)
    logger.info("VAMS v2.6 -> v2.7 ASSET LOCATION FOLDER KEYS")
    logger.info(f"Asset table:  {cfg['asset_storage_table_name']}")
    logger.info(f"Report:       {report_path}")
    logger.info(f"Dry Run: {dry_run}   Limit: {limit}   Resume: {args.resume}   Pace: {pace}s")
    logger.info("=" * 80)

    start = datetime.now(timezone.utc)
    try:
        counts = fix_asset_location_folder_keys(dynamodb_client, s3_client, cfg, dry_run, limit,
                                                resume=args.resume, pace_seconds=pace)
    except Exception as e:
        logger.error(f"{LOCATION_STEP} step failed: {e}. Re-run with --resume to continue from the last "
                     "saved page.")
        return 1
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info("=" * 80)
    logger.info("ASSET LOCATION FOLDER KEYS SUMMARY")
    logger.info(f"  Duration: {duration:.1f}s   Dry Run: {dry_run}")
    logger.info(f"  Asset rows examined:                  {counts['scanned']}")
    logger.info(f"  Folder keys (end in '/'):             {counts['folder']}")
    logger.info(f"  Single-file keys (an object):         {counts['single-file']}")
    logger.info(f"  Folder keys without the '/':          {counts['folder-without-slash']}")
    logger.info(f"    '/' {'that would be ' if dry_run else ''}appended:            {counts['appended']}")
    logger.info(f"    skipped, the key changed meanwhile: {counts['changed']}")
    logger.info(f"  Keys with no objects (reported):      {counts['no-objects']}")
    logger.info(f"  Keys that could not be read:          {counts['unreadable']}")
    logger.info(f"  Single-file keys with a copy named like the file: {counts['copy-locations']}")
    logger.info(f"  Single-file assets already done (marked on the row): {counts['copy-already-moved']}")
    moved_label = 'that would be moved' if dry_run else 'moved'
    logger.info(f"    records {moved_label} to /{{name}}/{{name}}: {counts['records-moved']}")
    logger.info(f"    records left, a record is already at the new path: {counts['records-clash']}")
    logger.info(f"    records left, their version is the file's own:     {counts['records-left-object']}")
    logger.info(f"    records left, their version matches neither file:  {counts['records-left-unknown-version']}")
    logger.info(f"    records removed meanwhile:            {counts['records-gone']}")
    logger.info(f"  Errors:                               {counts['errors']}")
    if counts['records-moved'] and not dry_run:
        logger.info("  Run the vectorBackfill step afterwards (it runs next under --steps all) so the moved copy's")
        logger.info("  search vectors are rewritten for its new path.")
    logger.info("=" * 80)

    return 0 if counts['errors'] == 0 and counts['unreadable'] == 0 else 1


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
# STEP: assetLocationIndex
#######################

# Mirrors backend/backend/common/assetLocations.py: the two index attributes on every asset row, and
# the completion marker the backend's index_ready() reads (pinned by
# infra/test/platform/migrationTooling.test.ts).
ASSET_LOCATION_BUCKET_ATTRIBUTE = 'locationBucket'
ASSET_LOCATION_KEY_ATTRIBUTE = 'locationKey'
ASSET_LOCATION_INDEX_MARKER_SUFFIX = 'dataMigrations/assetLocationIndex'
ASSET_LOCATION_INDEX_MARKER_VERSION = 1
ASSET_LOCATION_INDEX_STATUS_COMPLETE = 'complete'
# common.s3PathPatterns.PREVIEW_FILE_PATTERN: a single-file location also holds `{key}.previewFile.{ext}`.
ASSET_LOCATION_PREVIEW_FILE_PATTERN = '.previewFile.'
ARCHIVED_ASSET_DATABASE_SUFFIX = '#deleted'

ASSET_LOCATION_CHECKPOINT_VERSION = 1
ASSET_LOCATION_INDEX_SKIPPED_AFTER_FOLDER_KEYS = 'skipped: assetLocationFolderKeys failed in this run'
ASSET_LOCATION_DEFAULT_CHECKPOINT_FILE = 'asset_location_index_checkpoint.json'
ASSET_LOCATION_DEFAULT_REPORT_FILE = 'asset_location_index_report.json'
ASSET_LOCATION_DEFAULT_WRITES_PER_SECOND = 25
ASSET_LOCATION_SCAN_PAGE_SIZE = 500
# Strongly consistent verification passes before the marker is written; each pass after the first
# repairs only rows that changed under the previous one.
ASSET_LOCATION_MAX_VERIFY_PASSES = 3
ASSET_LOCATION_WRITE_ATTEMPTS = 5
_THROTTLING_ERRORS = ('ProvisionedThroughputExceededException', 'ThrottlingException',
                      'RequestLimitExceeded', 'InternalServerError')

# Rows the step leaves out of the index, by reason. Their locations hold no key the index could
# attribute: no bucket, no registration to name the bucket, or no location (the bucket root `/` is
# stored as an empty location, which holds no key, as in common.assetLocations.contains).
NOT_INDEXED_NO_BUCKET_ID = 'noBucketId'
NOT_INDEXED_REGISTRATION_MISSING = 'registrationMissing'
NOT_INDEXED_NO_LOCATION = 'noLocation'
NOT_INDEXED_BUCKET_ROOT = 'bucketRoot'
NOT_INDEXED_REASONS = (NOT_INDEXED_NO_BUCKET_ID, NOT_INDEXED_REGISTRATION_MISSING, NOT_INDEXED_NO_LOCATION,
                       NOT_INDEXED_BUCKET_ROOT)
# Rows the step indexes and reports for review.
REVIEW_SHARED_LOCATION = 'sharedLocation'
REVIEW_NESTED_LOCATION = 'nestedLocation'
REVIEW_SINGLE_OBJECT_LOCATION = 'singleObjectLocation'
REVIEW_OUTSIDE_REGISTRATION_PREFIX = 'outsideRegistrationPrefix'
# The location is the registration's own prefix, which create refuses since v2.6.1. Such a row holds
# every key under the prefix, so it is indexed: an ownership check must still see it.
REVIEW_REGISTRATION_ROOT = 'registrationRoot'
REVIEW_REASONS = (REVIEW_SHARED_LOCATION, REVIEW_NESTED_LOCATION, REVIEW_SINGLE_OBJECT_LOCATION,
                  REVIEW_OUTSIDE_REGISTRATION_PREFIX, REVIEW_REGISTRATION_ROOT)

_ASSET_LOCATION_PROJECTION = '#db, #a, #bid, #al, #lb, #lk'
_ASSET_LOCATION_NAMES = {'#db': 'databaseId', '#a': 'assetId', '#bid': 'bucketId', '#al': 'assetLocation',
                         '#lb': ASSET_LOCATION_BUCKET_ATTRIBUTE, '#lk': ASSET_LOCATION_KEY_ATTRIBUTE}


def asset_location_marker_name(base_param_prefix: Optional[str]) -> Optional[str]:
    """The completion marker's SSM name: under the resource-name prefix, where the backend reads it."""
    if not base_param_prefix:
        return None
    return f"{base_param_prefix.rstrip('/')}/{ASSET_LOCATION_INDEX_MARKER_SUFFIX}"


def normalize_location(key: Optional[str]) -> str:
    """A location as the index stores it: no leading `/` (common.assetLocations.normalize_location)."""
    return key.lstrip('/') if key else ''


def normalize_base_prefix(base_assets_prefix: Optional[str]) -> str:
    """A registration prefix with a trailing `/` and no leading `/` ('' is the bucket root), as
    common.assetLocations.normalize_base_prefix."""
    if not base_assets_prefix:
        return ''
    if not base_assets_prefix.endswith('/'):
        base_assets_prefix += '/'
    if base_assets_prefix.startswith('/'):
        base_assets_prefix = base_assets_prefix[1:]
    return base_assets_prefix


def _is_preview_of(base_key: str, object_key: str) -> bool:
    """`object_key` is `{base_key}.previewFile.{ext}` with no `/` in the extension
    (common.assetLocations._is_preview_of)."""
    stem = base_key + ASSET_LOCATION_PREVIEW_FILE_PATTERN
    if not object_key.startswith(stem):
        return False
    extension = object_key[len(stem):]
    return bool(extension) and '/' not in extension


def _location_key_sets(location: str) -> List[Tuple[str, str]]:
    """The key sets a non-empty location holds (common.assetLocations._held_sets): a folder is one prefix;
    a single file is its key, its companion folder `{key}/` and its preview stem `{key}.previewFile.`."""
    if location.endswith('/'):
        return [('prefix', location)]
    return [('key', location), ('prefix', location + '/'), ('previews', location + ASSET_LOCATION_PREVIEW_FILE_PATTERN)]


def _key_set_holds(held: Tuple[str, str], key: str) -> bool:
    kind, text = held
    if kind == 'key':
        return key == text
    if not key.startswith(text):
        return False
    rest = key[len(text):]
    return kind == 'prefix' or (bool(rest) and '/' not in rest)


def _key_sets_meet(first: Tuple[str, str], second: Tuple[str, str]) -> bool:
    if first[0] == 'key':
        return _key_set_holds(second, first[1])
    if second[0] == 'key':
        return _key_set_holds(first, second[1])
    (shorter_kind, shorter), (_, longer) = sorted((first, second), key=lambda held: len(held[1]))
    if not longer.startswith(shorter):
        return False
    return shorter_kind == 'prefix' or '/' not in longer[len(shorter):]


def locations_share_key(first: Optional[str], second: Optional[str]) -> bool:
    """Whether two locations hold a key in common (common.assetLocations.locations_share_key). This is more
    than one containing the other: `a/x` and `a/x.previewFile` both hold `a/x.previewFile.previewFile.z`."""
    a, b = normalize_location(first), normalize_location(second)
    if not a or not b:
        return False
    return any(_key_sets_meet(x, y) for x in _location_key_sets(a) for y in _location_key_sets(b))


def _preview_partner_below(location: str) -> List[str]:
    """For a single file named `{S}.previewFile` or `{S}.previewFile.`, the single file `S` it shares preview
    keys with though neither contains the other (common.assetLocations._preview_partner_locations)."""
    if not location or location.endswith('/'):
        return []
    stem = ASSET_LOCATION_PREVIEW_FILE_PATTERN[:-1]
    return [location[:-len(suffix)] for suffix in (stem, ASSET_LOCATION_PREVIEW_FILE_PATTERN)
            if location.endswith(suffix) and len(location) > len(suffix)]


def candidate_locations(object_key: str) -> List[str]:
    """The stored locations that could hold a key, deepest first (common.assetLocations.candidate_locations):
    the key, the single file whose companion marker a `.../` key is, an own preview's base, and each
    ancestor folder `X/` with the single file `X`."""
    key = normalize_location(object_key)
    if not key:
        return []
    candidates = {key}
    if key.endswith('/'):
        candidates.add(key[:-1])
    last_segment_start = key.rfind('/', 0, len(key) - 1) + 1
    position = key.find(ASSET_LOCATION_PREVIEW_FILE_PATTERN, last_segment_start)
    while position > 0:
        if _is_preview_of(key[:position], key):
            candidates.add(key[:position])
        position = key.find(ASSET_LOCATION_PREVIEW_FILE_PATTERN, position + 1)
    position = len(key) - 1 if key.endswith('/') else len(key)
    while True:
        position = key.rfind('/', 0, position)
        if position <= 0:
            break
        candidates.add(key[:position + 1])
        candidates.add(key[:position])
    candidates.discard('')
    return sorted(candidates, key=lambda c: (-len(c), c))


def _plain_attr(item: Dict, name: str):
    value = (item or {}).get(name)
    if value is None:
        return None
    return _ASSET_LOCATION_DESERIALIZER.deserialize(value)


def plan_asset_location(row: Dict, registration: Optional[Dict]) -> Tuple[Optional[Dict[str, str]], Optional[str], List[str]]:
    """What the step writes for one asset row (wire format): (attributes or None, the reason a row
    is not indexed or None, review notes for an indexed row).

    `registration` is the row's bucket registration (plain attributes) or None when it is gone."""
    location_map = _plain_attr(row, 'assetLocation')
    stored_key = location_map.get('Key') if isinstance(location_map, dict) else None
    if not _plain_attr(row, 'bucketId'):
        return None, NOT_INDEXED_NO_BUCKET_ID, []
    if registration is None or not registration.get('bucketName'):
        return None, NOT_INDEXED_REGISTRATION_MISSING, []
    if not isinstance(stored_key, str) or stored_key == '':
        return None, NOT_INDEXED_NO_LOCATION, []
    location = normalize_location(stored_key)
    if not location:
        return None, NOT_INDEXED_BUCKET_ROOT, []
    prefix = normalize_base_prefix(registration.get('baseAssetsPrefix'))
    folder_form = location if location.endswith('/') else location + '/'
    notes = []
    if prefix and folder_form == prefix:
        notes.append(REVIEW_REGISTRATION_ROOT)
    if not location.endswith('/'):
        notes.append(REVIEW_SINGLE_OBJECT_LOCATION)
    if prefix and not location.startswith(prefix):
        notes.append(REVIEW_OUTSIDE_REGISTRATION_PREFIX)
    return ({ASSET_LOCATION_BUCKET_ATTRIBUTE: registration['bucketName'],
             ASSET_LOCATION_KEY_ATTRIBUTE: location}, None, notes)


def find_location_overlaps(entries: List[Dict]) -> Dict[str, List[Dict]]:
    """Shared and nested locations among the indexed rows, per bucket.

    `sharedLocation`: one location held by two or more assets (`ambiguous` when two of them are live:
    the resolver attributes nothing there). `nestedLocation`: a row whose location shares a key with a
    shorter location of another asset (`locations_share_key`): under its folder, in a single file's
    companion folder `{key}/` or among its own previews `{key}.previewFile.{ext}` (so `x` and `x/` are a
    nested pair too), or a single file `{key}.previewFile` or `{key}.previewFile.`, which shares preview
    keys with `{key}` without either holding the other. Each such pair is listed once, under the longer
    location. Live and archived rows both count, as for an ownership check; the live and archived copies
    of one asset are one asset."""
    by_location: Dict[Tuple[str, str], List[Dict]] = {}
    for entry in entries:
        by_location.setdefault((entry['bucket'], entry['location']), []).append(entry)
    shared = []
    for (bucket, location), rows in sorted(by_location.items()):
        if len({_asset_of(r) for r in rows}) > 1:
            shared.append({'bucket': bucket, 'location': location,
                           'ambiguous': len([r for r in rows if not r['archived']]) > 1,
                           'assets': [dict(_entry_identity(r), archived=r['archived']) for r in rows]})
    nested = []
    for entry in entries:
        location = entry['location']
        shorter = candidate_locations(location) + _preview_partner_below(location)
        owners = [r for candidate in dict.fromkeys(shorter) if candidate != location
                  for r in by_location.get((entry['bucket'], candidate), [])
                  if _asset_of(r) != _asset_of(entry) and locations_share_key(r['location'], location)]
        if owners:
            nested.append({'bucket': entry['bucket'], 'location': location,
                           'asset': dict(_entry_identity(entry), archived=entry['archived']),
                           'within': [dict(_entry_identity(o), archived=o['archived'], location=o['location'])
                                      for o in owners]})
    return {REVIEW_SHARED_LOCATION: shared, REVIEW_NESTED_LOCATION: nested}


def _asset_of(entry: Dict) -> Tuple[str, str]:
    """An asset's identity across its live and archived rows."""
    return entry['databaseId'].removesuffix(ARCHIVED_ASSET_DATABASE_SUFFIX), entry['assetId']


def _entry_identity(entry: Dict) -> Dict:
    return {'databaseId': entry['databaseId'], 'assetId': entry['assetId'], 'bucketId': entry['bucketId']}


class AssetLocationBackfill:
    """Writes `locationBucket` and `locationKey` on every asset row, then the completion marker.

    Pass 1 scans the asset table and writes the rows whose attributes are missing or wrong, paced to
    `writes_per_second`, checkpointing after every page so an interrupted run resumes where it
    stopped. The verification passes then scan strongly consistent, repair any row that changed
    meanwhile, and stop at the first pass that writes nothing; only then are the report and the
    marker written. A row already carrying the right values is never written, so a second run makes
    no write and raises no stream record. No S3 call is made."""

    def __init__(self, dynamodb_client, ssm_client, asset_table: str, buckets_table: str,
                 marker_name: str, checkpoint_path: str, report_path: str, dry_run: bool,
                 writes_per_second: float = ASSET_LOCATION_DEFAULT_WRITES_PER_SECOND,
                 page_size: int = ASSET_LOCATION_SCAN_PAGE_SIZE, limit: Optional[int] = None,
                 max_verify_passes: int = ASSET_LOCATION_MAX_VERIFY_PASSES,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.dynamodb = dynamodb_client
        self.ssm = ssm_client
        self.asset_table = asset_table
        self.buckets_table = buckets_table
        self.marker_name = marker_name
        self.checkpoint_path = checkpoint_path
        self.report_path = report_path
        self.dry_run = dry_run
        self.write_interval = 1.0 / writes_per_second if writes_per_second and writes_per_second > 0 else 0.0
        self.page_size = page_size
        self.limit = limit
        self.max_verify_passes = max_verify_passes
        self.sleep = sleep
        self.clock = clock
        self.now = now
        self._next_write_at = 0.0
        self._registrations: Dict[str, Optional[Dict]] = {}
        self.counts = self._zero_counts()

    @staticmethod
    def _zero_counts() -> Dict[str, int]:
        counts = {'scanned': 0, 'written': 0, 'alreadyIndexed': 0, 'goneDuringRun': 0, 'failed': 0}
        counts.update({reason: 0 for reason in NOT_INDEXED_REASONS})
        return counts

    # ---- checkpoint ---------------------------------------------------------------------------

    def _load_checkpoint(self) -> Optional[Dict]:
        if self.dry_run or not os.path.exists(self.checkpoint_path):
            return None
        with open(self.checkpoint_path, 'r') as f:
            checkpoint = json.load(f)
        if checkpoint.get('version') != ASSET_LOCATION_CHECKPOINT_VERSION:
            raise ValueError(f"Checkpoint {self.checkpoint_path} has an unknown version; delete it to start over")
        if checkpoint.get('assetTable') != self.asset_table:
            raise ValueError(f"Checkpoint {self.checkpoint_path} belongs to table "
                             f"{checkpoint.get('assetTable')}, not {self.asset_table}; delete it or point "
                             "asset_location_checkpoint_file elsewhere")
        return checkpoint

    def _save_checkpoint(self, phase: str, start_key: Optional[Dict]) -> None:
        if self.dry_run:
            return
        checkpoint = {'version': ASSET_LOCATION_CHECKPOINT_VERSION, 'assetTable': self.asset_table,
                      'phase': phase, 'exclusiveStartKey': start_key, 'counts': self.counts,
                      'savedAt': self.now().isoformat()}
        temporary = self.checkpoint_path + '.tmp'
        with open(temporary, 'w') as f:
            json.dump(checkpoint, f)
        os.replace(temporary, self.checkpoint_path)

    def _clear_checkpoint(self) -> None:
        if not self.dry_run and os.path.exists(self.checkpoint_path):
            os.remove(self.checkpoint_path)

    # ---- reads --------------------------------------------------------------------------------

    def _registration(self, bucket_id: str) -> Optional[Dict]:
        if bucket_id not in self._registrations:
            items = list(_paginate(
                self.dynamodb.query, TableName=self.buckets_table,
                KeyConditionExpression='bucketId = :id',
                ExpressionAttributeValues={':id': {'S': bucket_id}}))
            if len(items) > 1:
                logger.warning(f"Bucket registration {bucket_id} has {len(items)} rows; using the first")
            self._registrations[bucket_id] = (
                {name: _ASSET_LOCATION_DESERIALIZER.deserialize(value) for name, value in items[0].items()}
                if items else None)
        return self._registrations[bucket_id]

    def _scan_pages(self, start_key: Optional[Dict], consistent: bool) -> Iterator[Tuple[List[Dict], Optional[Dict]]]:
        kwargs = {'TableName': self.asset_table, 'ProjectionExpression': _ASSET_LOCATION_PROJECTION,
                  'ExpressionAttributeNames': _ASSET_LOCATION_NAMES, 'Limit': self.page_size,
                  'ConsistentRead': consistent}
        if start_key:
            kwargs['ExclusiveStartKey'] = start_key
        while True:
            response = self.dynamodb.scan(**kwargs)
            next_key = response.get('LastEvaluatedKey') if 'LastEvaluatedKey' in response else None
            yield response.get('Items', []), next_key
            if next_key is None:
                return
            kwargs['ExclusiveStartKey'] = next_key

    # ---- writes -------------------------------------------------------------------------------

    def _pace(self) -> None:
        if not self.write_interval:
            return
        now = self.clock()
        if now < self._next_write_at:
            self.sleep(self._next_write_at - now)
            now = self._next_write_at
        self._next_write_at = now + self.write_interval

    def _write(self, row: Dict, attributes: Dict[str, str]) -> str:
        """Write the attributes, conditional on the row still existing at the location it was read
        with. Returns 'written', 'gone' or 'failed'."""
        stored_key = _plain_attr(row, 'assetLocation')['Key']
        request = {
            'TableName': self.asset_table,
            'Key': {'databaseId': row['databaseId'], 'assetId': row['assetId']},
            'UpdateExpression': 'SET #lb = :lb, #lk = :lk',
            'ConditionExpression': 'attribute_exists(#db) AND #al.#k = :key',
            'ExpressionAttributeNames': {'#lb': ASSET_LOCATION_BUCKET_ATTRIBUTE,
                                         '#lk': ASSET_LOCATION_KEY_ATTRIBUTE,
                                         '#db': 'databaseId', '#al': 'assetLocation', '#k': 'Key'},
            'ExpressionAttributeValues': {
                ':lb': {'S': attributes[ASSET_LOCATION_BUCKET_ATTRIBUTE]},
                ':lk': {'S': attributes[ASSET_LOCATION_KEY_ATTRIBUTE]},
                ':key': {'S': stored_key}},
        }
        for attempt in range(1, ASSET_LOCATION_WRITE_ATTEMPTS + 1):
            self._pace()
            try:
                self.dynamodb.update_item(**request)
                return 'written'
            except ClientError as e:
                code = e.response.get('Error', {}).get('Code', '')
                if code == 'ConditionalCheckFailedException':
                    return 'gone'
                if code in _THROTTLING_ERRORS and attempt < ASSET_LOCATION_WRITE_ATTEMPTS:
                    self.sleep(min(2 ** attempt, 30))
                    continue
                logger.error(f"  failed writing the location of {_attr_s(row, 'databaseId')}/"
                             f"{_attr_s(row, 'assetId')}: {code or e}")
                return 'failed'
        return 'failed'

    def _process_row(self, row: Dict, entries: Optional[List[Dict]], notes: Optional[Dict[str, List[Dict]]],
                     not_indexed: Optional[Dict[str, List[Dict]]]) -> int:
        """Plan and, unless a dry run, write one row. Returns 1 when it wrote (or would write)."""
        self.counts['scanned'] += 1
        database_id = _attr_s(row, 'databaseId')
        asset_id = _attr_s(row, 'assetId')
        bucket_id = _attr_s(row, 'bucketId')
        registration = self._registration(bucket_id) if bucket_id else None
        attributes, reason, review = plan_asset_location(row, registration)
        identity = {'databaseId': database_id, 'assetId': asset_id, 'bucketId': bucket_id,
                    'location': (_plain_attr(row, 'assetLocation') or {}).get('Key')}
        if reason:
            self.counts[reason] += 1
            if not_indexed is not None:
                not_indexed[reason].append(identity)
            return 0
        if entries is not None:
            entries.append({'bucket': attributes[ASSET_LOCATION_BUCKET_ATTRIBUTE],
                            'location': attributes[ASSET_LOCATION_KEY_ATTRIBUTE],
                            'databaseId': database_id, 'assetId': asset_id, 'bucketId': bucket_id,
                            'archived': database_id.endswith(ARCHIVED_ASSET_DATABASE_SUFFIX)})
        if notes is not None:
            for note in review:
                notes[note].append(identity)
        current = {name: _attr_s(row, name) for name in attributes}
        if current == attributes:
            self.counts['alreadyIndexed'] += 1
            return 0
        if self.dry_run:
            self.counts['written'] += 1
            logger.debug(f"  [DRY RUN] would index {database_id}/{asset_id} at "
                         f"{attributes[ASSET_LOCATION_KEY_ATTRIBUTE]}")
            return 1
        outcome = self._write(row, attributes)
        if outcome == 'written':
            self.counts['written'] += 1
            return 1
        if outcome == 'gone':
            self.counts['goneDuringRun'] += 1
            return 0
        self.counts['failed'] += 1
        return 1

    # ---- passes -------------------------------------------------------------------------------

    def _write_pass(self, start_key: Optional[Dict], collect: bool) -> Tuple[bool, Optional[Dict]]:
        """Pass 1. Returns (finished, collected report parts when `collect`)."""
        entries: List[Dict] = []
        notes = {reason: [] for reason in REVIEW_REASONS if reason not in (REVIEW_SHARED_LOCATION, REVIEW_NESTED_LOCATION)}
        not_indexed = {reason: [] for reason in NOT_INDEXED_REASONS}
        for items, next_key in self._scan_pages(start_key, consistent=False):
            for row in items:
                if self.limit is not None and self.counts['scanned'] >= self.limit:
                    logger.info(f"Limit of {self.limit} asset row(s) reached; stopping. The marker is not written.")
                    return False, None
                self._process_row(row, entries if collect else None, notes if collect else None,
                                  not_indexed if collect else None)
            self._save_checkpoint('write', next_key)
        parts = {'entries': entries, 'notes': notes, 'notIndexed': not_indexed} if collect else None
        return True, parts

    def _verify_pass(self) -> Tuple[int, Dict]:
        """One strongly consistent pass over every row; returns (rows written, report parts)."""
        entries: List[Dict] = []
        notes = {reason: [] for reason in REVIEW_REASONS if reason not in (REVIEW_SHARED_LOCATION, REVIEW_NESTED_LOCATION)}
        not_indexed = {reason: [] for reason in NOT_INDEXED_REASONS}
        writes = 0
        saved = self.counts
        self.counts = self._zero_counts()
        for items, _ in self._scan_pages(None, consistent=True):
            for row in items:
                writes += self._process_row(row, entries, notes, not_indexed)
        pass_counts, self.counts = self.counts, saved
        for name in ('written', 'goneDuringRun', 'failed'):
            self.counts[name] += pass_counts[name]
        return writes, {'entries': entries, 'notes': notes, 'notIndexed': not_indexed, 'counts': pass_counts}

    def _report(self, parts: Dict, complete: bool, passes: int) -> Dict:
        overlaps = find_location_overlaps(parts['entries'])
        counts = parts.get('counts') or self.counts
        report = {
            'generatedAt': self.now().isoformat(),
            'assetTable': self.asset_table,
            'dryRun': self.dry_run,
            'complete': complete,
            'verificationPasses': passes,
            'counts': {**counts, 'indexed': len(parts['entries']),
                       **{reason: len(items) for reason, items in parts['notes'].items()},
                       REVIEW_SHARED_LOCATION: len(overlaps[REVIEW_SHARED_LOCATION]),
                       REVIEW_NESTED_LOCATION: len(overlaps[REVIEW_NESTED_LOCATION])},
            'notIndexed': parts['notIndexed'],
            'review': {**parts['notes'], **overlaps},
        }
        with open(self.report_path, 'w') as f:
            json.dump(report, f, indent=2, sort_keys=True, default=str)
        return report

    def _write_marker(self, report: Dict) -> None:
        marker = {'version': ASSET_LOCATION_INDEX_MARKER_VERSION, 'status': ASSET_LOCATION_INDEX_STATUS_COMPLETE,
                  'assetTable': self.asset_table,
                  'completedAt': self.now().isoformat(), 'assetRows': report['counts']['scanned'],
                  'indexed': report['counts']['indexed'],
                  'notIndexed': sum(report['counts'][reason] for reason in NOT_INDEXED_REASONS)}
        self.ssm.put_parameter(Name=self.marker_name, Value=json.dumps(marker, sort_keys=True), Type='String',
                               Overwrite=True,
                               Description='VAMS asset location index backfill completed (v2.6 to v2.7 migration)')

    def run(self) -> Dict:
        """Run the step. Returns {'complete', 'markerWritten', 'counts', 'report'}."""
        checkpoint = self._load_checkpoint()
        phase, start_key = 'write', None
        if checkpoint:
            phase, start_key = checkpoint.get('phase', 'write'), checkpoint.get('exclusiveStartKey')
            self.counts.update(checkpoint.get('counts') or {})
            logger.info(f"Resuming from {self.checkpoint_path} (phase {phase}, "
                        f"{self.counts['scanned']} row(s) already examined)")

        if phase == 'write':
            finished, parts = self._write_pass(start_key, collect=self.dry_run)
            if not finished:
                return {'complete': False, 'limited': True, 'markerWritten': False, 'counts': self.counts,
                        'report': None}
            if self.dry_run:
                report = self._report(parts, complete=False, passes=0)
                return {'complete': False, 'limited': False, 'markerWritten': False, 'counts': self.counts,
                        'report': report}
            self._save_checkpoint('verify', None)

        for verify_pass in range(1, self.max_verify_passes + 1):
            writes, parts = self._verify_pass()
            logger.info(f"Verification pass {verify_pass}: {parts['counts']['scanned']} row(s), {writes} written")
            if writes == 0 and parts['counts']['failed'] == 0:
                report = self._report(parts, complete=True, passes=verify_pass)
                self._write_marker(report)
                self._clear_checkpoint()
                return {'complete': True, 'limited': False, 'markerWritten': True, 'counts': self.counts,
                        'report': report}
        report = self._report(parts, complete=False, passes=self.max_verify_passes)
        logger.error(f"Rows still changed or failed after {self.max_verify_passes} verification passes; "
                     "the marker is not written. Run the step again.")
        return {'complete': False, 'limited': False, 'markerWritten': False, 'counts': self.counts,
                'report': report}


def run_asset_location_index_step(config: dict, args, base_param_prefix, profile, region, dry_run) -> int:
    """Backfill the asset location index and write its completion marker. Returns 0 on success."""
    try:
        resolve = make_resolver(config, base_param_prefix, profile, region)
        asset_table = resolve('asset_storage_table_name', ResourceParamKeys.ASSET_STORAGE_TABLE)
        buckets_table = resolve('s3_asset_buckets_storage_table_name',
                                ResourceParamKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    except Exception as e:
        logger.error(f"Failed resolving table names for the assetLocationIndex step: {e}")
        return 1
    marker_name = config.get('asset_location_index_marker_parameter')
    if _is_unset(marker_name):
        marker_name = asset_location_marker_name(base_param_prefix)
    if not marker_name:
        logger.error("The assetLocationIndex step needs resource_names_ssm_param_prefix (or "
                     "asset_location_index_marker_parameter) to name its completion marker.")
        return 1
    checkpoint_path = config.get('asset_location_checkpoint_file')
    if _is_unset(checkpoint_path):
        checkpoint_path = ASSET_LOCATION_DEFAULT_CHECKPOINT_FILE
    report_path = config.get('asset_location_report_file')
    if _is_unset(report_path):
        report_path = ASSET_LOCATION_DEFAULT_REPORT_FILE
    writes_per_second = config.get('asset_location_writes_per_second')
    if writes_per_second is None:
        writes_per_second = ASSET_LOCATION_DEFAULT_WRITES_PER_SECOND
    limit = args.limit if args.limit is not None else config.get('limit')

    session = _session(profile, region)
    backfill = AssetLocationBackfill(
        session.client('dynamodb', config=_DYNAMODB_CLIENT_CONFIG),
        session.client('ssm', config=_SSM_CLIENT_CONFIG),
        asset_table, buckets_table, marker_name, checkpoint_path, report_path, dry_run,
        writes_per_second=float(writes_per_second), limit=limit)

    logger.info("=" * 80)
    logger.info("VAMS v2.6 -> v2.7 ASSET LOCATION INDEX BACKFILL")
    logger.info(f"Asset table: {asset_table}")
    logger.info(f"Bucket registrations table: {buckets_table}")
    logger.info(f"Completion marker: {marker_name}")
    logger.info(f"Checkpoint: {checkpoint_path}   Report: {report_path}")
    logger.info(f"Dry Run: {dry_run}   Limit: {limit}   Writes per second: {writes_per_second}")
    logger.info("=" * 80)

    start = datetime.now(timezone.utc)
    try:
        result = backfill.run()
    except Exception as e:
        logger.error(f"assetLocationIndex step failed: {e}. Run it again to resume from {checkpoint_path}.")
        return 1
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    counts = result['counts']
    report_counts = (result['report'] or {}).get('counts', {})
    logger.info("=" * 80)
    logger.info("ASSET LOCATION INDEX SUMMARY")
    logger.info(f"  Duration: {duration:.1f}s   Dry Run: {dry_run}")
    logger.info(f"  Asset rows examined:                {counts['scanned']}")
    logger.info(f"  Rows {'that would be ' if dry_run else ''}written:   {counts['written']}")
    logger.info(f"  Rows already indexed:               {counts['alreadyIndexed']}")
    logger.info(f"  Rows gone during the run:           {counts['goneDuringRun']}")
    logger.info(f"  Rows not indexed:                   "
                f"{sum(report_counts.get(r, counts.get(r, 0)) for r in NOT_INDEXED_REASONS)}")
    for reason in NOT_INDEXED_REASONS:
        logger.info(f"    {reason}: {report_counts.get(reason, counts.get(reason, 0))}")
    for reason in REVIEW_REASONS:
        if reason in report_counts:
            logger.info(f"  Indexed, to review - {reason}: {report_counts[reason]}")
    logger.info(f"  Write failures:                     {counts['failed']}")
    if result['report'] is not None:
        logger.info(f"  Report written to {report_path}")
    if result['markerWritten']:
        logger.info(f"  Completion marker written: {marker_name}")
    elif result.get('limited'):
        logger.info("  Stopped at the row limit: the completion marker is NOT written")
    elif not dry_run:
        logger.info("  Completion marker NOT written; run the step again")
    logger.info("=" * 80)

    if dry_run or result.get('limited'):
        return 0
    return 0 if result['complete'] else 1


#######################
# STEP: commentSubscriptionKeys
#######################

# v2.7 stores comments and subscriptions by database and asset id. The v2.6 tables keyed them by the asset
# id alone, and the v2.7 deploy keeps those tables as this step's sources (published under
# dynamoTables/legacy/). The step copies every V1 row into the V2 key of the asset it belongs to and never
# writes or deletes a V1 row. The key shapes mirror backend/backend/common/commentSubscriptionKeys.py.
COMMENT_SUBSCRIPTION_STEP = 'commentSubscriptionKeys'
COMMENT_V1_PARTITION_KEY = 'assetId'
COMMENT_V1_SORT_KEY = 'assetVersionId:commentId'
COMMENT_V2_PARTITION_KEY = 'databaseId:assetId'
COMMENT_V2_SORT_KEY = 'commentId'
COMMENT_DELETED_SUFFIX = '#deleted'
SUBSCRIPTION_V1_SORT_KEY = 'entityName_entityId'
SUBSCRIPTION_V2_SORT_KEY = 'entityName:databaseId:entityId'
ARCHIVED_DATABASE_SUFFIX = '#deleted'
ASSET_ID_INDEX = 'assetIdGSI'
COMMENT_SUBSCRIPTION_WRITES_PER_SECOND = 25
COMMENT_SUBSCRIPTION_PAGE_SIZE = 500
# Copy records: one item per v2.6 comment row and per v2.6 subscriber the step has copied, kept in the V2
# comment table under a reserved partition per asset id and written in the same transaction as the copy.
# A database id never contains '#' (the ID rule), so the partition never meets a comment's, and VAMS reads
# the comment table only by an asset's partition, so no API, web, CLI or MCP read returns one. A record
# outlives what users do with the copy afterwards (edit, delete, unsubscribe, delete the subscription or
# the asset), so a later run never copies that row or subscriber again.
COPY_RECORD_PARTITION_PREFIX = '#commentSubscriptionKeys:'
TRANSACT_MAX_ITEMS = 100
COMMENT_SUBSCRIPTION_ATTEMPTS = 5
# Classes of the JSON Lines report: one per v2.6 comment row, one per subscriber of a v2.6 subscription row.
COMMENT_SUBSCRIPTION_CLASSES = (
    'placed', 'resolvedByVersions', 'resolvedBySnsTopic', 'resolvedByMapping', 'alreadyPresent', 'alreadyCopied',
    'ambiguous', 'duplicateCommentId', 'noAsset', 'noSubscribers', 'unreadable',
)
_COMMENT_SUBSCRIPTION_LEFT = ('ambiguous', 'duplicateCommentId', 'noAsset', 'unreadable')
_RETRYABLE_CANCELLATIONS = {'None', 'ConditionalCheckFailed', 'TransactionConflict'}


def _plain(item: Dict) -> Dict:
    return {k: _ASSET_LOCATION_DESERIALIZER.deserialize(v) for k, v in (item or {}).items()}


def _wire(value: Dict) -> Dict:
    return {k: _COMMENT_SUBSCRIPTION_SERIALIZER.serialize(v) for k, v in value.items()}


def parse_v1_comment_key(partition: str, sort_key: str) -> Optional[Tuple[str, bool, str, str]]:
    """(assetId, deleted, assetVersionId, commentId) of a V1 comment row, or None when unreadable.

    A soft-deleted comment's partition ends with '#deleted'. A comment id never contains ':' (the API
    validated it with the ID rule), so the last ':' of the sort key separates it from the version id."""
    deleted = partition.endswith(COMMENT_DELETED_SUFFIX)
    asset_id = partition[:-len(COMMENT_DELETED_SUFFIX)] if deleted else partition
    version_id, sep, comment_id = sort_key.rpartition(':')
    if not asset_id or not sep or not version_id or not comment_id:
        return None
    return asset_id, deleted, version_id, comment_id


def parse_v1_subscription_key(sort_key: str) -> Optional[Tuple[str, str]]:
    """(entityName, entityId) of a V1 subscription row. An entity name never contains '#'."""
    entity_name, sep, entity_id = sort_key.partition('#')
    if not sep or not entity_name or not entity_id:
        return None
    return entity_name, entity_id


def copy_record_key(asset_id: str, source: str) -> Dict[str, str]:
    """Key of the copy record of one v2.6 comment row or subscriber, in the V2 comment table."""
    return {COMMENT_V2_PARTITION_KEY: f"{COPY_RECORD_PARTITION_PREFIX}{asset_id}", COMMENT_V2_SORT_KEY: source}


def comment_copy_source(deleted: bool, v1_sort_key: str) -> str:
    """The copy record's sort key for a V1 comment row (its asset id is in the partition)."""
    return f"comment|{'deleted' if deleted else 'live'}|{v1_sort_key}"


def subscription_copy_source(event_name: str, subscriber: str) -> str:
    """The copy record's sort key for one subscriber of a V1 subscription row (entity 'Asset', id in the partition)."""
    return f"subscription|{event_name}|{subscriber}"


class CommentSubscriptionCopy:
    """Copies the V1 comment and subscription rows into their database-keyed V2 tables."""

    def __init__(self, dynamodb_client, sns_client, cfg: Dict, dry_run: bool, limit: Optional[int] = None,
                 writes_per_second: float = COMMENT_SUBSCRIPTION_WRITES_PER_SECOND,
                 mapping: Optional[Dict[str, str]] = None, page_size: int = COMMENT_SUBSCRIPTION_PAGE_SIZE,
                 run_id: Optional[str] = None):
        self.ddb = dynamodb_client
        self.sns = sns_client
        self.cfg = cfg
        self.dry_run = dry_run
        self.limit = limit
        self.pause = (1.0 / writes_per_second) if writes_per_second else 0
        self.mapping = mapping or {}
        self.page_size = page_size
        # The run a copy record names; a resumed run takes the one its checkpoint saved (run_table)
        self.run_id = run_id or uuid.uuid4().hex
        self.current_run_id = self.run_id
        self._candidates: Dict[str, List[str]] = {}
        self._versions: Dict[Tuple[str, str], Optional[Tuple[set, str]]] = {}
        self._endpoints: Dict[Tuple[str, str], set] = {}
        self._emails: Dict[str, str] = {}

    # ----- the assets an id belongs to -----

    def candidate_databases(self, asset_id: str) -> List[str]:
        """The databases holding an asset with this id, live or archived, from assetIdGSI (paged)."""
        if asset_id not in self._candidates:
            rows = _query_all(self.ddb, self.cfg['asset_storage_table_name'], 'assetId', asset_id,
                              index=ASSET_ID_INDEX)
            databases = set()
            for row in rows:
                database_id = _attr_s(row, 'databaseId')
                if database_id.endswith(ARCHIVED_DATABASE_SUFFIX):
                    database_id = database_id[:-len(ARCHIVED_DATABASE_SUFFIX)]
                if database_id:
                    databases.add(database_id)
            self._candidates[asset_id] = sorted(databases)
        return self._candidates[asset_id]

    def _asset_row(self, database_id: str, asset_id: str) -> Optional[Dict]:
        for partition in (database_id, f"{database_id}{ARCHIVED_DATABASE_SUFFIX}"):
            item = self.ddb.get_item(TableName=self.cfg['asset_storage_table_name'],
                                     Key=_wire({'databaseId': partition, 'assetId': asset_id})).get('Item')
            if item:
                return _plain(item)
        return None

    def _asset_versions(self, database_id: str, asset_id: str) -> Optional[Tuple[set, str]]:
        """(version ids, earliest version dateCreated) of one asset, from the asset versions table."""
        key = (database_id, asset_id)
        if key not in self._versions:
            rows = [_plain(row) for row in _query_all(self.ddb, self.cfg['asset_versions_storage_table_name'],
                                                     'databaseId:assetId', f"{database_id}:{asset_id}")]
            if not rows:
                self._versions[key] = None
            else:
                created = sorted(str(row.get('dateCreated') or '') for row in rows if row.get('dateCreated'))
                self._versions[key] = ({str(row.get('assetVersionId')) for row in rows},
                                       created[0] if created else '')
        return self._versions[key]

    def _subscriber_addresses(self, subscribers: List[str]) -> set:
        """The subscriber ids and the e-mail addresses the user table holds for them."""
        addresses = set()
        for user_id in subscribers:
            addresses.add(str(user_id).lower())
            if user_id not in self._emails:
                item = self.ddb.get_item(TableName=self.cfg['user_storage_table_name'],
                                         Key=_wire({'userId': user_id})).get('Item')
                self._emails[user_id] = str(_plain(item).get('email') or '') if item else ''
            if self._emails[user_id]:
                addresses.add(self._emails[user_id].lower())
        return addresses

    def _topic_endpoints(self, database_id: str, asset_id: str) -> set:
        key = (database_id, asset_id)
        if key not in self._endpoints:
            endpoints = set()
            row = self._asset_row(database_id, asset_id)
            topic = (row or {}).get('snsTopic')
            if topic:
                kwargs = {'TopicArn': topic}
                while True:
                    try:
                        response = self.sns.list_subscriptions_by_topic(**kwargs)
                    except ClientError as e:
                        if e.response.get('Error', {}).get('Code') == 'NotFound':
                            break
                        raise
                    for subscription in response.get('Subscriptions', []):
                        endpoints.add(str(subscription.get('Endpoint') or '').lower())
                    if not response.get('NextToken'):
                        break
                    kwargs['NextToken'] = response['NextToken']
            self._endpoints[key] = endpoints
        return self._endpoints[key]

    def resolve(self, asset_id: str, evidence: Callable[[str], bool], evidence_class: str
                ) -> Tuple[Optional[str], str, List[str]]:
        """(databaseId or None, report class, candidates) for a V1 comment row or subscriber of this asset id.

        One candidate database: placed there. Several: the evidence rule, then the operator mapping file
        (honoured only when it names a candidate); otherwise ambiguous. Never more than one database."""
        candidates = self.candidate_databases(asset_id)
        if not candidates:
            return None, 'noAsset', candidates
        if len(candidates) == 1:
            return candidates[0], 'placed', candidates
        matching = [database_id for database_id in candidates if evidence(database_id)]
        if len(matching) == 1:
            return matching[0], evidence_class, candidates
        mapped = self.mapping.get(asset_id)
        if mapped in candidates:
            return mapped, 'resolvedByMapping', candidates
        return None, 'ambiguous', candidates

    def place_subscriber(self, asset_id: str, subscriber: str) -> Tuple[Optional[str], str, List[str]]:
        """Where one v2.6 subscriber goes: the database whose asset topic has the subscriber's endpoint (its id
        or its e-mail address in the user table). The mapping file decides only for a subscriber on no
        candidate's topic, or on several."""
        addresses = self._subscriber_addresses([subscriber])

        def evidence(database_id: str) -> bool:
            return bool(self._topic_endpoints(database_id, asset_id) & addresses)

        return self.resolve(asset_id, evidence, 'resolvedBySnsTopic')

    # ----- copy records -----

    def _copy_record(self, asset_id: str, source: str) -> Optional[Dict]:
        item = self.ddb.get_item(TableName=self.cfg['comment_storage_table_name'],
                                 Key=_wire(copy_record_key(asset_id, source)), ConsistentRead=True).get('Item')
        return _plain(item) if item else None

    def _copy_record_put(self, asset_id: str, source: str, source_table: str, database_id: str, result: str) -> Dict:
        item = dict(copy_record_key(asset_id, source), step=COMMENT_SUBSCRIPTION_STEP, sourceTable=source_table,
                    assetId=asset_id, placedInDatabaseId=database_id, result=result, runId=self.current_run_id,
                    copiedAt=datetime.now(timezone.utc).isoformat())
        return {'Put': {'TableName': self.cfg['comment_storage_table_name'], 'Item': _wire(item),
                        'ConditionExpression': 'attribute_not_exists(#k)',
                        'ExpressionAttributeNames': {'#k': COMMENT_V2_SORT_KEY}}}

    def _reported(self, record: Dict, copied: Dict) -> Dict:
        """The report line of a row or subscriber with a copy record: 'alreadyCopied', or the class it was
        copied with when this run (resumed after an interruption) copied it, as an uninterrupted run reports it."""
        same_run = copied.get('runId') == self.current_run_id and copied.get('result')
        return dict(record, databaseId=copied.get('placedInDatabaseId'),
                    result=copied['result'] if same_run else 'alreadyCopied')

    # ----- writes -----

    def _paced(self) -> None:
        if self.pause:
            time.sleep(self.pause)

    def _transact(self, actions: List[Dict]) -> bool:
        """Writes the actions in one transaction. False when a condition no longer held: re-read and retry."""
        try:
            self.ddb.transact_write_items(TransactItems=actions)
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') != 'TransactionCanceledException':
                raise
            reasons = {str(reason.get('Code') or 'None') for reason in e.response.get('CancellationReasons') or []}
            if reasons - _RETRYABLE_CANCELLATIONS:
                raise
            return False
        self._paced()
        return True

    def _existing_comment(self, database_id: str, asset_id: str, comment_id: str) -> Optional[Dict]:
        """The V2 comment with this id on the asset, live or in its '#deleted' partition, or None."""
        for suffix in ('', COMMENT_DELETED_SUFFIX):
            key = {COMMENT_V2_PARTITION_KEY: f"{database_id}:{asset_id}{suffix}", COMMENT_V2_SORT_KEY: comment_id}
            item = self.ddb.get_item(TableName=self.cfg['comment_storage_table_name'], Key=_wire(key),
                                     ConsistentRead=True).get('Item')
            if item:
                return _plain(item)
        return None

    def copy_comment(self, item: Dict) -> List[Dict]:
        partition = _attr_s(item, COMMENT_V1_PARTITION_KEY)
        sort_key = _attr_s(item, COMMENT_V1_SORT_KEY)
        record = {'table': 'comment', 'v1Key': {COMMENT_V1_PARTITION_KEY: partition, COMMENT_V1_SORT_KEY: sort_key}}
        parsed = parse_v1_comment_key(partition, sort_key)
        if parsed is None:
            return [dict(record, result='unreadable')]
        asset_id, deleted, version_id, comment_id = parsed
        record.update(assetId=asset_id, deleted=deleted, commentId=comment_id)
        source = comment_copy_source(deleted, sort_key)
        comment = _plain(item)
        created = str(comment.get('dateCreated') or '')

        def evidence(database_id: str) -> bool:
            # The comment's version counts only when some candidate has it: a comment made in the web app
            # before v2.7 may carry another value (the S3 version or the asset id) in place of the asset
            # version id. The asset must also have existed when the comment was made.
            versions = self._asset_versions(database_id, asset_id)
            if versions is None:
                return False
            version_ids, first_created = versions
            known = any(version_id in (self._asset_versions(db, asset_id) or (set(), ''))[0]
                        for db in self.candidate_databases(asset_id))
            if known and version_id not in version_ids:
                return False
            return not created or not first_created or first_created <= created

        for _ in range(COMMENT_SUBSCRIPTION_ATTEMPTS):
            # Copied before: left as users left it (edited, deleted), even when its asset is gone since
            copied = self._copy_record(asset_id, source)
            if copied:
                return [self._reported(record, copied)]
            database_id, result, candidates = self.resolve(asset_id, evidence, 'resolvedByVersions')
            record['candidates'] = candidates
            if database_id is None:
                return [dict(record, result=result)]
            record['databaseId'] = database_id

            existing = self._existing_comment(database_id, asset_id, comment_id)
            if existing is not None:
                same = existing.get('assetVersionId') == version_id and \
                    existing.get('dateCreated') == comment.get('dateCreated')
                if not same:
                    return [dict(record, result='duplicateCommentId')]
                # The comment is there (live or deleted): only the copy record is missing
                result = 'alreadyPresent'
                actions = [self._copy_record_put(asset_id, source, 'comment', database_id, result)]
            else:
                v2_partition = f"{database_id}:{asset_id}{COMMENT_DELETED_SUFFIX if deleted else ''}"
                target = {k: v for k, v in comment.items() if k not in (COMMENT_V1_PARTITION_KEY, COMMENT_V1_SORT_KEY)}
                target.update({COMMENT_V2_PARTITION_KEY: v2_partition, COMMENT_V2_SORT_KEY: comment_id,
                               'databaseId': database_id, 'assetId': asset_id, 'assetVersionId': version_id})
                actions = [{'Put': {'TableName': self.cfg['comment_storage_table_name'], 'Item': _wire(target),
                                    'ConditionExpression': 'attribute_not_exists(#k)',
                                    'ExpressionAttributeNames': {'#k': COMMENT_V2_SORT_KEY}}},
                           self._copy_record_put(asset_id, source, 'comment', database_id, result)]
            if self.dry_run or self._transact(actions):
                return [dict(record, result=result)]
        return [dict(record, result='unreadable', detail='the V2 rows kept changing; run the step again')]

    def _write_subscribers(self, event_name: str, entity_name: str, asset_id: str, database_id: str,
                           lines: Dict[str, Dict], subscribers: List[str]) -> bool:
        """Adds the subscribers to the V2 row of the asset in this database, with a copy record each, in one
        transaction conditional on the subscriber list it read. A subscriber the row already lists gets its
        copy record only ('alreadyPresent'). False when the row or a copy record changed meanwhile."""
        table = self.cfg['subscriptions_storage_table_name']
        key = {'eventName': event_name, SUBSCRIPTION_V2_SORT_KEY: f"{entity_name}:{database_id}:{asset_id}"}
        existing = self.ddb.get_item(TableName=table, Key=_wire(key), ConsistentRead=True).get('Item')
        row = _plain(existing) if existing else None
        current = [str(s) for s in ((row or {}).get('subscribers') or [])]
        for subscriber in subscribers:
            if subscriber in current:
                lines[subscriber]['result'] = 'alreadyPresent'
        if self.dry_run:
            return True
        new = [s for s in subscribers if s not in current]
        actions = []
        if row is None:
            actions.append({'Put': {'TableName': table, 'Item': _wire(dict(
                key, entityName=entity_name, databaseId=database_id, entityId=asset_id, subscribers=new)),
                'ConditionExpression': 'attribute_not_exists(#k)',
                'ExpressionAttributeNames': {'#k': SUBSCRIPTION_V2_SORT_KEY}}})
        elif new:
            # Conditional on the list read above, so a subscriber added or removed meanwhile is never undone
            update = {'TableName': table, 'Key': _wire(key), 'UpdateExpression': 'SET #s = :new',
                      'ExpressionAttributeNames': {'#s': 'subscribers'}}
            if 'subscribers' in row:
                update.update(ConditionExpression='#s = :old',
                              ExpressionAttributeValues=_wire({':new': current + new, ':old': current}))
            else:
                update['ExpressionAttributeNames']['#k'] = SUBSCRIPTION_V2_SORT_KEY
                update.update(ConditionExpression='attribute_exists(#k) AND attribute_not_exists(#s)',
                              ExpressionAttributeValues=_wire({':new': new}))
            actions.append({'Update': update})
        for subscriber in subscribers:
            actions.append(self._copy_record_put(asset_id, subscription_copy_source(event_name, subscriber),
                                                 'subscription', database_id, lines[subscriber]['result']))
        return self._transact(actions)

    def copy_subscription(self, item: Dict) -> List[Dict]:
        """One report line per subscriber of the V1 row: each subscriber is placed on its own evidence."""
        row = _plain(item)
        event_name = str(row.get('eventName') or '')
        sort_key = str(row.get(SUBSCRIPTION_V1_SORT_KEY) or '')
        record = {'table': 'subscription', 'v1Key': {'eventName': event_name, SUBSCRIPTION_V1_SORT_KEY: sort_key}}
        parsed = parse_v1_subscription_key(sort_key)
        if parsed is None or not event_name or parsed[0] != 'Asset':
            return [dict(record, result='unreadable')]
        entity_name, asset_id = parsed
        record['assetId'] = asset_id
        subscribers = list(dict.fromkeys(str(s) for s in (row.get('subscribers') or [])))
        if not subscribers:
            return [dict(record, result='noSubscribers')]

        for _ in range(COMMENT_SUBSCRIPTION_ATTEMPTS):
            lines: Dict[str, Dict] = {}
            pending: Dict[str, List[str]] = {}
            for subscriber in subscribers:
                line = dict(record, subscriber=subscriber)
                # Copied before: an unsubscribe or a deleted subscription since then stays as the user left it
                copied = self._copy_record(asset_id, subscription_copy_source(event_name, subscriber))
                if copied:
                    lines[subscriber] = self._reported(line, copied)
                    continue
                database_id, result, candidates = self.place_subscriber(asset_id, subscriber)
                lines[subscriber] = dict(line, candidates=candidates, result=result)
                if database_id is not None:
                    lines[subscriber]['databaseId'] = database_id
                    pending.setdefault(database_id, []).append(subscriber)
            written = True
            for database_id, placed in pending.items():
                for start in range(0, len(placed), TRANSACT_MAX_ITEMS - 1):
                    chunk = placed[start:start + TRANSACT_MAX_ITEMS - 1]
                    if not self._write_subscribers(event_name, entity_name, asset_id, database_id, lines, chunk):
                        written = False
                        break
                if not written:
                    break
            if written:
                return [lines[s] for s in subscribers]
        return [dict(record, subscriber=s, result='unreadable', detail='the V2 row kept changing; run the step again')
                for s in subscribers]

    # ----- the scan -----

    def run_table(self, source_table: str, key_names: Tuple[str, str], copy: Callable[[Dict], List[Dict]], report,
                  checkpoint_path: str, counts: Dict[str, int], resume: bool) -> bool:
        """Scan one V1 table page by page, copying each row and saving the scan position after each page.

        Returns True when the table was scanned to its end, False when --limit stopped it (the position is
        then saved after the last row examined, so --resume continues with the next one)."""
        start_key = None
        self.current_run_id = self.run_id
        checkpoint = _read_checkpoint(checkpoint_path, source_table) if resume else None
        if checkpoint:
            counts.update(checkpoint.get('counts') or {})
            self.current_run_id = checkpoint.get('runId') or self.run_id
            if checkpoint.get('complete'):
                logger.info(f"{source_table} was scanned to its end by the run being resumed "
                            f"({counts['scanned']} row(s)).")
                return True
            start_key = checkpoint.get('exclusiveStartKey')
            logger.info(f"Resuming {source_table} from {checkpoint_path} after {counts['scanned']} row(s).")
        else:
            _write_copy_checkpoint(checkpoint_path, source_table, None, counts, self.current_run_id, report, False)
        while True:
            kwargs = {'TableName': source_table, 'ConsistentRead': True, 'Limit': self.page_size}
            if start_key:
                kwargs['ExclusiveStartKey'] = start_key
            response = self.ddb.scan(**kwargs)
            last_key = None
            for item in response.get('Items', []):
                if self.limit is not None and counts['scanned'] >= self.limit:
                    logger.info(f"Limit of {self.limit} row(s) reached for {source_table}; stopping.")
                    if last_key is not None:
                        _write_copy_checkpoint(checkpoint_path, source_table, last_key, counts, self.current_run_id,
                                               report, False)
                    return False
                last_key = {name: item[name] for name in key_names if name in item}
                counts['scanned'] += 1
                try:
                    records = copy(item)
                except ClientError as e:
                    records = [{'table': source_table, 'result': 'unreadable', 'detail': str(e)}]
                for record in records:
                    counts[record['result']] += 1
                    report.write(json.dumps(record, default=str) + '\n')
                    if record['result'] in _COMMENT_SUBSCRIPTION_LEFT:
                        subscriber = f" subscriber {record['subscriber']}" if record.get('subscriber') else ''
                        logger.info(f"  {'[DRY RUN] ' if self.dry_run else ''}{record.get('table')} "
                                    f"{record.get('assetId', '?')}{subscriber}: {record['result']}")
                report.flush()
            start_key = response.get('LastEvaluatedKey')
            if not start_key:
                break
            _write_copy_checkpoint(checkpoint_path, source_table, start_key, counts, self.current_run_id, report, False)
        _write_copy_checkpoint(checkpoint_path, source_table, None, counts, self.current_run_id, report, True)
        return True


def _write_copy_checkpoint(path: str, source_table: str, start_key: Optional[Dict], counts: Dict[str, int],
                           run_id: str, report, complete: bool) -> None:
    """The scan position, the counts and the report length after the last finished page, and the run id.

    A resumed run cuts the report back to that length, so the rows of the interrupted page are reported
    once, and reports what that run copied with the class it was copied with."""
    report.flush()
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'assetTable': source_table, 'exclusiveStartKey': start_key, 'counts': counts, 'runId': run_id,
                   'reportOffset': os.fstat(report.fileno()).st_size, 'complete': complete}, f)
    os.replace(tmp, path)


def _comment_subscription_files(output_dir: str, dry_run: bool, table: str) -> Tuple[str, str]:
    mode = 'dry-run' if dry_run else 'execute'
    return (os.path.join(output_dir, f"{COMMENT_SUBSCRIPTION_STEP}-{mode}-report.jsonl"),
            os.path.join(output_dir, f"{COMMENT_SUBSCRIPTION_STEP}-{table}-{mode}-checkpoint.json"))


def load_resolution_file(path: Optional[str]) -> Dict[str, str]:
    """The operator's {"<assetId>": "<databaseId>"} mapping for ids several databases hold."""
    if _is_unset(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        mapping = json.load(f)
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                for k, v in mapping.items()):
        raise ValueError(f"{path} must be a JSON object mapping asset ids to database ids")
    return mapping


def run_comment_subscription_keys_step(config: dict, args, base_param_prefix, profile, region,
                                       dry_run) -> int:
    """Copy the V1 comment and subscription rows into the database-keyed V2 tables. Returns 0 on success."""
    try:
        resolve = make_resolver(config, base_param_prefix, profile, region)
        cfg = {
            'comment_storage_table_name_v1': resolve(
                'comment_storage_table_name_v1', ResourceParamKeys.LEGACY_COMMENT_STORAGE_TABLE_V1),
            'subscriptions_storage_table_name_v1': resolve(
                'subscriptions_storage_table_name_v1', ResourceParamKeys.LEGACY_SUBSCRIPTIONS_STORAGE_TABLE_V1),
            'comment_storage_table_name': resolve(
                'comment_storage_table_name', ResourceParamKeys.COMMENT_STORAGE_TABLE),
            'subscriptions_storage_table_name': resolve(
                'subscriptions_storage_table_name', ResourceParamKeys.SUBSCRIPTIONS_STORAGE_TABLE),
            'asset_storage_table_name': resolve('asset_storage_table_name', ResourceParamKeys.ASSET_STORAGE_TABLE),
            'asset_versions_storage_table_name': resolve(
                'asset_versions_storage_table_name', ResourceParamKeys.ASSET_VERSIONS_STORAGE_TABLE),
            'user_storage_table_name': resolve('user_storage_table_name', ResourceParamKeys.USER_STORAGE_TABLE),
        }
        mapping = load_resolution_file(config.get('comment_subscription_resolution_file'))
    except Exception as e:
        logger.error(f"Failed preparing the {COMMENT_SUBSCRIPTION_STEP} step: {e}")
        return 1
    if cfg['comment_storage_table_name_v1'] == cfg['comment_storage_table_name'] or \
            cfg['subscriptions_storage_table_name_v1'] == cfg['subscriptions_storage_table_name']:
        logger.error(f"{COMMENT_SUBSCRIPTION_STEP}: a source table is also its target. Deploy v2.7 first, or fix "
                     "the *_table_name overrides in the config.")
        return 1

    output_dir = config.get('comment_subscription_output_dir') or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    limit = args.limit if args.limit is not None else config.get('limit')
    rate = config.get('comment_subscription_writes_per_second')
    rate = COMMENT_SUBSCRIPTION_WRITES_PER_SECOND if rate is None else float(rate)
    session = _session(profile, region)
    copier = CommentSubscriptionCopy(session.client('dynamodb', config=_DYNAMODB_CLIENT_CONFIG),
                                     session.client('sns', config=_SNS_CLIENT_CONFIG), cfg, dry_run,
                                     limit=limit, writes_per_second=rate, mapping=mapping)
    report_path, _ = _comment_subscription_files(output_dir, dry_run, 'comments')
    tables = (
        ('comments', cfg['comment_storage_table_name_v1'], (COMMENT_V1_PARTITION_KEY, COMMENT_V1_SORT_KEY),
         copier.copy_comment),
        ('subscriptions', cfg['subscriptions_storage_table_name_v1'], ('eventName', SUBSCRIPTION_V1_SORT_KEY),
         copier.copy_subscription),
    )
    checkpoint_paths = [_comment_subscription_files(output_dir, dry_run, name)[1] for name, _, _, _ in tables]
    resume = bool(args.resume) and any(os.path.exists(path) for path in checkpoint_paths)
    if args.resume and not resume:
        logger.info(f"{COMMENT_SUBSCRIPTION_STEP}: no checkpoint to resume from; starting over.")

    logger.info("=" * 80)
    logger.info("VAMS v2.6 -> v2.7 COMMENT AND SUBSCRIPTION KEYS")
    logger.info(f"Comments:      {cfg['comment_storage_table_name_v1']} -> {cfg['comment_storage_table_name']}")
    logger.info(f"Subscriptions: {cfg['subscriptions_storage_table_name_v1']} -> "
                f"{cfg['subscriptions_storage_table_name']}")
    logger.info(f"Report:        {report_path}   Mapping entries: {len(mapping)}")
    logger.info(f"Dry Run: {dry_run}   Limit: {limit}   Resume: {args.resume}   Writes/s: {rate}")
    logger.info("=" * 80)

    totals = {}
    complete = True
    try:
        if resume:
            # A checkpoint of another table is refused here, before the report is touched
            saved = [_read_checkpoint(path, source) for (_, source, _, _), path in zip(tables, checkpoint_paths)]
            offsets = [c['reportOffset'] for c in saved if c and c.get('reportOffset') is not None]
            if offsets and os.path.exists(report_path):
                # The lines written after the last saved page are written again by the resumed scan
                with open(report_path, 'r+b') as f:
                    f.truncate(offsets[-1])
        else:
            for path in checkpoint_paths:
                if os.path.exists(path):
                    os.remove(path)
        with open(report_path, 'a' if resume else 'w', encoding='utf-8') as report:
            for (name, source, key_names, copy), checkpoint_path in zip(tables, checkpoint_paths):
                counts = {'scanned': 0, **{c: 0 for c in COMMENT_SUBSCRIPTION_CLASSES}}
                complete = copier.run_table(source, key_names, copy, report, checkpoint_path, counts,
                                            resume) and complete
                totals[name] = counts
    except Exception as e:
        logger.error(f"{COMMENT_SUBSCRIPTION_STEP} step failed: {e}. Re-run with --resume to continue from the last "
                     "saved page.")
        return 1
    if complete:
        for path in checkpoint_paths:
            if os.path.exists(path):
                os.remove(path)

    logger.info("=" * 80)
    logger.info(f"COMMENT AND SUBSCRIPTION KEYS SUMMARY{' (DRY RUN: counts of what a real run would write)' if dry_run else ''}")
    for name, counts in totals.items():
        logger.info(f"  {name}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    left = sum(counts['ambiguous'] + counts['duplicateCommentId'] for counts in totals.values())
    if left:
        logger.info(f"  {left} comment row(s) or subscriber(s) were left in the V1 tables (ambiguous or "
                    "duplicateCommentId). Name the database of each ambiguous asset id in "
                    "comment_subscription_resolution_file and run the step again: it copies only what earlier "
                    "runs left.")
    logger.info("=" * 80)
    return 0 if all(counts['unreadable'] == 0 for counts in totals.values()) else 1


#######################
# CLI
#######################

STEP_CHOICES = ('assetLocationFolderKeys', 'orphanedTriggers', 'vectorBackfill', 'metadataBackfill',
                'systemPipelineRetirement', 'commentSubscriptionKeys', 'assetLocationIndex', 'all')


def _gated_summary(deletes_triggers: bool, backfills_vectors: bool, clears_vectors: bool,
                   backfills_metadata: bool = False, fixes_locations: bool = False,
                   indexes_locations: bool = False, copies_comments: bool = False) -> str:
    """What a real run changes, deletes, bills or rewrites, phrased to follow "This run will"."""
    parts = []
    if fixes_locations:
        parts.append("append '/' to asset location keys that name a folder and move the records of a "
                     "single-file asset's copy named like its file (assetLocationFolderKeys)")
    if deletes_triggers:
        parts.append('delete orphaned workflow-trigger rows (orphanedTriggers)')
    if copies_comments:
        parts.append('copy every comment and subscription into the tables keyed by database and asset id '
                     '(commentSubscriptionKeys)')
    if clears_vectors:
        parts.append('delete every stored vector (vectorBackfill --clear-vectors)')
    if backfills_vectors:
        parts.append('launch one Amazon Bedrock-billed execution per file (vectorBackfill)')
    if backfills_metadata:
        parts.append('launch one Amazon Bedrock-billed execution per file (metadataBackfill)')
    if indexes_locations:
        parts.append('write the location index attributes on every asset row and its completion marker '
                     '(assetLocationIndex)')
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
        description='VAMS v2.6 to v2.7 data migration (asset location folder keys, orphaned trigger cleanup, '
                    'vector index backfill, system-pipeline retirement report, comment and subscription keys, '
                    'asset location index backfill). '
                    'Dry run by default.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run, all steps (the default: nothing is deleted or launched)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json

  # Real run, all steps (asks for the account id: orphanedTriggers deletes rows, vectorBackfill bills Bedrock)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute

  # Real run without a prompt (automation): the resolved account must equal the id given
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --confirm-account <account-id>

  # Append the trailing '/' to asset location keys that name a folder (run before the backfills)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps assetLocationFolderKeys

  # Delete orphaned trigger rows only
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps orphanedTriggers

  # Rebuild the vector index from scratch (clears every stored vector first)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps vectorBackfill --clear-vectors

  # Vector search off: analyze every existing file with the system pipeline (GenAI metadata, no vectors)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute \\
      --steps metadataBackfill --generate-metadata-for-existing-files

  # Report in-flight retired executions and workflows that still reference a retired pipeline (reads only)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --steps systemPipelineRetirement

  # Backfill the asset location index (writes two attributes on every asset row, then the completion marker)
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps assetLocationIndex

  # Copy the v2.6 comments and subscriptions into the tables keyed by database and asset id
  python v2.6_to_v2.7_migration.py --config v2.6_to_v2.7_migration_config.json --execute --steps commentSubscriptionKeys

Notes:
  - Every run is a DRY RUN unless --execute is given AND the config's dry_run is false. The shipped
    config sets "dry_run": true; set it to false once the dry run's counts look right.
  - A real run that changes asset rows (assetLocationFolderKeys), deletes rows (orphanedTriggers),
    deletes vectors (--clear-vectors), or launches
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
  - assetLocationFolderKeys reads one object, the versions of that one key and at most one key under it per
    asset row whose location key has no trailing '/', writes each finding to
    assetLocationFolderKeys-<mode>-report.jsonl, and saves its scan position after every page (--resume
    continues from it). Part (b) moves the records of a single-file asset's copy '{key}/{name}' to
    '/{name}/{name}' and marks the asset row done, so no later run moves them again. Run it before users
    edit metadata on single-file assets. It runs first under --steps all.
  - assetLocationIndex is required after the v2.7 deploy and runs after assetLocationFolderKeys (last under
    --steps all, and skipped in a real run when assetLocationFolderKeys failed): it writes locationBucket and
    locationKey on every asset row (one UpdateItem per row that lacks them, paced), verifies them with
    strongly consistent passes, writes a report of the rows it could not index, and writes the completion
    marker last. It resumes from its checkpoint file after an interruption, and a second run writes no row.
  - --limit caps asset rows examined (assetLocationFolderKeys, assetLocationIndex; a limited index run never
    writes the completion marker), trigger rows examined (orphanedTriggers), is forwarded as the reindexer's
    limit (vectorBackfill), caps rows listed per section (systemPipelineRetirement), and caps V1 rows examined
    per table (commentSubscriptionKeys).
  - commentSubscriptionKeys is required after the v2.7 deploy on a deployment with comments or subscriptions:
    until it runs, existing comments do not show and existing subscriptions read as not subscribed (change
    e-mails keep arriving: they use each asset's topic). It writes commentSubscriptionKeys-<mode>-report.jsonl,
    saves its scan position after every page (--resume continues from it), never overwrites a V2 row, and
    never writes or deletes a v2.6 row. Ids several databases hold that the evidence cannot place are left as
    'ambiguous': name their database in comment_subscription_resolution_file and run the step again. A
    repeat copies only what earlier runs left ('alreadyCopied' for the rest), so it never brings back a
    deleted comment, an unsubscribed user or a deleted subscription.
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
    parser.add_argument('--resume', action='store_true',
                        help='assetLocationFolderKeys and commentSubscriptionKeys: continue the scan from the checkpoint a '
                             'stopped run of the same mode (dry run or real) saved. Default: start over.')
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

    run_location_keys = args.steps in ('assetLocationFolderKeys', 'all')
    run_orphaned_triggers = args.steps in ('orphanedTriggers', 'all')
    run_vector_backfill = args.steps in ('vectorBackfill', 'all')
    run_metadata_backfill = args.steps in ('metadataBackfill', 'all')
    run_retirement_report = args.steps in ('systemPipelineRetirement', 'all')
    run_asset_location_index = args.steps in ('assetLocationIndex', 'all')
    run_comment_subscription_keys = args.steps in ('commentSubscriptionKeys', 'all')

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
    if run_location_keys:
        step_plan.append('assetLocationFolderKeys')
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
    if run_comment_subscription_keys:
        step_plan.append('commentSubscriptionKeys')
    if run_asset_location_index:
        step_plan.append('assetLocationIndex')

    # Echo the resolved target before anything is deleted or billed. The shipped config template leaves
    # aws_profile and aws_region null, so boto3 falls back to the ambient environment, and the SSM
    # prefix is /{configName}-{baseStackName}-{region}/resourceNames with no account in it. Two
    # deployments in different accounts that share a config name, stack name and Region therefore
    # resolve identical parameter paths, and nothing else in this script would reveal which account
    # it had reached.
    #
    # The gate covers every step that deletes, bills or rewrites rows: orphanedTriggers deletes rows,
    # a real vectorBackfill launches one Bedrock-billed execution per file whether or not it clears the
    # stored vectors first, and assetLocationIndex writes every asset row. systemPipelineRetirement only
    # reads.
    clears_vectors = backfills_vectors and clear_vectors
    if not confirm_migration_target(
        profile=profile,
        region=region,
        base_param_prefix=base_param_prefix,
        dry_run=dry_run,
        args_dry_run=args.dry_run,
        gated=run_location_keys or run_orphaned_triggers or backfills_vectors or backfills_metadata
        or run_asset_location_index or run_comment_subscription_keys,
        gated_summary=_gated_summary(run_orphaned_triggers, backfills_vectors, clears_vectors,
                                     backfills_metadata, fixes_locations=run_location_keys,
                                     indexes_locations=run_asset_location_index,
                                     copies_comments=run_comment_subscription_keys),
        confirm_account=args.confirm_account,
        step_plan=step_plan,
    ):
        return 1

    exit_code = 0
    outcomes: List[Tuple[str, str]] = []

    def _record(step: str, rc: int) -> None:
        outcomes.append((step, "completed" if rc == 0 else f"failed (exit {rc})"))

    # First: the backfills below walk every asset's location, so a folder key gets its '/' before them.
    location_keys_failed = False
    if run_location_keys:
        logger.info("")
        logger.info("##### STEP: Asset location folder keys #####")
        rc = run_asset_location_folder_keys_step(config, args, base_param_prefix, profile, region, dry_run)
        _record('assetLocationFolderKeys', rc)
        if rc != 0:
            exit_code = rc
            location_keys_failed = True

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

    # Independent of the location steps: it reads the asset table's assetIdGSI and the version records only.
    if run_comment_subscription_keys:
        logger.info("")
        logger.info("##### STEP: Comment and subscription keys #####")
        rc = run_comment_subscription_keys_step(config, args, base_param_prefix, profile, region, dry_run)
        _record('commentSubscriptionKeys', rc)
        if rc != 0:
            exit_code = rc

    if run_asset_location_index and location_keys_failed and not dry_run:
        logger.info("")
        logger.error("assetLocationIndex skipped: assetLocationFolderKeys failed in this run, and the index "
                     "is built from the location keys that step fixes. Fix its errors, run it again, then "
                     "run --steps assetLocationIndex.")
        outcomes.append(('assetLocationIndex', ASSET_LOCATION_INDEX_SKIPPED_AFTER_FOLDER_KEYS))
        exit_code = exit_code or 1
    elif run_asset_location_index:
        logger.info("")
        logger.info("##### STEP: Asset location index backfill #####")
        rc = run_asset_location_index_step(config, args, base_param_prefix, profile, region, dry_run)
        _record('assetLocationIndex', rc)
        if rc != 0:
            exit_code = rc

    print_run_summary(outcomes, dry_run)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
