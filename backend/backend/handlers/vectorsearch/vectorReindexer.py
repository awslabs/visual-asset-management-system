# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Vector index reindexer: clears the vector table and/or enqueues the latest live file versions for
re-embedding by the system GenAI workflow.

Invoked directly (operator or data-migration tooling); there is no event source. Payload:

    {operation: "enqueue" | "clear" | "both", dryRun?: bool, limit?: int, databaseId?: str, startAfter?: str}

`clear` pages the table on its two key attributes and batch-deletes; with `databaseId` the scan is filtered
to partition keys beginning `<databaseId>:`, so a scoped clear never touches another database's vectors.
`enqueue` enumerates the latest live file versions (bucket registrations -> list_objects_v2 -> asset
resolution, archived assets excluded), keeps the files the system workflow's input filters admit, and sends
one launch message per file to the system-workflow launch queue in batches of ten; the launcher paces the
executions. `both` runs `clear` to completion across every continuation and only then starts `enqueue`.

Work is bounded by the 15-minute Lambda window: when fewer than MIN_REMAINING_MS remain the function
re-invokes itself asynchronously with a `continuation` token (the reserved sixth payload key, written and
read only by this function) carrying the scan cursor or the enumeration marker, the run id, and the
running counts. The response reports where the run stands after this invocation.
"""

import json
import os
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config

from common.indexing.fileEnumeration import FileRef, enumerate_latest_live_files
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from common.vectorsearch.vectorStore import DynamoDbVectorStore
from common.workflows.executionValidation import aggregate_input_file_filters, apply_input_file_filters
from customLogging.logger import safeLogger

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
# The continuation Invoke is asynchronous and re-queues from a cursor: a retried duplicate would enqueue
# the same files twice, so it is delivered exactly once with a short connect/read budget.
invoke_config = Config(retries={'total_max_attempts': 1}, read_timeout=60, connect_timeout=10)
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
sqs_client = boto3.client('sqs', config=retry_config)
lambda_client = boto3.client('lambda', config=invoke_config)
logger = safeLogger(service_name="VectorReindexer")

OPERATIONS = ('enqueue', 'clear', 'both')
ALLOWED_KEYS = frozenset({'operation', 'dryRun', 'limit', 'databaseId', 'startAfter', 'continuation'})
CONTINUATION_KEYS = frozenset(
    {'runId', 'phase', 'clearStartKey', 'startAfter', 'chunk', 'enqueued', 'deleted', 'invocations'})
PHASE_CLEAR = 'clear'
PHASE_ENQUEUE = 'enqueue'
PHASE_DONE = 'done'
# Reserve below which the invocation hands off to a continuation instead of being cut mid-write.
MIN_REMAINING_MS = 90000
# Executions of one reindex run share `executionGroupId = vec-{runId}-{chunk}`; a chunk is this many
# files, so a group stays small enough for the per-group abort (200 per request) to drain in one pass.
FILES_PER_CHUNK = 1000
SEND_BATCH_SIZE = 10
SCAN_PAGE_SIZE = 1000

try:
    vector_table_name = get_table_name(ResourceKeys.VECTOR_EMBEDDINGS_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    workflow_storage_table_v2_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    pipeline_storage_table_v2_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    launch_queue_url = os.environ["SYSTEM_WORKFLOW_LAUNCH_QUEUE_URL"]
    system_workflow_id = os.environ["SYSTEM_GENAI_WORKFLOW_ID"]
    system_workflow_database_id = os.environ.get("SYSTEM_GENAI_WORKFLOW_DATABASE_ID", "GLOBAL")
    vector_index_name = os.environ["VECTOR_INDEX_NAME"]
    embedding_model_id = os.environ["EMBEDDING_MODEL_ID"]
    embedding_dimensions = int(os.environ["EMBEDDING_DIMENSIONS"])
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

asset_storage_table = dynamodb.Table(asset_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)
workflow_storage_table_v2 = dynamodb.Table(workflow_storage_table_v2_name)
pipeline_storage_table_v2 = dynamodb.Table(pipeline_storage_table_v2_name)
vector_store = DynamoDbVectorStore(
    vector_table_name, vector_index_name, embedding_model_id, embedding_dimensions, dynamodb_client)


class SystemWorkflowNotFound(Exception):
    pass


def validate_payload(event: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(normalized params, None) or (None, error message)."""
    if not isinstance(event, dict):
        return None, "payload must be a JSON object"
    unknown = sorted(set(event) - ALLOWED_KEYS)
    if unknown:
        return None, f"unknown payload keys: {unknown}"
    operation = event.get('operation')
    if operation not in OPERATIONS:
        return None, f"operation must be one of {list(OPERATIONS)}"
    dry_run = event.get('dryRun', False)
    if not isinstance(dry_run, bool):
        return None, "dryRun must be a boolean"
    limit = event.get('limit')
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        return None, "limit must be a positive integer"
    database_id = event.get('databaseId')
    if database_id is not None:
        valid, message = validate({'databaseId': {'value': database_id, 'validator': 'ID'}})
        if not valid:
            return None, message
    start_after = event.get('startAfter')
    if start_after is not None and not isinstance(start_after, str):
        return None, "startAfter must be a string"
    continuation = event.get('continuation')
    if continuation is not None:
        if not isinstance(continuation, dict) or set(continuation) - CONTINUATION_KEYS:
            return None, "continuation is not a token this function issued"
    return {
        'operation': operation, 'dryRun': dry_run, 'limit': limit,
        'databaseId': database_id, 'startAfter': start_after, 'continuation': continuation,
    }, None


