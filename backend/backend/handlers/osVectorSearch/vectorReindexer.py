# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Vector index reindexer: clears the vector table and/or enqueues the latest live file versions for
re-embedding by the system GenAI workflow.

Invoked directly (operator or data-migration tooling), or as a CloudFormation custom resource when
`app.vectorSearch.reindexOnCdkDeploy` is set — the same two shapes the OpenSearch `crReindexer` takes.
There is no event source. Direct payload:

    {operation: "enqueue" | "clear" | "both", dryRun?: bool, limit?: int, databaseId?: str, startAfter?: str}

`clear` pages the table on its two key attributes and batch-deletes; with `databaseId` the scan is filtered
to partition keys beginning `<databaseId>:`, so a scoped clear never touches another database's vectors.
`enqueue` enumerates the latest live file versions (bucket registrations -> list_objects_v2 -> asset
resolution, archived assets excluded), keeps the files the system workflow's input filters admit, and sends
one launch message per file to the system-workflow launch queue in batches of ten; the launcher paces the
executions. `both` runs `clear` to completion across every continuation and only then starts `enqueue`.

The function exists whenever the system GenAI metadata pipeline is on and registered, with vector search
on or off; `VECTOR_SEARCH_ENABLED` says which. With vector search off there is no vector index and nothing
to clear: the function reads no vector index environment, builds no vector store, and refuses `clear` and
`both` with a 400. `enqueue` works the same way in both cases, so the system workflow analyzes the
existing files; its runs skip the embedding step while vector search is off.

Work is bounded by the 15-minute Lambda window: when fewer than MIN_REMAINING_MS remain the function
re-invokes itself asynchronously with a `continuation` token (the reserved sixth payload key, written and
read only by this function) carrying the scan cursor or the enumeration marker, the run id, and the
running counts. The response reports where the run stands after this invocation.

AWS Lambda's recursive-loop detection would stop that chain after about 16 invocations, so it is turned
off for this function (`recursiveLoop: ALLOW` in the partitions that have it); the chain is bounded here
instead: a run makes at most MAX_RUN_INVOCATIONS invocations, and the one that reaches the cap with work
left stops rather than continuing itself (ReindexHopLimitReached).

A failure in the first invocation is returned as a 500 to its caller. A failure in a continuation, the
invocation cap included, raises ReindexContinuationFailed, so AWS Lambda sends that asynchronous
invocation to the function's on-failure dead-letter queue once (its async config retries nothing); the
error message carries the payload that resumes the run after the last file the failed invocation sent.
A resume payload from the cap starts the invocation count again, so the resumed run gets a full budget.

