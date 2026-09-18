# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Vector index writer: the single owner of the vector embeddings table.

Consumes one SQS queue fed by four sources and maps each record onto the lifecycle of a per-file-version
vector item:

* `vector.embedding.ready` events from the orchestration bus (EventBridge -> SQS; the event JSON is the
  message body): read the embedding document from the auxiliary bucket (an event locating it in any other
  bucket, or a document over EMBEDDING_DOCUMENT_MAX_BYTES, is dropped), resolve the bucket registration
  from the event's `bucketId` or, when the pipeline left it empty, the asset row's, decide `isLatest` /
  `isArchived` against live S3 state and the asset row, write the item, delete the document. A document
  is either the file version's whole-file vector or one of its segment vectors (a video time window or a
  content chunk, keyed `{keyPath}#{versionId}#{segmentKey}`); a whole-file document also removes the
  version's segment items left by an earlier run. After a latest whole-file write the key's S3 state is
  read again: when a newer version landed in between, the file's latest marks are realigned to it.
* Bucket-sync S3 records republished on the file indexer SNS topic (the SQS -> SNS -> SQS -> SNS -> S3
  envelope `fileIndexer.py` unwraps): a new version flips the file's other items to `isLatest="false"`, a
  delete marker archives them, a marker removal un-archives them, a permanent delete removes them. A new
  version whose `vams-changesource` is a restore (`fileUnarchive`, `assetUnarchive`) is the file's newest
  content copied forward, so it only un-archives the file's items and demotes nothing: the items of the
  copied content stay the file's latest and the file is searchable at once.
* Asset table stream records republished on the asset indexer SNS topic: an archive marks every item of
  the asset, a permanent delete removes them; metadata, attribute and link stream records are ignored.
* The indexer's own `vector.indexer.continue` messages: an asset- or file-wide rule pages through its
  item collection under the invocation's time budget, and when the store hands back a start key the rest
  of the rule is re-enqueued here and resumed from that key by a later invocation.

Every per-file rule reaches the store through the file's key-path prefix, so segment items follow their
file through new versions, archive, unarchive and delete without a rule of their own. Every write is keyed
and conditional inside the store, so a duplicate delivery repeats a no-op. The handler reports
partial-batch failures: only the records whose processing failed are redelivered.
"""

import json
import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.batchItemFailures import (
    all_batch_item_failures,
    batch_item_identifier,
    with_batch_item_failures,
)
from common.dynamodb import query_all_items
from common.indexing.documentIds import (
    SEGMENT_KEY_MAX_BYTES,
    SEGMENT_KINDS,
    SORT_KEY_MAX_BYTES,
    build_vector_file_version_key,
    vector_file_key_path,
)
from common.resourceNames import ResourceKeys, get_bucket_name, get_table_name
from common.s3 import list_all_object_versions
from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_SOURCE_RESTORE_VALUES,
)
from common.s3PathPatterns import PREVIEW_FILE_PATTERN, key_has_reserved_segment
from common.vectorsearch.embeddings import round_vector
from common.vectorsearch.vectorStore import DynamoDbVectorStore, VectorItem
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
sqs_client = boto3.client('sqs', config=retry_config)
logger = safeLogger(service_name="VectorIndexer")

EMBEDDING_READY_DETAIL_TYPE = "vector.embedding.ready"
# Body key and value of the indexer's own continuation message (a raw JSON body, never SNS-wrapped).
CONTINUE_DETAIL_TYPE = "vector.indexer.continue"
# The asset- and file-wide rules a continuation message names; each resumes one store method.
RULE_OBJECT_CREATED = 'objectCreated'
RULE_SET_ARCHIVED_FOR_FILE = 'setArchivedForFile'
RULE_DELETE_FILE = 'deleteFile'
RULE_SET_ARCHIVED_FOR_ASSET = 'setArchivedForAsset'
RULE_DELETE_ASSET = 'deleteAsset'
FILE_RULES = (RULE_OBJECT_CREATED, RULE_SET_ARCHIVED_FOR_FILE, RULE_DELETE_FILE)
# The function's own timeout, reported as the time left when no Lambda context supplies one.
FULL_BUDGET_MS = 900_000
ARCHIVED_PARTITION_SUFFIX = "#deleted"
FILE_EXT_NONE = "none"
# Stored excerpt of the embedded text; the embedded text itself may be longer.
SOURCE_TEXT_STORED_CHARS = 8000
S3_NOT_FOUND_CODES = ('404', 'NoSuchKey', 'NotFound')
# The whole-file vector of a file version; every other segmentKind marks one segment of it.
SEGMENT_KIND_NONE = "none"
# The six segment fields of the embedding document and event, with the whole-file values a publisher
# that omits them receives.
SEGMENT_FIELD_DEFAULTS = {
    'segmentKey': '',
    'segmentKind': SEGMENT_KIND_NONE,
    'segmentLabel': '',
    'segmentStartMs': None,
    'segmentEndMs': None,
    'segmentCount': 0,
}
SEGMENT_KEY_SEPARATOR = '#'
REQUIRED_DETAIL_KEYS = ('databaseId', 'assetId', 'filePath',
                        'embeddingModelId', 'embeddingDimensions', 'documentS3Location')
# Present in every event but legitimately empty: '' is an unversioned bucket, stored as 'null'.
PRESENT_DETAIL_KEYS = ('versionId',)
# Largest embedding document read: the document is one embedding of at most a few thousand numbers plus
# an excerpt of the embedded text, well under this; a larger object is not a document this indexer wrote.
EMBEDDING_DOCUMENT_MAX_BYTES = 1_048_576

try:
    vector_table_name = get_table_name(ResourceKeys.VECTOR_EMBEDDINGS_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    vector_index_name = os.environ["VECTOR_INDEX_NAME"]
    embedding_model_id = os.environ["EMBEDDING_MODEL_ID"]
    embedding_dimensions = int(os.environ["EMBEDDING_DIMENSIONS"])
    # Embedding documents live in the auxiliary bucket only; an event naming any other bucket is dropped.
    aux_bucket_name = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    vector_indexer_queue_url = os.environ["VECTOR_INDEXER_QUEUE_URL"]
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

asset_storage_table = dynamodb.Table(asset_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)
vector_store = DynamoDbVectorStore(
    vector_table_name, vector_index_name, embedding_model_id, embedding_dimensions, dynamodb_client)


@dataclass
class Outcome:
    """Result of one unwrapped record. `ok` False marks the SQS message for redelivery."""
    ok: bool
    action: str
    detail: str = ""


@dataclass
class S3KeyState:
    """Live state of one key: whether anything remains, whether a delete marker is current, and the
    id of the current content version (the newest content version when a marker is current)."""
    exists: bool
    archived: bool
    current_version_id: Optional[str]


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _time_left(context: Any) -> Callable[[], int]:
    """The invocation's remaining-time callable, or a full budget when no Lambda context supplies one."""
    getter = getattr(context, 'get_remaining_time_in_millis', None)
    return getter if callable(getter) else (lambda: FULL_BUDGET_MS)