def _time_left(context: Any) -> Callable[[], int]:
    getter = getattr(context, 'get_remaining_time_in_millis', None)
    return getter if callable(getter) else (lambda: 900000)


def _initial_state(params: Dict[str, Any]) -> Dict[str, Any]:
    state = dict(params['continuation'] or {})
    state.setdefault('runId', uuid.uuid4().hex[:12])
    state.setdefault('phase', PHASE_CLEAR if params['operation'] in ('clear', 'both') else PHASE_ENQUEUE)
    state.setdefault('clearStartKey', None)
    state.setdefault('startAfter', params['startAfter'])
    state.setdefault('chunk', 0)
    state.setdefault('enqueued', 0)
    state.setdefault('deleted', 0)
    state.setdefault('invocations', 0)
    state['invocations'] += 1
    return state


def _clear_scope(params: Dict[str, Any]) -> Optional[str]:
    """The partition-key prefix a databaseId-scoped clear is confined to (`<databaseId>:`), else None."""
    return f"{params['databaseId']}:" if params['databaseId'] else None


def run_clear(state: Dict[str, Any], time_left: Callable[[], int], dry_run: bool,
              pk_prefix: Optional[str] = None) -> bool:
    """Delete every key page by page (only the keys under `pk_prefix` when one is given). Returns True when
    the table has been walked to the end."""
    start_key = state.get('clearStartKey')
    while True:
        keys, next_key = vector_store.scan_keys(start_key=start_key, limit=SCAN_PAGE_SIZE, pk_prefix=pk_prefix)
        if keys:
            state['deleted'] += len(keys) if dry_run else vector_store.delete_keys(keys)
        state['clearStartKey'] = next_key
        if next_key is None:
            return True
        start_key = next_key
        if time_left() < MIN_REMAINING_MS:
            return False


def _system_workflow_filters() -> Dict[str, List[str]]:
    """The {allow, exclude} restriction the system workflow imposes, aggregated over its pipelines."""
    workflow = workflow_storage_table_v2.get_item(
        Key={'databaseId': system_workflow_database_id, 'workflowId': system_workflow_id}).get('Item')
    if not workflow:
        raise SystemWorkflowNotFound(
            f"workflow {system_workflow_database_id}:{system_workflow_id} is not registered")
    pipeline_configs = []
    for ref in workflow.get('specifiedPipelines') or []:
        row = pipeline_storage_table_v2.get_item(
            Key={'databaseId': ref.get('pipelineDatabaseId'), 'pipelineId': ref.get('pipelineId')}).get('Item') or {}
        pipeline_configs.append(row.get('systemConfig') or {})
    aggregate = aggregate_input_file_filters(workflow.get('systemConfig') or {}, pipeline_configs)
    return {'allow': aggregate['allow'], 'exclude': aggregate['exclude']}


def _send_launch_messages(refs: List[FileRef], state: Dict[str, Any], dry_run: bool) -> None:
    """One message per file, in SendMessageBatch entries of ten; entries SQS rejects are retried once."""
    for start in range(0, len(refs), SEND_BATCH_SIZE):
        window = refs[start:start + SEND_BATCH_SIZE]
        entries = []
        for offset, ref in enumerate(window):
            message = {
                'databaseId': ref.database_id, 'assetId': ref.asset_id,
                'relativeFileKey': ref.relative_file_key, 'versionId': '',
                'reindexRunId': state['runId'], 'chunk': (state['enqueued'] + offset) // FILES_PER_CHUNK,
            }
            entries.append({'Id': str(offset), 'MessageBody': json.dumps(message)})
        if not dry_run:
            response = sqs_client.send_message_batch(QueueUrl=launch_queue_url, Entries=entries)
            failed_ids = {f['Id'] for f in response.get('Failed', []) or []}
            if failed_ids:
                retry = [e for e in entries if e['Id'] in failed_ids]
                response = sqs_client.send_message_batch(QueueUrl=launch_queue_url, Entries=retry)
                still_failed = response.get('Failed', []) or []
                if still_failed:
                    raise RuntimeError(f"{len(still_failed)} launch message(s) could not be sent")
        state['enqueued'] += len(window)
        state['chunk'] = state['enqueued'] // FILES_PER_CHUNK