Every invocation that runs or refuses a reindex writes one `searchReindex` audit record as the system
(`_log_reindex`): what it was asked, what it did, and the run id its other invocations share.
"""

import json
import os
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from common.s3 import region_routing_s3_client
from botocore.config import Config

from common.indexing.fileEnumeration import FileRef, encode_continuation, enumerate_latest_live_files
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from common.vectorsearch.vectorStore import DynamoDbVectorStore
from common.workflows.executionValidation import aggregate_input_file_filters, apply_input_file_filters
from customLogging.auditLogging import audited_handler, current_audit_context, log_action
from customLogging.logger import safeLogger

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
# The continuation Invoke is asynchronous and re-queues from a cursor: a retried duplicate would enqueue
# the same files twice, so it is delivered exactly once with a short connect/read budget.
invoke_config = Config(retries={'total_max_attempts': 1}, read_timeout=60, connect_timeout=10)
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
# Asset buckets may be in another Region: every call is signed for the Region of the bucket it names.
s3_client = region_routing_s3_client()
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
# The most invocations one run (or one resume of it) makes: about 50 hours of 15-minute invocations. With
# recursive-loop detection off this is the bound on a chain that never finishes; the invocation that
# reaches it with work left sends its resume payload to the dead-letter queue instead of continuing.
MAX_RUN_INVOCATIONS = 200
# How an invocation was started, as its searchReindex record names it: an operator's (or a migration
# tool's) direct invoke, the run's own continuation (or an operator resuming one), or the CloudFormation
# custom resource of app.vectorSearch.reindexOnCdkDeploy.
TRIGGER_DIRECT = 'direct'
TRIGGER_CONTINUATION = 'continuation'
TRIGGER_CLOUDFORMATION = 'cloudFormation'

try:
    vector_search_enabled = os.environ.get("VECTOR_SEARCH_ENABLED", "true").strip().lower() == "true"
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    workflow_storage_table_v2_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    pipeline_storage_table_v2_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    launch_queue_url = os.environ["WORKFLOW_LAUNCH_QUEUE_URL"]
    system_workflow_id = os.environ["GENAI_METADATA_WORKFLOW_ID"]
    system_workflow_database_id = os.environ["GENAI_METADATA_WORKFLOW_DATABASE_ID"]
    if vector_search_enabled:
        vector_table_name = get_table_name(ResourceKeys.VECTOR_EMBEDDINGS_STORAGE_TABLE)
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
# Only `clear` addresses the vector table, and it needs vector search.
vector_store = DynamoDbVectorStore(
    vector_table_name, vector_index_name, embedding_model_id, embedding_dimensions, dynamodb_client
) if vector_search_enabled else None

# The 400 a `clear` or `both` gets while vector search is off.
CLEAR_NEEDS_VECTOR_SEARCH = ("vector search is not enabled on this deployment, so there are no vectors to "
                             "clear; use operation 'enqueue' to analyze the existing files")


class SystemWorkflowNotFound(Exception):
    pass


class ReindexHopLimitReached(Exception):
    """The run reached MAX_RUN_INVOCATIONS with work left, so it stops instead of continuing itself."""


class ReindexContinuationFailed(Exception):
    """A continuation that failed. Raised rather than returned so AWS Lambda records the asynchronous
    invocation as failed and sends it, once, to the reindexer's on-failure dead-letter queue (the async
    config retries nothing: a retry would re-run the continuation from its start and send again every
    file this attempt already sent). The message is JSON: the run id, the phase, the error, and
    `resumePayload`, the payload that continues the run after the last file this invocation sent."""


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
    if operation in ('clear', 'both') and not vector_search_enabled:
        return None, CLEAR_NEEDS_VECTOR_SEARCH
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


def _record_sent(state: Dict[str, Any], sent: List[FileRef]) -> None:
    """Count the files sent and move the enumeration cursor to the last of them, so the run's state names
    exactly what was sent if a later batch of the same page fails (see ReindexContinuationFailed)."""
    if not sent:
        return
    state['enqueued'] += len(sent)
    state['chunk'] = state['enqueued'] // FILES_PER_CHUNK
    state['startAfter'] = encode_continuation(sent[-1].bucket_name, sent[-1].s3_key, sent[-1].bucket_id)


def _send_launch_messages(refs: List[FileRef], state: Dict[str, Any], dry_run: bool) -> None:
    """One message per file, in SendMessageBatch entries of ten; entries SQS rejects are retried once.
    When entries still fail, the files before the first of them are recorded as sent and the run fails."""
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
                still_failed = {f['Id'] for f in response.get('Failed', []) or []}
                if still_failed:
                    first_unsent = min(int(entry_id) for entry_id in still_failed)
                    _record_sent(state, window[:first_unsent])
                    raise RuntimeError(f"{len(still_failed)} launch message(s) could not be sent")
        _record_sent(state, window)


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
    # A dry run sends nothing, so its count is of the files a real run would enqueue.
    outcome = "would be enqueued" if params['dryRun'] else "enqueued"
    logger.info(f"vector reindex run {state['runId']}: {len(selected)} of {len(refs)} enumerated files "
                f"{outcome} (total {state['enqueued']})")
    if params['limit'] is not None and state['enqueued'] >= params['limit']:
        state['startAfter'] = None
        return True
    state['startAfter'] = token
    return token is None


def continuation_payload(params: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """The payload that continues the run from `state`: what the next self-invoke sends, and the
    `resumePayload` a failed continuation reports."""
    payload: Dict[str, Any] = {'operation': params['operation'], 'dryRun': params['dryRun'],
                               'continuation': dict(state)}
    if params['limit'] is not None:
        payload['limit'] = params['limit']
    if params['databaseId'] is not None:
        payload['databaseId'] = params['databaseId']
    return payload


def _self_invoke(params: Dict[str, Any], state: Dict[str, Any]) -> None:
    lambda_client.invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType='Event',
        Payload=json.dumps(continuation_payload(params, state)).encode('utf-8'))
    logger.info(f"vector reindex run {state['runId']} continues in phase {state['phase']}")


def _log_reindex(trigger: str, params: Optional[Dict[str, Any]] = None, state: Optional[Dict[str, Any]] = None,
                 before: Tuple[int, int] = (0, 0), reason: Optional[str] = None) -> None:
    """Record one invocation of a reindex run as the system's searchReindex action, in the invocation's
    audit context: success, or failed with a generic `reason` (`validation` for a refused payload,
    `internal` for a run that failed), never the error text or a file name. `item_count` is what this
    invocation did: the vector items it deleted plus the files it enqueued (a dry run counts what a real
    run would). The details name the run (`reindexRunId`, `invocation`, and `trigger`: direct,
    continuation or cloudFormation), what it was asked (`operation`, `dryRun`, `limit`, and `scope` with
    the record's databaseId when the run is confined to one database) and where the run stands
    (`phase`, `continued`, and the run's `deleted` and `enqueued` totals), so the records of one run
    group by its id. Writes nothing outside an invocation."""
    ctx = current_audit_context()
    if ctx is None:
        return
    details: Dict[str, Any] = {'trigger': trigger}
    item_count = None
    database_id = None
    if params is not None:
        details.update({'operation': params['operation'], 'dryRun': params['dryRun'],
                        'scope': 'database' if params['databaseId'] else 'all'})
        if params['limit'] is not None:
            details['limit'] = params['limit']
        database_id = params['databaseId']
    if state is not None:
        details.update({'reindexRunId': state['runId'], 'invocation': state['invocations'],
                        'phase': state['phase'], 'continued': reason is None and state['phase'] != PHASE_DONE,
                        'deleted': state['deleted'], 'enqueued': state['enqueued']})
        item_count = (state['deleted'] - before[0]) + (state['enqueued'] - before[1])
    log_action(ctx.event, "searchReindex", outcome="failed" if reason else "success", reason=reason,
               item_count=item_count, database_id=database_id, details=details)


def _run_reindex(params: Dict[str, Any], context: Any, raise_on_failure: bool = False,
                 trigger: str = TRIGGER_DIRECT) -> Dict[str, Any]:
    """One invocation's worth of work for a validated payload; the response reports where the run stands.

    A failure returns a 500 body to a caller that waits for it (the first invocation, the CloudFormation
    custom resource). With `raise_on_failure` (an asynchronous continuation, whose return value nobody
    reads) it raises ReindexContinuationFailed instead, so the failed continuation reaches the dead-letter
    queue. Either way the invocation writes one searchReindex record (`_log_reindex`)."""
    time_left = _time_left(context)
    state = _initial_state(params)
    before = (state['deleted'], state['enqueued'])
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
            if state['invocations'] >= MAX_RUN_INVOCATIONS:
                raise ReindexHopLimitReached(
                    f"the run reached {MAX_RUN_INVOCATIONS} invocations with phase {state['phase']} "
                    f"unfinished; resume it with the resumePayload")
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
        _log_reindex(trigger, params, state, before)
        return {'statusCode': 200, 'body': json.dumps(body)}
    except Exception as e:
        logger.exception(f"vector reindex run {state['runId']} failed in phase {state['phase']}: {e}")
        _log_reindex(trigger, params, state, before, reason='internal')
        # A resume after the cap starts a fresh invocation budget; any other resume keeps the count.
        resume_state = dict(state, invocations=0) if isinstance(e, ReindexHopLimitReached) else state
        if raise_on_failure:
            raise ReindexContinuationFailed(json.dumps({
                'reindexRunId': state['runId'], 'phase': state['phase'],
                'error': f"{type(e).__name__}: {e}"[:1000],
                'resumePayload': continuation_payload(params, resume_state),
            })) from e
        return {'statusCode': 500, 'body': json.dumps(
            {'error': 'vector reindex failed', 'reindexRunId': state['runId'], 'phase': state['phase']})}


# ---------------------------------------------------------------------------------------------------
# CloudFormation custom resource (app.vectorSearch.reindexOnCdkDeploy)
# ---------------------------------------------------------------------------------------------------

def is_cfn_event(event: Any) -> bool:
    """A custom-resource request carries both keys; a direct invocation carries neither."""
    return isinstance(event, dict) and 'RequestType' in event and 'ResponseURL' in event


def cfn_payload_from_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    """Map the resource's string properties onto the direct-invoke payload.

    `Operation` defaults to `both` and `DryRun` to false; `Timestamp` is what the construct changes to
    re-fire the resource and carries no meaning here. Anything else is passed through so an unknown
    property is rejected by validate_payload rather than ignored.
    """
    payload: Dict[str, Any] = {'operation': properties.get('Operation', 'both')}
    dry_run = str(properties.get('DryRun', 'false')).lower()
    if dry_run not in ('true', 'false'):
        payload['dryRun'] = properties.get('DryRun')
    elif dry_run == 'true':
        payload['dryRun'] = True
    if properties.get('DatabaseId'):
        payload['databaseId'] = properties['DatabaseId']
    for key in properties:
        if key not in ('Operation', 'DryRun', 'DatabaseId', 'Timestamp', 'ServiceToken'):
            payload[key] = properties[key]
    return payload


def handle_cfn_event(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Run the reindex for a Create/Update request and return the cr.Provider onEvent response.

    The provider framework relays this dict to CloudFormation, so the function never posts to
    `ResponseURL` itself. A run that outlives one invocation hands off to an async continuation and is
    reported as started: the deploy does not wait on Bedrock re-analyzing every file, and the run's id
    is returned as `Data.ReindexRunId` so an operator can follow it in the function's log group. A
    Delete request is a no-op — the vector table is retained and the rows stay valid.
    """
    request_type = event.get('RequestType')
    physical_id = event.get('PhysicalResourceId') or 'vams-vector-reindex-trigger'
    if request_type == 'Delete':
        logger.info("CloudFormation Delete request: nothing to reindex")
        return {'PhysicalResourceId': physical_id, 'Data': {'Message': 'Delete is a no-op'}}
    payload = cfn_payload_from_properties(event.get('ResourceProperties') or {})
    params, error = validate_payload(payload)
    if error:
        logger.error(f"CloudFormation {request_type} request rejected: {error}")
        _log_reindex(TRIGGER_CLOUDFORMATION, reason='validation')
        raise ValueError(f"vector reindex request rejected: {error}")
    logger.info(f"CloudFormation {request_type} request: operation={params['operation']} "
                f"dryRun={params['dryRun']}")
    result = _run_reindex(params, context, trigger=TRIGGER_CLOUDFORMATION)
    body = json.loads(result['body'])
    if result['statusCode'] != 200:
        # A failed FIRST invocation fails the resource; a failure inside a later continuation is
        # reported only in the log group, as the deploy has already moved on by then.
        raise RuntimeError(
            f"vector reindex run {body.get('reindexRunId')} failed in phase {body.get('phase')}")
    run = 'Vector reindex dry run' if params['dryRun'] else 'Vector reindex'
    return {
        'PhysicalResourceId': physical_id,
        'Data': {
            'Message': f"{run} started" if body['continued'] else f"{run} completed",
            'ReindexRunId': body['reindexRunId'],
            'Phase': body['phase'],
            'Deleted': str(body['deleted']),
            'Enqueued': str(body['enqueued']),
            'Continued': str(body['continued']).lower(),
        },
    }


@audited_handler
def lambda_handler(event, context) -> Dict[str, Any]:
    """Two invocation shapes: a direct payload, or a CloudFormation custom-resource request relayed
    by the CDK provider framework when `app.vectorSearch.reindexOnCdkDeploy` is set. Each invocation that
    runs or refuses a reindex writes one searchReindex record as the system (`_log_reindex`); a Delete
    request does nothing and writes none."""
    if is_cfn_event(event):
        return handle_cfn_event(event, context)
    params, error = validate_payload(event)
    if error:
        logger.warning(f"Rejected vector reindex payload: {error}")
        resumed = isinstance(event, dict) and event.get('continuation') is not None
        _log_reindex(TRIGGER_CONTINUATION if resumed else TRIGGER_DIRECT, reason='validation')
        return {'statusCode': 400, 'body': json.dumps({'error': error})}
    # A payload with a continuation is a self-invoke (or an operator resuming one from the dead-letter
    # queue), always asynchronous: its failure must fail the invocation to reach the dead-letter queue.
    resumed = params['continuation'] is not None
    return _run_reindex(params, context, raise_on_failure=resumed,
                        trigger=TRIGGER_CONTINUATION if resumed else TRIGGER_DIRECT)
