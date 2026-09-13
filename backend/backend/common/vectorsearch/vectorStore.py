# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The DynamoDB vector table behind a small store interface.

One item per (file, S3 version) under PK ``databaseId:assetId`` and SK ``fileVersionKey``, plus one
item per segment (a video time window or a text chunk) a run published for that version, keyed
``{keyPath}#{versionId}#{segmentKey}`` under the same partition so every per-file rule covers a file's
segments by prefix. The vector index over ``embedding`` carries seven INLINE_FILTER attributes --
``databaseId``, ``isLatest``, ``isArchived``, ``fileClass``, ``fileExt``, ``embeddingModelId``,
``segmentKind`` -- all strings and all written on every item: a filter attribute cannot be a BOOL, and
an item missing one is silently absent from filtered searches. Filters are equality-only,
``SearchVectors`` returns at most 100 results with no cursor, and the call is served from a dedicated
search endpoint. A new latest version and the demotion of the file's latest-marked items of other
versions go through ``put_latest_item``: a plain ``PutItem`` when nothing needs demoting, otherwise
``TransactWriteItems`` of at most DEMOTION_BATCH_ACTIONS actions -- the ``Put`` and the first 24
conditional demotions together, the rest in batches of 25 -- sized to the write throughput of the one
partition an asset's items share; until the last batch commits an older segmented version's remaining
chunks read as latest beside the new version. The file-wide and asset-wide walks (the archive flips,
the S3 ObjectCreated demotion, the deletes) page under a caller-supplied time budget and hand back the
cursor to resume from, so an item collection of any size is processed across invocations. The
low-level client is used throughout because neither the vector operations nor the ``L``-of-``N``
embedding attribute are modelled by the resource layer.
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from common.indexing.documentIds import (
    SEGMENT_KEY_MAX_BYTES,
    SEGMENT_KINDS,
    build_vector_file_version_key,
    vector_file_key_path,
)
from customLogging.logger import safeLogger

logger = safeLogger(service_name="VectorStore")

PARTITION_KEY = "databaseId:assetId"
SORT_KEY = "fileVersionKey"
INLINE_FILTER_ATTRIBUTES = (
    "databaseId", "isLatest", "isArchived", "fileClass", "fileExt", "embeddingModelId", "segmentKind",
)
MAX_TOP_K = 100
EMBEDDING_SIGNIFICANT_DIGITS = 9
BATCH_WRITE_MAX_ITEMS = 25
BATCH_WRITE_MAX_ATTEMPTS = 5
BATCH_WRITE_BACKOFF_BASE_SECONDS = 0.1
# Actions per TransactWriteItems on the isLatest write path: 25 items of up to 10 KB are about 500 WCU,
# half the 1,000 WCU/s of the one partition an asset's items share.
DEMOTION_BATCH_ACTIONS = 25
TRANSACT_MAX_ATTEMPTS = 3
TRANSACT_BACKOFF_BASE_SECONDS = 0.2
# Per-item updates of a file-wide or asset-wide walk run on this many threads.
FLIP_WORKERS = 8
# Substrings of the ValidationException message DynamoDB returns while a vector index is still being
# built or has not yet become searchable.
INDEX_NOT_READY_MARKERS = ("does not have the specified index", "backfill", "not active", "being created")

# CancellationReasons codes of a TransactWriteItems the partition refused for want of throughput.
_THROTTLE_CANCELLATION_CODES = ("ThrottlingError", "ProvisionedThroughputExceeded")

_KEY_NAMES = {"#pk": PARTITION_KEY, "#sk": SORT_KEY}
_KEY_PROJECTION = "#pk, #sk"
_FILE_ITEM_PROJECTION = "#pk, #sk, versionId, isLatest, isArchived"
_SEGMENT_ITEM_PROJECTION = "#pk, #sk, pipelineExecutionId"
# The file's latest-marked items of other versions. The item's own version is excluded whole: a
# segment document leaves its version's whole-file item and sibling segments alone, and a redelivered
# event never puts two actions on one item, which a transaction may not carry.
_LATEST_SIBLINGS_FILTER = "isLatest = :latest AND versionId <> :v"

_deserializer = TypeDeserializer()


class VectorStoreError(Exception):
    """A vector-store operation that could not be completed."""


class VectorIndexNotReady(VectorStoreError):
    """``SearchVectors`` was refused because the vector index is still being built."""


class VectorModelMismatch(VectorStoreError):
    """An item embedded with a model or dimension count other than the configured index's."""