def run_enqueue(state: Dict[str, Any], params: Dict[str, Any], time_left: Callable[[], int]) -> bool:
    """Enumerate, filter, and enqueue the latest live files. Returns True when the enumeration is
    exhausted (or the limit is reached); False when the enumerator handed back a continuation marker."""
    filters = _system_workflow_filters()
    if params['limit'] is not None and state['enqueued'] >= params['limit']:
        return True
    refs, token = enumerate_latest_live_files(
        s3_client=s3_client, buckets_table=s3_asset_buckets_table, asset_table=asset_storage_table,
        database_id=params['databaseId'], start_after=state.get('startAfter'),
        time_remaining_fn=time_left, min_remaining_ms=MIN_REMAINING_MS)
    candidates = [{'relativeFileKey': ref.relative_file_key, 'ref': ref} for ref in refs]
    selected = [c['ref'] for c in apply_input_file_filters(candidates, filters)]
    if params['limit'] is not None:
        selected = selected[:max(0, params['limit'] - state['enqueued'])]
    _send_launch_messages(selected, state, params['dryRun'])
    logger.info(f"vector reindex run {state['runId']}: {len(selected)} of {len(refs)} enumerated files "
                f"enqueued (total {state['enqueued']})")
    if params['limit'] is not None and state['enqueued'] >= params['limit']:
        state['startAfter'] = None
        return True
    state['startAfter'] = token
    return token is None


def _self_invoke(params: Dict[str, Any], state: Dict[str, Any]) -> None:
    payload: Dict[str, Any] = {'operation': params['operation'], 'dryRun': params['dryRun'],
                               'continuation': state}
    if params['limit'] is not None:
        payload['limit'] = params['limit']
    if params['databaseId'] is not None:
        payload['databaseId'] = params['databaseId']
    lambda_client.invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType='Event',
        Payload=json.dumps(payload).encode('utf-8'))
    logger.info(f"vector reindex run {state['runId']} continues in phase {state['phase']}")


def lambda_handler(event, context) -> Dict[str, Any]:
    params, error = validate_payload(event)
    if error:
        logger.warning(f"Rejected vector reindex payload: {error}")
        return {'statusCode': 400, 'body': json.dumps({'error': error})}
    time_left = _time_left(context)
    state = _initial_state(params)
    logger.info(f"vector reindex run {state['runId']}: operation={params['operation']} "
                f"phase={state['phase']} dryRun={params['dryRun']} invocation={state['invocations']}")
    try:
        if state['phase'] == PHASE_CLEAR:
            if run_clear(state, time_left, params['dryRun'], _clear_scope(params)):
                state['phase'] = PHASE_ENQUEUE if params['operation'] == 'both' else PHASE_DONE
        if state['phase'] == PHASE_ENQUEUE and time_left() >= MIN_REMAINING_MS:
            if run_enqueue(state, params, time_left):
                state['phase'] = PHASE_DONE
        continued = state['phase'] != PHASE_DONE
        if continued:
            _self_invoke(params, state)
        body: Dict[str, Any] = {
            'operation': params['operation'], 'reindexRunId': state['runId'], 'phase': state['phase'],
            'dryRun': params['dryRun'], 'deleted': state['deleted'], 'enqueued': state['enqueued'],
            'chunks': (state['enqueued'] + FILES_PER_CHUNK - 1) // FILES_PER_CHUNK,
            'continued': continued, 'invocations': state['invocations'],
        }
        # tableEmpty speaks for the whole table, so a databaseId-scoped clear never reports it.
        if params['operation'] in ('clear', 'both') and state['phase'] != PHASE_CLEAR \
                and params['databaseId'] is None:
            body['tableEmpty'] = not params['dryRun']
        return {'statusCode': 200, 'body': json.dumps(body)}
    except Exception as e:
        logger.exception(f"vector reindex run {state['runId']} failed in phase {state['phase']}: {e}")
        return {'statusCode': 500, 'body': json.dumps(
            {'error': 'vector reindex failed', 'reindexRunId': state['runId'], 'phase': state['phase']})}