def _continue_later(rule: str, pk: str, start_key: Dict[str, Any], key_path: Optional[str] = None,
                    version_id: Optional[str] = None, archived: Optional[bool] = None) -> str:
    """Re-enqueue the rest of an asset- or file-wide rule to this function's own queue, from the store's
    exclusive start key for the next page, and return the suffix the Outcome's detail records."""
    message: Dict[str, Any] = {'detailType': CONTINUE_DETAIL_TYPE, 'rule': rule, 'pk': pk}
    if key_path is not None:
        message['keyPath'] = key_path
    if version_id is not None:
        message['versionId'] = version_id
    if archived is not None:
        message['archived'] = archived
    message['startKey'] = start_key
    sqs_client.send_message(QueueUrl=vector_indexer_queue_url, MessageBody=json.dumps(message))
    logger.info(f"{rule} for {pk} continues in a later invocation from {start_key}")
    return ' continued'


# --- S3 and table lookups shared by the rules -----------------------------------------------------------

def _is_not_found(error: ClientError) -> bool:
    return error.response.get('Error', {}).get('Code') in S3_NOT_FOUND_CODES


def resolve_key_state(bucket_name: str, key: str) -> S3KeyState:
    try:
        head = s3_client.head_object(Bucket=bucket_name, Key=key)
        return S3KeyState(exists=True, archived=False, current_version_id=head.get('VersionId') or 'null')
    except ClientError as e:
        if not _is_not_found(e):
            raise
    # A 404 is either a current delete marker or a key with nothing left; only the listing tells which.
    listing = list_all_object_versions(bucket_name, key, client=s3_client)
    versions = [v for v in listing.get('Versions', []) if v.get('Key') == key]
    markers = [m for m in listing.get('DeleteMarkers', []) if m.get('Key') == key]
    if not versions and not markers:
        return S3KeyState(exists=False, archived=False, current_version_id=None)
    marker_is_current = any(m.get('IsLatest') for m in markers)
    if not versions:
        return S3KeyState(exists=True, archived=True, current_version_id=None)
    newest = max(versions, key=lambda v: v.get('LastModified'))
    return S3KeyState(exists=True, archived=marker_is_current,
                      current_version_id=newest.get('VersionId') or 'null')


def find_preview_file_key(bucket_name: str, key: str) -> str:
    """The `.previewFile.*` sibling of a key, or '' when none exists."""
    prefix = key + PREVIEW_FILE_PATTERN
    response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix, MaxKeys=5)
    for obj in response.get('Contents', []) or []:
        candidate = obj.get('Key', '')
        if candidate.startswith(prefix):
            return candidate
    return ''