def build_partition_key(database_id: str, asset_id: str) -> str:
    return f"{database_id}:{asset_id}"


def number_string(value: float) -> str:
    """The DynamoDB ``N`` string of ``value`` rounded to EMBEDDING_SIGNIFICANT_DIGITS significant digits."""
    rounded = format(float(value), f".{EMBEDDING_SIGNIFICANT_DIGITS}g")
    if float(rounded) == 0:
        return "0"
    return str(Decimal(rounded))


@dataclass
class VectorItem:
    """One embedded file version, or one segment of it. Field names are the stored attribute names;
    ``isLatest`` and ``isArchived`` are bools here and the strings ``"true"``/``"false"`` in the table.
    The segment fields default to the whole-file item's values (``""``, ``"none"``, ``""``, ``None``,
    ``None``, ``0``); a segment item sets ``segmentKey`` and ``segmentKind`` together."""

    databaseId: str
    assetId: str
    filePath: str
    versionId: str
    isLatest: bool
    isArchived: bool
    fileClass: str
    fileExt: str
    embeddingModelId: str
    embeddingDimensions: int
    embedding: List[float]
    sourceText: str
    sourceModalities: List[str]
    contentEtag: str
    bucketId: str
    fileSize: int
    contentType: str
    analysisModelId: str
    indexedAt: str
    pipelineExecutionId: str
    workflowExecutionId: str
    previewFileKey: Optional[str] = None
    segmentKey: str = ""
    segmentKind: str = "none"
    segmentLabel: str = ""
    segmentStartMs: Optional[int] = None
    segmentEndMs: Optional[int] = None
    segmentCount: int = 0

    def __post_init__(self):
        for flag in ("isLatest", "isArchived"):
            value = getattr(self, flag)
            if not isinstance(value, bool):
                raise TypeError(
                    f"{flag} must be a bool, got {value!r}; the strings 'true'/'false' are the stored form, "
                    "and a truthy 'false' would be written as 'true'"
                )
        self.fileExt = self.fileExt or "none"
        self.versionId = self.versionId or "null"
        if self.segmentKind not in SEGMENT_KINDS:
            raise ValueError(f"segmentKind {self.segmentKind!r} is not one of {SEGMENT_KINDS}")
        if len(self.segmentKey.encode("utf-8")) > SEGMENT_KEY_MAX_BYTES or "#" in self.segmentKey:
            raise ValueError(f"segmentKey {self.segmentKey!r} exceeds {SEGMENT_KEY_MAX_BYTES} bytes or contains '#'")
        if (self.segmentKey == "") != (self.segmentKind == "none"):
            raise ValueError(
                f"segmentKey {self.segmentKey!r} and segmentKind {self.segmentKind!r} disagree: the whole-file "
                "item has an empty key and kind 'none', a segment item has both"
            )
        # The assembled sort key must fit SORT_KEY_MAX_BYTES; the builder raises ValueError past it.
        build_vector_file_version_key(self.filePath, self.versionId, self.segmentKey)

    @property
    def pk(self) -> str:
        return build_partition_key(self.databaseId, self.assetId)

    @property
    def fileVersionKey(self) -> str:
        return build_vector_file_version_key(self.filePath, self.versionId, self.segmentKey)


@dataclass
class VectorHit:
    """One ``SearchVectors`` result: the projected item as plain Python and its cosine distance."""

    item: Dict[str, Any]
    distance: float


class VectorStore(Protocol):
    def put_item(self, item: VectorItem) -> None: ...
    def put_latest_item(self, item: VectorItem) -> int: ...
    def delete_other_run_segments(self, pk: str, key_path: str, version_id: str, pipeline_execution_id: str) -> int: ...
    def set_archived_for_file(
        self, pk: str, key_path: str, archived: bool, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]: ...
    def set_archived_for_asset(
        self, pk: str, archived: bool, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]: ...
    def set_not_latest_for_file_except(
        self, pk: str, key_path: str, version_id: str, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]: ...
    def delete_file(
        self, pk: str, key_path: str, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]: ...
    def delete_asset(
        self, pk: str, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]: ...
    def search(self, vector: List[float], *, top_k: int, filters: Dict[str, str]) -> List[VectorHit]: ...
    def scan_keys(self, start_key: Optional[dict] = None, limit: int = 1000,
                  pk_prefix: Optional[str] = None) -> Tuple[List[dict], Optional[dict]]: ...
    def delete_keys(self, keys: List[dict]) -> int: ...


def _string_flag(value: bool) -> Dict[str, str]:
    return {"S": "true" if value else "false"}


def _optional_number(value: Optional[int]) -> Dict[str, Any]:
    return {"NULL": True} if value is None else {"N": str(int(value))}


def serialize_item(item: VectorItem) -> Dict[str, Dict[str, Any]]:
    """The low-level image of a VectorItem: both keys, the seven filter attributes as ``S``, counts as
    ``N``, the embedding as ``L`` of ``N`` rounded to nine significant digits, the modalities as ``L``
    of ``S``, the segment fields (``segmentStartMs``/``segmentEndMs`` as ``NULL`` when unset);
    ``previewFileKey`` only when present. ``filePath`` is the plain path; only the sort key encodes."""
    image = {
        PARTITION_KEY: {"S": item.pk},
        SORT_KEY: {"S": item.fileVersionKey},
        "databaseId": {"S": item.databaseId},
        "assetId": {"S": item.assetId},
        "filePath": {"S": item.filePath},
        "versionId": {"S": item.versionId},
        "isLatest": _string_flag(item.isLatest),
        "isArchived": _string_flag(item.isArchived),
        "fileClass": {"S": item.fileClass},
        "fileExt": {"S": item.fileExt},
        "embeddingModelId": {"S": item.embeddingModelId},
        "embeddingDimensions": {"N": str(int(item.embeddingDimensions))},
        "embedding": {"L": [{"N": number_string(value)} for value in item.embedding]},
        "sourceText": {"S": item.sourceText},
        "sourceModalities": {"L": [{"S": modality} for modality in item.sourceModalities]},
        "contentEtag": {"S": item.contentEtag},
        "bucketId": {"S": item.bucketId},
        "fileSize": {"N": str(int(item.fileSize))},
        "contentType": {"S": item.contentType},
        "analysisModelId": {"S": item.analysisModelId},
        "indexedAt": {"S": item.indexedAt},
        "pipelineExecutionId": {"S": item.pipelineExecutionId},
        "workflowExecutionId": {"S": item.workflowExecutionId},
        "segmentKey": {"S": item.segmentKey},
        "segmentKind": {"S": item.segmentKind},
        "segmentLabel": {"S": item.segmentLabel},
        "segmentStartMs": _optional_number(item.segmentStartMs),
        "segmentEndMs": _optional_number(item.segmentEndMs),
        "segmentCount": {"N": str(int(item.segmentCount))},
    }
    if item.previewFileKey:
        image["previewFileKey"] = {"S": item.previewFileKey}
    return image


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, set):
        return sorted(_plain(v) for v in value)
    return value