def _get_asset_row(database_id: str, asset_id: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """(row, archived): the live row, else the `{db}#deleted` row with archived=True, else (None, False)."""
    live = asset_storage_table.get_item(
        Key={'databaseId': database_id, 'assetId': asset_id}, ConsistentRead=True).get('Item')
    if live:
        return live, False
    archived = asset_storage_table.get_item(
        Key={'databaseId': f"{database_id}{ARCHIVED_PARTITION_SUFFIX}", 'assetId': asset_id},
        ConsistentRead=True).get('Item')
    if archived:
        return archived, True
    return None, False


def _get_bucket_record(bucket_id: str) -> Optional[Dict[str, Any]]:
    """The bucket registration for a bucketId (one row per registration; the sort key is bucketName:prefix)."""
    response = s3_asset_buckets_table.query(KeyConditionExpression=Key('bucketId').eq(bucket_id), Limit=1)
    items = response.get('Items') or []
    return items[0] if len(items) > 0 else None


def _normalize_prefix(prefix: Optional[str]) -> str:
    """A registered prefix as it appears in object keys: `prefix-a/`, or '' for a bucket rooted at `/`."""
    text = (prefix or '').strip('/')
    return f"{text}/" if text else ''


def _asset_base_key(asset_row: Optional[Dict[str, Any]], base_prefix: str, asset_id: str) -> str:
    location = ((asset_row or {}).get('assetLocation') or {}).get('Key') if asset_row else None
    base = location or f"{base_prefix}{asset_id}/"
    return base if base.endswith('/') else base + '/'


def _file_ext(declared: Any, file_path: str) -> str:
    text = str(declared or '').strip().lower().lstrip('.')
    if not text:
        name = os.path.basename(file_path.rstrip('/'))
        text = name.rsplit('.', 1)[1].lower() if '.' in name else ''
    return text or FILE_EXT_NONE


def _declared(detail: Dict[str, Any], document: Dict[str, Any], name: str) -> Any:
    """A field as the publisher declared it: the Detail's value when the key is present (an empty value
    included), else the document's."""
    return detail[name] if name in detail else document.get(name)


def _parse_s3_uri(location: Any) -> Tuple[str, str]:
    parsed = urllib.parse.urlparse(str(location or ''))
    key = parsed.path.lstrip('/')
    if parsed.scheme != 's3' or not parsed.netloc or not key:
        raise ValueError("documentS3Location is not an s3://bucket/key URI")
    return parsed.netloc, key


def _read_document(key: str) -> Optional[Dict[str, Any]]:
    """The embedding document at ``key`` in the auxiliary bucket, or None when the object is larger than
    EMBEDDING_DOCUMENT_MAX_BYTES. The body is read one byte past the cap at most, so an object of any size
    costs that much memory before it is refused; a document that is not a JSON object reads as empty."""
    response = s3_client.get_object(Bucket=aux_bucket_name, Key=key)
    if int(response.get('ContentLength') or 0) > EMBEDDING_DOCUMENT_MAX_BYTES:
        return None
    raw = response['Body'].read(EMBEDDING_DOCUMENT_MAX_BYTES + 1)
    if len(raw) > EMBEDDING_DOCUMENT_MAX_BYTES:
        return None
    parsed = json.loads(raw.decode('utf-8'))
    return parsed if isinstance(parsed, dict) else {}


def _segment_fields(detail: Dict[str, Any], document: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """The six segment fields of a document — the Detail's value, else the document's, else the whole-file
    default — checked against the store's key rules: (fields, '') or (None, reason) when the document is
    not writable. Checked here rather than left to VectorItem so a publisher fault is dropped, not
    redelivered."""
    fields: Dict[str, Any] = {}
    for name, default in SEGMENT_FIELD_DEFAULTS.items():
        if name in detail:
            fields[name] = detail[name]
        elif name in document:
            fields[name] = document[name]
        else:
            fields[name] = default
    kind = str(fields['segmentKind'] or SEGMENT_KIND_NONE)
    key = str(fields['segmentKey'] or '')
    if kind not in SEGMENT_KINDS:
        return None, f"segmentKind {kind!r} is not one of {SEGMENT_KINDS}"
    if len(key.encode('utf-8')) > SEGMENT_KEY_MAX_BYTES:
        return None, f"segmentKey exceeds {SEGMENT_KEY_MAX_BYTES} bytes"
    if SEGMENT_KEY_SEPARATOR in key:
        return None, f"segmentKey contains the sort-key separator {SEGMENT_KEY_SEPARATOR!r}"
    if (kind == SEGMENT_KIND_NONE) != (key == ''):
        return None, f"segmentKind {kind!r} disagrees with segmentKey {key!r}"
    start, end = fields['segmentStartMs'], fields['segmentEndMs']
    fields.update({
        'segmentKey': key,
        'segmentKind': kind,
        'segmentLabel': str(fields['segmentLabel'] or ''),
        'segmentStartMs': int(start) if start is not None else None,
        'segmentEndMs': int(end) if end is not None else None,
        'segmentCount': int(fields['segmentCount'] or 0),
    })
    return fields, ''


# --- vector.embedding.ready --------------------------------------------------------------------------

def handle_embedding_ready(detail: Dict[str, Any]) -> Outcome:
    missing = [k for k in REQUIRED_DETAIL_KEYS if detail.get(k) in (None, '')]
    missing += [k for k in PRESENT_DETAIL_KEYS if k not in detail]
    if missing:
        logger.warning(f"embedding.ready detail is missing {missing}; dropping")
        return Outcome(True, 'drop', f"missing detail keys {missing}")
    if detail['embeddingModelId'] != embedding_model_id or \
            int(detail['embeddingDimensions']) != embedding_dimensions:
        logger.warning("embedding.ready names a model or dimension other than the configured index; dropping")
        return Outcome(True, 'drop', 'model or dimensions differ from the configured index')

    try:
        doc_bucket, doc_key = _parse_s3_uri(detail['documentS3Location'])
    except ValueError as e:
        logger.error(str(e))
        return Outcome(False, 'error', str(e))
    if doc_bucket != aux_bucket_name:
        # The event names where the document is; the indexer reads and deletes only in the auxiliary
        # bucket, so a location elsewhere is a publisher fault, acknowledged and left untouched.
        logger.warning("embedding.ready names a document outside the auxiliary bucket; dropping")
        return Outcome(True, 'drop', 'document is not in the auxiliary bucket')
    document = _read_document(doc_key)
    if document is None:
        logger.warning(f"embedding.ready document {doc_key} exceeds {EMBEDDING_DOCUMENT_MAX_BYTES} bytes; dropping")
        return Outcome(True, 'drop', f"document exceeds {EMBEDDING_DOCUMENT_MAX_BYTES} bytes")
    if document.get('embeddingModelId') != embedding_model_id or \
            int(document.get('embeddingDimensions') or 0) != embedding_dimensions:
        return Outcome(True, 'drop', 'document model or dimensions differ from the configured index')
    embedding = document.get('embedding') or []
    if len(embedding) != embedding_dimensions:
        return Outcome(True, 'drop', 'embedding length differs from the configured dimensions')
    segment, rejection = _segment_fields(detail, document)
    if segment is None:
        logger.warning(f"embedding.ready carries unusable segment fields ({rejection}); dropping")
        return Outcome(True, 'drop', rejection)

    database_id = str(detail['databaseId'])
    asset_id = str(detail['assetId'])
    file_path = '/' + str(detail['filePath']).lstrip('/')
    version_id = str(detail['versionId'] or 'null')
    try:
        sort_key = build_vector_file_version_key(file_path, version_id, segment['segmentKey'])
    except ValueError as e:
        # The segment key passed its own checks above, so the builder is refusing the assembled length.
        logger.warning(f"embedding.ready for {file_path}#{version_id} has a sort key over "
                       f"{SORT_KEY_MAX_BYTES} bytes ({e}); dropping")
        return Outcome(True, 'drop', f"sort key exceeds {SORT_KEY_MAX_BYTES} bytes: {e}")
    asset_row, asset_archived = _get_asset_row(database_id, asset_id)
    if asset_row is None:
        logger.warning(f"embedding.ready for {database_id}:{asset_id} whose asset row is gone; dropping")
        return Outcome(True, 'drop', 'asset row absent from both partitions')
    # An empty bucketId falls back to the asset row's registration id.
    bucket_id = str(detail.get('bucketId') or asset_row.get('bucketId') or '')
    if not bucket_id:
        logger.error(f"{database_id}:{asset_id} names no bucketId in the event or the asset row")
        return Outcome(False, 'error', 'bucketId absent from the event and the asset row')
    bucket_record = _get_bucket_record(bucket_id)
    if bucket_record is None:
        logger.error(f"bucketId {bucket_id} has no registration row")
        return Outcome(False, 'error', 'bucket registration not found')
    bucket_name = bucket_record['bucketName']
    base_prefix = _normalize_prefix(bucket_record.get('baseAssetsPrefix'))
    object_key = _asset_base_key(asset_row, base_prefix, asset_id) + file_path.lstrip('/')

    state = resolve_key_state(bucket_name, object_key)
    if not state.exists:
        logger.warning(f"embedding.ready for {object_key} which has no versions left; dropping")
        return Outcome(True, 'drop', 'no versions of the file remain')
    # Both flags are bools: VectorItem rejects any other type and the store writes their "true"/"false" form.
    is_latest = state.current_version_id == version_id
    is_archived = bool(state.archived or asset_archived)

    attrs = dict(
        databaseId=database_id,
        assetId=asset_id,
        filePath=file_path,
        versionId=version_id,
        isLatest=is_latest,
        isArchived=is_archived,
        fileClass=str(detail.get('fileClass') or document.get('fileClass') or 'other'),
        fileExt=_file_ext(_declared(detail, document, 'fileExt'), file_path),
        embeddingModelId=embedding_model_id,
        embeddingDimensions=embedding_dimensions,
        embedding=round_vector([float(v) for v in embedding]),
        sourceText=str(document.get('sourceText') or '')[:SOURCE_TEXT_STORED_CHARS],
        sourceModalities=[str(m) for m in (detail.get('sourceModalities') or document.get('sourceModalities') or [])],
        contentEtag=str(detail.get('contentEtag') or document.get('contentEtag') or ''),
        bucketId=bucket_id,
        fileSize=int(detail.get('fileSize') or document.get('fileSize') or 0),
        contentType=str(detail.get('contentType') or document.get('contentType') or ''),
        previewFileKey=find_preview_file_key(bucket_name, object_key),
        analysisModelId=str(detail.get('analysisModelId') or document.get('analysisModelId') or ''),
        indexedAt=datetime.now(timezone.utc).isoformat(),
        pipelineExecutionId=str(detail.get('pipelineExecutionId') or ''),
        workflowExecutionId=str(detail.get('workflowExecutionId') or ''),
        **segment,
    )
    # The dataclass re-checks the segment fields and the assembled sort key's length; its ValueError is a
    # permanent publisher fault and drops. Its TypeError (a non-bool flag, an attribute name it does not
    # know) is an indexer fault and propagates, so the record fails to the DLQ instead of vanishing.
    try:
        item = VectorItem(**attrs)
    except ValueError as e:
        logger.warning(f"embedding.ready for {file_path}#{version_id} builds an item VectorItem refuses ({e}); dropping")
        return Outcome(True, 'drop', f"VectorItem rejected the document: {e}")
    # A latest whole-file document goes through the store's transactional write, which demotes the file's
    # latest items of OTHER versions in batches of DEMOTION_BATCH_ACTIONS (the put rides in the first).
    # Every other document is a plain put: a non-latest version has nothing to demote, and a segment
    # document leaves the flip to the run's whole-file document (published first) and to the S3
    # ObjectCreated rule, so a 1,000-chunk document costs 1,000 writes rather than 1,000 sibling reads.
    whole_file = segment['segmentKind'] == SEGMENT_KIND_NONE
    pk = f"{database_id}:{asset_id}"
    key_path = vector_file_key_path(file_path)
    realigned = 0
    if whole_file and is_latest:
        flipped = vector_store.put_latest_item(item)
        # The put demoted the file's latest items of every other version, including those of a version
        # uploaded after the state read above (documents of concurrent runs are written in any order). The
        # key is read again: when the current version has moved on, the file's latest marks are aligned to
        # it -- this item and the rest read "false", the current version's items read "true".
        after = resolve_key_state(bucket_name, object_key)
        if after.exists and after.current_version_id not in (None, version_id):
            realigned = vector_store.set_latest_for_file_version(pk, key_path, after.current_version_id)
            logger.info(f"{file_path}#{version_id} was superseded by a newer version while being indexed; "
                        f"realigned {realigned} latest marks")
    else:
        vector_store.put_item(item)
        flipped = 0
    # The whole-file document of a run sweeps the version's segment items an earlier run left behind
    # (another interval or chunking setting); the current run's own segments are never touched. The sweep
    # assumes a run's whole-file document arrives before any LATER run's segments for the same version: the
    # perInputFileVersion lock serialises runs per version, so that holds unless an embedding.ready event is
    # delayed past an entire subsequent run, in which case that run's segments are swept and the next run
    # rewrites them.
    stale_segments = 0
    if whole_file:
        stale_segments = vector_store.delete_other_run_segments(pk, key_path, version_id, item.pipelineExecutionId)
        if stale_segments:
            logger.info(f"Removed {stale_segments} segment items of {file_path}#{version_id} left by an earlier run")
    s3_client.delete_object(Bucket=aux_bucket_name, Key=doc_key)
    return Outcome(True, 'put',
                   f"{database_id}:{asset_id}{sort_key} latest={str(is_latest).lower()} "
                   f"archived={str(is_archived).lower()} flipped={flipped} realigned={realigned} "
                   f"staleSegments={stale_segments}")


# --- S3 lifecycle rules ------------------------------------------------------------------------------

def _split_asset_key(key: str, base_prefix: str) -> Tuple[Optional[str], Optional[str]]:
    """(assetId, asset-relative path) for a key under a registered prefix, or (None, None) when the key
    has no path below the asset folder. The remainder after the prefix is split, never the raw key."""
    remainder = key[len(base_prefix):] if base_prefix and key.startswith(base_prefix) else key
    parts = remainder.lstrip('/').split('/')
    if len(parts) < 2 or not parts[0]:
        return None, None
    return parts[0], '/' + '/'.join(parts[1:])


def _lookup_database_id(asset_id: str, bucket_name: str, base_prefix: str) -> Optional[str]:
    """The live databaseId of the one asset with this id whose bucket registration is the event's bucket
    and prefix. assetId is not unique across databases, so an unverified match would attribute the file
    to another database's asset."""
    rows = query_all_items(asset_storage_table, IndexName='assetIdGSI',
                           KeyConditionExpression=Key('assetId').eq(asset_id))
    matches = set()
    for row in rows:
        bucket_record = _get_bucket_record(str(row.get('bucketId') or ''))
        if not bucket_record:
            continue
        if bucket_record.get('bucketName') == bucket_name and \
                _normalize_prefix(bucket_record.get('baseAssetsPrefix')) == base_prefix:
            database_id = str(row.get('databaseId') or '')
            if database_id.endswith(ARCHIVED_PARTITION_SUFFIX):
                database_id = database_id[:-len(ARCHIVED_PARTITION_SUFFIX)]
            if database_id:
                matches.add(database_id)
    if len(matches) != 1:
        logger.warning(f"assetId {asset_id} resolves to {len(matches)} databases for bucket "
                       f"{bucket_name}/{base_prefix}; cannot attribute the file")
        return None
    return matches.pop()


def _resolve_file_identity(bucket_name: str, key: str,
                           prefix: Optional[str]) -> Optional[Tuple[str, str, str, str]]:
    """(databaseId, assetId, filePath, changeSource) for an object key: from the object's own metadata
    while the object is readable, else from the key and assetIdGSI (the object is gone on a permanent
    delete). ``changeSource`` is the object's ``vams-changesource`` provenance, '' when the object is
    unreadable or carries none; it comes from the same HEAD, so no second read is made."""
    database_id = asset_id = None
    change_source = ''
    try:
        metadata = s3_client.head_object(Bucket=bucket_name, Key=key).get('Metadata', {}) or {}
        database_id = metadata.get(DATABASE_ID_METADATA_KEY)
        asset_id = metadata.get(ASSET_ID_METADATA_KEY)
        change_source = str(metadata.get(VAMS_CHANGE_SOURCE_METADATA_KEY) or '')
    except ClientError as e:
        if not _is_not_found(e):
            raise
    base_prefix = _normalize_prefix(prefix)
    key_asset_id, key_relative = _split_asset_key(key, base_prefix)
    asset_id = asset_id or key_asset_id
    if not asset_id:
        return None
    if not database_id:
        database_id = _lookup_database_id(asset_id, bucket_name, base_prefix)
    if not database_id:
        return None
    asset_row, _ = _get_asset_row(database_id, asset_id)
    location = ((asset_row or {}).get('assetLocation') or {}).get('Key') if asset_row else None
    if location and key.startswith(location):
        file_path = '/' + key[len(location):].lstrip('/')
    else:
        file_path = key_relative or ''
    if file_path in ('', '/'):
        return None
    return database_id, asset_id, file_path, change_source


def run_object_created(pk: str, key_path: str, version_id: str, start_key: Optional[Dict[str, Any]],
                       time_left: Callable[[], int]) -> Outcome:
    """A new live version: every other version's items become not-latest, then the file is un-archived.
    When the demotion hands off, the un-archive step waits for the continuation that finishes it."""
    flipped, next_key = vector_store.set_not_latest_for_file_except(
        pk, key_path, version_id, start_key=start_key, time_remaining_fn=time_left)
    if next_key is not None:
        note = _continue_later(RULE_OBJECT_CREATED, pk, next_key, key_path=key_path, version_id=version_id)
        return Outcome(True, 'created', f"{pk}{key_path}: notLatest={flipped}{note}")
    unarchived, next_key = vector_store.set_archived_for_file(
        pk, key_path, False, time_remaining_fn=time_left)
    note = ''
    if next_key is not None:
        note = _continue_later(RULE_SET_ARCHIVED_FOR_FILE, pk, next_key, key_path=key_path, archived=False)
    return Outcome(True, 'created', f"{pk}{key_path}: notLatest={flipped} unarchived={unarchived}{note}")


def run_set_archived_for_file(pk: str, key_path: str, archived: bool, start_key: Optional[Dict[str, Any]],
                              time_left: Callable[[], int]) -> Outcome:
    changed, next_key = vector_store.set_archived_for_file(
        pk, key_path, archived, start_key=start_key, time_remaining_fn=time_left)
    note = ''
    if next_key is not None:
        note = _continue_later(RULE_SET_ARCHIVED_FOR_FILE, pk, next_key, key_path=key_path, archived=archived)
    action = 'archived' if archived else 'unarchived'
    return Outcome(True, action, f"{pk}{key_path}: {action}={changed}{note}")


def run_delete_file(pk: str, key_path: str, start_key: Optional[Dict[str, Any]],
                    time_left: Callable[[], int]) -> Outcome:
    deleted, next_key = vector_store.delete_file(
        pk, key_path, start_key=start_key, time_remaining_fn=time_left)
    note = ''
    if next_key is not None:
        note = _continue_later(RULE_DELETE_FILE, pk, next_key, key_path=key_path)
    return Outcome(True, 'deleted', f"{pk}{key_path}: deleted={deleted}{note}")


def handle_s3_record(record: Dict[str, Any], bucket_name_hint: Optional[str],
                     bucket_prefix_hint: Optional[str],
                     time_left: Optional[Callable[[], int]] = None) -> Outcome:
    time_left = time_left or (lambda: FULL_BUDGET_MS)
    s3_info = record.get('s3', {}) or {}
    bucket_name = s3_info.get('bucket', {}).get('name') or record.get('ASSET_BUCKET_NAME') or bucket_name_hint
    key = urllib.parse.unquote_plus(s3_info.get('object', {}).get('key') or '')
    event_name = record.get('eventName', '') or ''
    prefix = record.get('ASSET_BUCKET_PREFIX', bucket_prefix_hint)
    if not bucket_name or not key:
        return Outcome(True, 'ignore', 'record without bucket or key')
    if key.endswith('/'):
        return Outcome(True, 'ignore', 'folder marker')
    if key_has_reserved_segment(key, prefix or ''):
        return Outcome(True, 'ignore', 'reserved segment')
    if PREVIEW_FILE_PATTERN in key:
        return Outcome(True, 'ignore', 'preview file')

    identity = _resolve_file_identity(bucket_name, key, prefix)
    if identity is None:
        logger.warning(f"S3 record for {bucket_name}/{key} resolves to no asset; nothing to update")
        return Outcome(True, 'ignore', 'file identity unresolved')
    database_id, asset_id, file_path, change_source = identity
    pk = f"{database_id}:{asset_id}"
    key_path = vector_file_key_path(file_path)

    if event_name.startswith('ObjectCreated'):
        if change_source in VAMS_CHANGE_SOURCE_RESTORE_VALUES:
            # An unarchive copies the file's newest content version forward under a new version id. The
            # items of that content are the file's latest ones already, and no run will embed the copy,
            # so they keep their latest marks and only their archived marks are cleared: the file is
            # searchable the moment the flip lands, with exactly one latest item per segment as before.
            logger.info(f"{pk}{key_path}: ObjectCreated by '{change_source}' restores stored content; "
                        "un-archiving the file's items without demoting them")
            return run_set_archived_for_file(pk, key_path, False, None, time_left)
        version_id = s3_info.get('object', {}).get('versionId') or 'null'
        return run_object_created(pk, key_path, version_id, None, time_left)
    if event_name == 'ObjectRemoved:DeleteMarkerCreated':
        return run_set_archived_for_file(pk, key_path, True, None, time_left)
    if event_name.startswith('ObjectRemoved'):
        state = resolve_key_state(bucket_name, key)
        if not state.exists:
            return run_delete_file(pk, key_path, None, time_left)
        if not state.archived:
            # Marker removed (file or asset unarchive): live S3 state is authoritative for the flag.
            return run_set_archived_for_file(pk, key_path, False, None, time_left)
        return Outcome(True, 'ignore', 'a delete marker is still current')
    return Outcome(True, 'ignore', f"unhandled S3 event {event_name}")


# --- Asset table stream rules -------------------------------------------------------------------------

def run_set_archived_for_asset(pk: str, archived: bool, start_key: Optional[Dict[str, Any]],
                               time_left: Callable[[], int]) -> Outcome:
    marked, next_key = vector_store.set_archived_for_asset(
        pk, archived, start_key=start_key, time_remaining_fn=time_left)
    note = ''
    if next_key is not None:
        note = _continue_later(RULE_SET_ARCHIVED_FOR_ASSET, pk, next_key, archived=archived)
    word = 'archived' if archived else 'unarchived'
    return Outcome(True, f"asset-{word}", f"{pk}: {word}={marked}{note}")


def run_delete_asset(pk: str, start_key: Optional[Dict[str, Any]], time_left: Callable[[], int]) -> Outcome:
    deleted, next_key = vector_store.delete_asset(pk, start_key=start_key, time_remaining_fn=time_left)
    note = ''
    if next_key is not None:
        note = _continue_later(RULE_DELETE_ASSET, pk, next_key)
    return Outcome(True, 'asset-deleted', f"{pk}: deleted={deleted}{note}")


def handle_stream_record(record: Dict[str, Any], time_left: Optional[Callable[[], int]] = None) -> Outcome:
    """Asset table stream records only; every other table's record (metadata, attribute, links, the
    REINDEX_METADATA_RECORD marker) carries no vector state."""
    time_left = time_left or (lambda: FULL_BUDGET_MS)
    source_arn = record.get('eventSourceARN', '') or ''
    if asset_storage_table_name not in source_arn:
        return Outcome(True, 'ignore', 'not an asset table stream record')
    event_name = record.get('eventName', '') or ''
    stream_data = record.get('dynamodb', {}) or {}
    image = stream_data.get('NewImage') or {}
    keys = stream_data.get('Keys') or {}
    database_key = (image.get('databaseId') or keys.get('databaseId') or {}).get('S')
    asset_id = (image.get('assetId') or keys.get('assetId') or {}).get('S')
    if not database_key or not asset_id:
        return Outcome(True, 'ignore', 'stream record without keys')

    in_deleted_partition = database_key.endswith(ARCHIVED_PARTITION_SUFFIX)
    database_id = database_key[:-len(ARCHIVED_PARTITION_SUFFIX)] if in_deleted_partition else database_key
    pk = f"{database_id}:{asset_id}"

    if event_name == 'INSERT' and in_deleted_partition:
        return run_set_archived_for_asset(pk, True, None, time_left)
    if event_name == 'REMOVE':
        row, archived = _get_asset_row(database_id, asset_id)
        if row is not None and not archived:
            # The archived copy is being removed by an unarchive; the files keep their own markers.
            return Outcome(True, 'ignore', 'asset row is live')
        if row is not None and archived:
            return run_set_archived_for_asset(pk, True, None, time_left)
        return run_delete_asset(pk, None, time_left)
    partition = 'archived' if in_deleted_partition else 'live'
    return Outcome(True, 'ignore', f"{event_name} on the {partition} partition changes no vector state")


def handle_continuation(message: Dict[str, Any], time_left: Callable[[], int]) -> Outcome:
    """Resume an asset- or file-wide rule from the exclusive start key a previous invocation stopped at.
    The message is this function's own, so a malformed one is a bug to log, not a record to retry."""
    rule = message.get('rule')
    pk = str(message.get('pk') or '')
    start_key = message.get('startKey')
    if not pk or not isinstance(start_key, dict) or not start_key:
        logger.warning(f"continuation for {rule!r} carries no pk or startKey; dropping")
        return Outcome(True, 'drop', 'continuation without pk or startKey')
    key_path = str(message.get('keyPath') or '')
    if rule in FILE_RULES and not key_path:
        logger.warning(f"continuation for {rule!r} on {pk} carries no keyPath; dropping")
        return Outcome(True, 'drop', 'file continuation without keyPath')
    if rule == RULE_OBJECT_CREATED:
        return run_object_created(pk, key_path, str(message.get('versionId') or 'null'), start_key, time_left)
    if rule == RULE_SET_ARCHIVED_FOR_FILE:
        return run_set_archived_for_file(pk, key_path, bool(message.get('archived')), start_key, time_left)
    if rule == RULE_DELETE_FILE:
        return run_delete_file(pk, key_path, start_key, time_left)
    if rule == RULE_SET_ARCHIVED_FOR_ASSET:
        return run_set_archived_for_asset(pk, bool(message.get('archived')), start_key, time_left)
    if rule == RULE_DELETE_ASSET:
        return run_delete_asset(pk, start_key, time_left)
    logger.warning(f"continuation names an unknown rule {rule!r}; dropping")
    return Outcome(True, 'drop', f"unknown continuation rule {rule!r}")


# --- Envelope unwrapping ------------------------------------------------------------------------------

def _dispatch_sns_message(sns_message: Dict[str, Any], bucket_name: Optional[str],
                          bucket_prefix: Optional[str], time_left: Callable[[], int]) -> List[Outcome]:
    """Route one SNS `Message`: a raw DynamoDB stream record, or the bucket-sync envelope of S3 records."""
    if sns_message.get('eventSource') == 'aws:dynamodb' or \
            sns_message.get('eventName') in ('INSERT', 'MODIFY', 'REMOVE'):
        return [handle_stream_record(sns_message, time_left)]
    if 'Records' not in sns_message:
        logger.warning(f"SNS message has no recognized shape: {sorted(sns_message.keys())}")
        return [Outcome(True, 'ignore', 'unrecognized SNS message shape')]

    outcomes: List[Outcome] = []
    outer_bucket = sns_message.get('ASSET_BUCKET_NAME') or bucket_name
    outer_prefix = sns_message.get('ASSET_BUCKET_PREFIX')
    if outer_prefix is None:
        outer_prefix = bucket_prefix
    for inner in sns_message['Records']:
        source = inner.get('eventSource', '')
        if source == 'aws:s3':
            outcomes.append(handle_s3_record(inner, outer_bucket, outer_prefix, time_left))
        elif source == 'aws:sqs':
            inner_body = _parse_json(inner.get('body'))
            if inner_body.get('Type') != 'Notification' or not inner_body.get('Message'):
                logger.warning("Nested SQS record is not an SNS notification; ignoring")
                continue
            inner_message = _parse_json(inner_body['Message'])
            nested_bucket = inner_message.get('ASSET_BUCKET_NAME', outer_bucket)
            nested_prefix = inner_message.get('ASSET_BUCKET_PREFIX', outer_prefix)
            for s3_record in inner_message.get('Records', []):
                if s3_record.get('eventSource') == 'aws:s3':
                    outcomes.append(handle_s3_record(s3_record, nested_bucket, nested_prefix, time_left))
        else:
            logger.warning(f"Unknown inner record event source: {source}")
    return outcomes


def _dispatch_sqs_record(record: Dict[str, Any], time_left: Callable[[], int]) -> List[Outcome]:
    body = _parse_json(record.get('body'))
    if body.get('Type') == 'Notification' and body.get('Message'):
        return _dispatch_sns_message(_parse_json(body['Message']), None, None, time_left)
    if body.get('detailType') == CONTINUE_DETAIL_TYPE:
        return [handle_continuation(body, time_left)]
    if body.get('detail-type') == EMBEDDING_READY_DETAIL_TYPE:
        return [handle_embedding_ready(body.get('detail') or {})]
    logger.warning(f"SQS body has no recognized shape: {sorted(body.keys())}")
    return [Outcome(True, 'ignore', 'unrecognized SQS body')]


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    try:
        records = event.get('Records') if isinstance(event, dict) else None
        if records is None:
            logger.warning("Event carries no Records; nothing to index")
            return success(body={'message': 'No records', 'results': []})

        time_left = _time_left(context)
        top_bucket = event.get('ASSET_BUCKET_NAME')
        top_prefix = event.get('ASSET_BUCKET_PREFIX', '/')
        outcomes: List[Outcome] = []
        failures: List[Dict[str, str]] = []
        for record in records:
            source = record.get('eventSource', '')
            try:
                if source == 'aws:sqs':
                    record_outcomes = _dispatch_sqs_record(record, time_left)
                elif source == 'aws:s3':
                    record_outcomes = [handle_s3_record(record, top_bucket, top_prefix, time_left)]
                elif source == 'aws:dynamodb':
                    record_outcomes = [handle_stream_record(record, time_left)]
                else:
                    logger.warning(f"Unknown event source: {source}")
                    record_outcomes = [Outcome(True, 'ignore', f'unknown event source {source}')]
            except Exception as e:
                logger.exception(f"Error processing vector index record: {e}")
                record_outcomes = [Outcome(False, 'error', str(e))]
            outcomes.extend(record_outcomes)
            if any(not o.ok for o in record_outcomes):
                identifier = batch_item_identifier(record)
                if identifier:
                    failures.append({'itemIdentifier': identifier})
                else:
                    logger.warning(
                        "A failed record carries no messageId; it cannot be reported for redrive")

        body = {
            'message': f"Processed {sum(1 for o in outcomes if o.ok)}/{len(outcomes)} vector index operations",
            'results': [{'ok': o.ok, 'action': o.action, 'detail': o.detail} for o in outcomes],
        }
        return with_batch_item_failures(success(body=body), event, failures)
    except Exception as e:
        logger.exception(f"Internal error in vector indexer: {e}")
        # Not attributable to one record: report the whole batch, or an error response deletes it.
        return with_batch_item_failures(internal_error(), event, all_batch_item_failures(event))