def deserialize_item(image: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """A plain-Python view of a low-level item: ``N`` becomes int when integral, else float."""
    return {name: _plain(_deserializer.deserialize(value)) for name, value in image.items()}


def key_of(image: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The primary key of a low-level item image."""
    return {PARTITION_KEY: image[PARTITION_KEY], SORT_KEY: image[SORT_KEY]}


def is_index_not_ready(error: ClientError) -> bool:
    """True for the ValidationException DynamoDB returns while the vector index is not yet searchable."""
    info = error.response.get("Error", {})
    if info.get("Code") != "ValidationException":
        return False
    message = str(info.get("Message", "")).lower()
    return any(marker in message for marker in INDEX_NOT_READY_MARKERS)


def _is_throttled_cancellation(error: ClientError) -> bool:
    """True when a TransactionCanceledException names throughput among its CancellationReasons."""
    reasons = error.response.get("CancellationReasons") or []
    return any(reason.get("Code") in _THROTTLE_CANCELLATION_CODES for reason in reasons)


class DynamoDbVectorStore:
    """The VectorStore over one DynamoDB table and one vector index."""

    def __init__(self, table_name: str, index_name: str, model_id: str, dimensions: int, dynamodb_client):
        self._table_name = table_name
        self._index_name = index_name
        self._model_id = model_id
        self._dimensions = int(dimensions)
        self._client = dynamodb_client

    def _query_kwargs(
        self, key_condition: str, values: Dict[str, Any], projection: str, filter_expression: Optional[str] = None
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "TableName": self._table_name,
            "KeyConditionExpression": key_condition,
            "ExpressionAttributeNames": dict(_KEY_NAMES),
            "ExpressionAttributeValues": values,
            "ProjectionExpression": projection,
        }
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression
        return kwargs

    def _file_query(self, pk: str, key_path: str, projection: str) -> Dict[str, Any]:
        return self._query_kwargs(
            "#pk = :pk AND begins_with(#sk, :prefix)",
            {":pk": {"S": pk}, ":prefix": {"S": f"{key_path}#"}},
            projection,
        )

    def _asset_query(self, pk: str, projection: str) -> Dict[str, Any]:
        return self._query_kwargs("#pk = :pk", {":pk": {"S": pk}}, projection)

    def _query(self, kwargs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Every item of a Query, all pages."""
        items: List[Dict[str, Any]] = []
        while True:
            response = self._client.query(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        return items

    def _paged(
        self,
        kwargs: Dict[str, Any],
        handle: Callable[[List[Dict[str, Any]]], int],
        *,
        start_key: Optional[dict],
        time_remaining_fn: Optional[Callable[[], int]],
        min_remaining_ms: int,
    ) -> Tuple[int, Optional[dict]]:
        """Run ``handle`` over the items of every Query page from ``start_key`` on; returns the total
        ``handle`` reported and the cursor to resume from -- the page's LastEvaluatedKey when the time
        budget fell under ``min_remaining_ms`` once that page was processed, None once the walk
        completed. A page is always processed whole before the budget is consulted."""
        if start_key is not None:
            kwargs["ExclusiveStartKey"] = start_key
        count = 0
        while True:
            response = self._client.query(**kwargs)
            count += handle(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return count, None
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            if time_remaining_fn is not None and time_remaining_fn() < min_remaining_ms:
                return count, response["LastEvaluatedKey"]

    def _check_item_matches_index(self, item: VectorItem) -> None:
        if item.embeddingModelId != self._model_id or int(item.embeddingDimensions) != self._dimensions:
            raise VectorModelMismatch(
                f"item embedded with {item.embeddingModelId}/{item.embeddingDimensions}; "
                f"the index is {self._model_id}/{self._dimensions}"
            )
        if len(item.embedding) != self._dimensions:
            raise VectorModelMismatch(
                f"embedding has {len(item.embedding)} values; the index has {self._dimensions} dimensions"
            )

    def put_item(self, item: VectorItem) -> None:
        self._check_item_matches_index(item)
        self._client.put_item(TableName=self._table_name, Item=serialize_item(item))

    def _latest_siblings(self, item: VectorItem) -> List[Dict[str, Any]]:
        """Keys of the file's items of other versions still marked latest."""
        return self._query(self._query_kwargs(
            "#pk = :pk AND begins_with(#sk, :prefix)",
            {
                ":pk": {"S": item.pk},
                ":prefix": {"S": f"{vector_file_key_path(item.filePath)}#"},
                ":latest": {"S": "true"},
                ":v": {"S": item.versionId},
            },
            _KEY_PROJECTION,
            filter_expression=_LATEST_SIBLINGS_FILTER,
        ))

    def _demotion(self, image: Dict[str, Any]) -> Dict[str, Any]:
        """The transaction action that demotes one sibling, conditional on it still reading latest."""
        return {
            "Update": {
                "TableName": self._table_name,
                "Key": key_of(image),
                "UpdateExpression": "SET isLatest = :target",
                "ConditionExpression": "isLatest = :current",
                "ExpressionAttributeValues": {":target": {"S": "false"}, ":current": {"S": "true"}},
            }
        }

    def put_latest_item(self, item: VectorItem) -> int:
        """Write ``item`` as the file's latest version and demote every item of the file's other versions
        still marked latest; returns the number of siblings demoted. With nothing to demote the write is
        a plain PutItem. Otherwise the Put and the demotions go out in TransactWriteItems of at most
        DEMOTION_BATCH_ACTIONS actions -- the Put rides in the first -- each demotion conditional on the
        sibling still reading latest. The item's own version is never touched, so a segment document
        leaves its whole-file item and sibling segments as they are. A cancelled transaction (a sibling
        changed between the read and the write, or the partition refused the batch) re-reads the siblings
        and re-runs the sequence from the Put, TRANSACT_MAX_ATTEMPTS times in all, sleeping
        TRANSACT_BACKOFF_BASE_SECONDS * 2**attempt first when the cancellation names throttling;
        siblings already demoted drop out of the fresh read."""
        if not item.isLatest:
            raise ValueError("put_latest_item writes latest items only; put_item writes an isLatest=false item")
        self._check_item_matches_index(item)
        image = serialize_item(item)
        put = {"Put": {"TableName": self._table_name, "Item": image}}
        demoted = 0
        cancelled: Optional[ClientError] = None
        for attempt in range(TRANSACT_MAX_ATTEMPTS):
            demotions = [self._demotion(sibling) for sibling in self._latest_siblings(item)]
            if not demotions:
                self._client.put_item(TableName=self._table_name, Item=image)
                return demoted
            actions = [put] + demotions
            batches = [
                actions[start : start + DEMOTION_BATCH_ACTIONS]
                for start in range(0, len(actions), DEMOTION_BATCH_ACTIONS)
            ]
            try:
                for batch in batches:
                    self._client.transact_write_items(TransactItems=batch)
                    demoted += sum(1 for action in batch if "Update" in action)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                    raise
                cancelled = e
                if attempt + 1 < TRANSACT_MAX_ATTEMPTS and _is_throttled_cancellation(e):
                    time.sleep(TRANSACT_BACKOFF_BASE_SECONDS * (2 ** attempt))
                continue
            return demoted
        raise VectorStoreError(
            f"the latest-version transaction for {item.fileVersionKey} was cancelled {TRANSACT_MAX_ATTEMPTS} times"
        ) from cancelled

    def _flip_one(self, image: Dict[str, Any], attribute: str, target: str) -> int:
        """Set ``attribute`` to ``target`` on one item when it differs, conditionally on the value read;
        1 when the item changed, 0 when it already read ``target`` or another writer got there first."""
        current = image.get(attribute, {}).get("S")
        if current == target:
            return 0
        values: Dict[str, Any] = {":target": {"S": target}}
        if current is None:
            condition = f"attribute_not_exists({attribute})"
        else:
            condition = f"{attribute} = :current"
            values[":current"] = {"S": current}
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=key_of(image),
                UpdateExpression=f"SET {attribute} = :target",
                ConditionExpression=condition,
                ExpressionAttributeValues=values,
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return 0
            raise
        return 1

    def _flip(self, items: List[Dict[str, Any]], attribute: str, target: str) -> int:
        """Flip every item of one page on FLIP_WORKERS threads; a lost condition is not counted, and any
        other error propagates once the page's in-flight updates have finished."""
        if not items:
            return 0
        with ThreadPoolExecutor(max_workers=FLIP_WORKERS) as pool:
            return sum(pool.map(lambda image: self._flip_one(image, attribute, target), items))

    def set_not_latest_for_file_except(
        self, pk: str, key_path: str, version_id: str, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]:
        def demote_other_versions(items: List[Dict[str, Any]]) -> int:
            others = [image for image in items if image.get("versionId", {}).get("S") != version_id]
            return self._flip(others, "isLatest", "false")

        return self._paged(
            self._file_query(pk, key_path, _FILE_ITEM_PROJECTION), demote_other_versions,
            start_key=start_key, time_remaining_fn=time_remaining_fn, min_remaining_ms=min_remaining_ms,
        )

    def set_archived_for_file(
        self, pk: str, key_path: str, archived: bool, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]:
        target = "true" if archived else "false"
        return self._paged(
            self._file_query(pk, key_path, _FILE_ITEM_PROJECTION),
            lambda items: self._flip(items, "isArchived", target),
            start_key=start_key, time_remaining_fn=time_remaining_fn, min_remaining_ms=min_remaining_ms,
        )

    def set_archived_for_asset(
        self, pk: str, archived: bool, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]:
        target = "true" if archived else "false"
        return self._paged(
            self._asset_query(pk, _FILE_ITEM_PROJECTION),
            lambda items: self._flip(items, "isArchived", target),
            start_key=start_key, time_remaining_fn=time_remaining_fn, min_remaining_ms=min_remaining_ms,
        )

    def delete_file(
        self, pk: str, key_path: str, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]:
        return self._paged(
            self._file_query(pk, key_path, _KEY_PROJECTION), self.delete_keys,
            start_key=start_key, time_remaining_fn=time_remaining_fn, min_remaining_ms=min_remaining_ms,
        )

    def delete_asset(
        self, pk: str, *, start_key: Optional[dict] = None,
        time_remaining_fn: Optional[Callable[[], int]] = None, min_remaining_ms: int = 60_000,
    ) -> Tuple[int, Optional[dict]]:
        return self._paged(
            self._asset_query(pk, _KEY_PROJECTION), self.delete_keys,
            start_key=start_key, time_remaining_fn=time_remaining_fn, min_remaining_ms=min_remaining_ms,
        )

    def delete_other_run_segments(self, pk: str, key_path: str, version_id: str, pipeline_execution_id: str) -> int:
        """Delete the segment items of one file version that a pipeline run other than
        ``pipeline_execution_id`` published; the current run's own segments, whichever order they arrive
        in, are left alone. ``version_id`` is the stored form (``null`` on an unversioned bucket)."""
        segments = self._query(self._query_kwargs(
            "#pk = :pk AND begins_with(#sk, :prefix)",
            {":pk": {"S": pk}, ":prefix": {"S": f"{key_path}#{version_id or 'null'}#"}},
            _SEGMENT_ITEM_PROJECTION,
        ))
        stale = [
            image for image in segments
            if image.get("pipelineExecutionId", {}).get("S") != pipeline_execution_id
        ]
        return self.delete_keys(stale)

    def delete_keys(self, keys: List[dict]) -> int:
        deleted = 0
        for start in range(0, len(keys), BATCH_WRITE_MAX_ITEMS):
            requests = [{"DeleteRequest": {"Key": key_of(key)}} for key in keys[start:start + BATCH_WRITE_MAX_ITEMS]]
            for attempt in range(BATCH_WRITE_MAX_ATTEMPTS):
                response = self._client.batch_write_item(RequestItems={self._table_name: requests})
                unprocessed = response.get("UnprocessedItems", {}).get(self._table_name, [])
                deleted += len(requests) - len(unprocessed)
                if not unprocessed:
                    break
                requests = unprocessed
                if attempt + 1 < BATCH_WRITE_MAX_ATTEMPTS:
                    time.sleep(
                        BATCH_WRITE_BACKOFF_BASE_SECONDS * (2 ** attempt)
                        + random.uniform(0, BATCH_WRITE_BACKOFF_BASE_SECONDS)
                    )
            else:
                raise VectorStoreError(
                    f"{len(requests)} vector deletes were still unprocessed after {BATCH_WRITE_MAX_ATTEMPTS} attempts"
                )
        return deleted

    def scan_keys(self, start_key: Optional[dict] = None, limit: int = 1000,
                  pk_prefix: Optional[str] = None) -> Tuple[List[dict], Optional[dict]]:
        """One page of primary keys; with ``pk_prefix`` only the keys whose partition key begins with it.
        The filter narrows a page after the read, so the returned cursor still walks the whole table."""
        kwargs: Dict[str, Any] = {
            "TableName": self._table_name,
            "ProjectionExpression": _KEY_PROJECTION,
            "ExpressionAttributeNames": dict(_KEY_NAMES),
            "Limit": limit,
        }
        if pk_prefix:
            kwargs["FilterExpression"] = "begins_with(#pk, :prefix)"
            kwargs["ExpressionAttributeValues"] = {":prefix": {"S": pk_prefix}}
        if start_key is not None:
            kwargs["ExclusiveStartKey"] = start_key
        response = self._client.scan(**kwargs)
        return list(response.get("Items", [])), response.get("LastEvaluatedKey")

    def search(self, vector: List[float], *, top_k: int, filters: Dict[str, str]) -> List[VectorHit]:
        if not 1 <= top_k <= MAX_TOP_K:
            raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}, got {top_k}")
        if len(vector) != self._dimensions:
            raise ValueError(f"query vector has {len(vector)} values; the index has {self._dimensions} dimensions")
        unknown = [name for name in filters if name not in INLINE_FILTER_ATTRIBUTES]
        if unknown:
            raise ValueError(f"not among the {len(INLINE_FILTER_ATTRIBUTES)} INLINE_FILTER attributes of the vector index: {unknown}")

        request: Dict[str, Any] = {
            "TableName": self._table_name,
            "IndexName": self._index_name,
            "SearchVector": [{"N": number_string(value)} for value in vector],
            "TopK": top_k,
        }
        if filters:
            request["SearchConditionExpression"] = " AND ".join(f"#{name} = :{name}" for name in filters)
            request["ExpressionAttributeNames"] = {f"#{name}": name for name in filters}
            request["ExpressionAttributeValues"] = {f":{name}": {"S": str(value)} for name, value in filters.items()}
        try:
            response = self._client.search_vectors(**request)
        except ClientError as e:
            if is_index_not_ready(e):
                raise VectorIndexNotReady(str(e)) from e
            raise
        return [
            VectorHit(item=deserialize_item(result.get("Item", {})), distance=float(result["Score"]))
            for result in response.get("SearchResults", [])
        ]
