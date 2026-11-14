# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A latest whole-file write reaches DynamoDB as Query + TransactWriteItems followed by the stale-segment
sweep Query; every other document is one PutItem.

The indexer hands `DynamoDbVectorStore.put_latest_item` a whole-file item whose `isLatest` is `True`; the
store must read the file's latest items OF OTHER VERSIONS and demote them to `isLatest="false"` in the
TransactWriteItems that puts the new item — up to 24 of them; a version holding more is demoted in further
transactions of at most `DEMOTION_BATCH_ACTIONS = 25`, so every fixture here seeds at most one sibling and
expects exactly one transaction. A non-latest whole-file document goes through `put_item`, a plain PutItem,
and the second test pins that no sibling Query or transaction is issued for it. A whole-file document then
calls `delete_other_run_segments`: one Query on the version's segment prefix, whose empty page issues no
delete here. A segment document — latest or not — is a plain PutItem with NO Query at all (the whole-file
document and the S3 ObjectCreated rule own the demotion) and no sweep; the third test pins that. Behaviour
tests use a fake store; these drive the real store through a real botocore client wrapped in `Stubber`, so
botocore validates every parameter NAME the store sends (a typo in a key attribute or expression raises
`ParamValidationError` here rather than live), and `assert_no_pending_responses` proves each operation was
issued. The store's expression TEXT belongs to its own tests; what this file pins is MEANING and WIRE FORM —
a `before-parameter-build` hook captures every request, the sibling filter is resolved placeholder by
placeholder to `versionId <> <the document's versionId>`, the sweep's prefix value is
`{keyPath}#{versionId}#`, and on every written item the two flags are the strings `{"S": "true"}` /
`{"S": "false"}` the bools serialise to (never BOOL). The flags are read from the captured
request rather than passed as Stubber `expected_params`: `expected_params` is an exact whole-parameter
match, so asserting two attributes inside `Item` would mean restating every attribute name `VectorItem`
defines.
"""

import io
import json
import os
import re
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.stub import Stubber

from tests.handlers.osVectorSearch.vectorsearch_support import load_handler

DETAIL = {
    "databaseId": "db1", "assetId": "a1", "filePath": "/model.glb", "versionId": "v2",
    "bucketId": "bucket-guid", "fileClass": "mesh", "fileExt": "glb", "fileSize": 10,
    "contentType": "model/gltf-binary", "embeddingModelId": "amazon.titan-embed-text-v2:0",
    "embeddingDimensions": 4, "sourceModalities": ["text"], "pipelineExecutionId": "pe-1",
    "segmentKey": "", "segmentKind": "none", "segmentLabel": "",
    "segmentStartMs": None, "segmentEndMs": None, "segmentCount": 0,
    "documentS3Location": f"s3://{os.environ['S3_ASSET_AUXILIARY_BUCKET']}/doc.json",
}
DOCUMENT = {**DETAIL, "embedding": [0.1, 0.2, 0.3, 0.4], "sourceText": "text"}
VIDEO_SEGMENT = {"segmentKey": "t0000083456", "segmentKind": "videoTime",
                 "segmentLabel": "00:01:23.456–00:01:33.456",
                 "segmentStartMs": 83456, "segmentEndMs": 93456, "segmentCount": 12}
EMPTY_PAGE = {"Items": [], "Count": 0, "ScannedCount": 0}


def _serve(m, document):
    m.s3_client.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO(json.dumps(document).encode("utf-8"))}


@pytest.fixture
def stubbed():
    real_dynamodb = boto3.client("dynamodb", region_name="us-east-1",
                                 aws_access_key_id="test", aws_secret_access_key="test")
    stubber = Stubber(real_dynamodb)
    captured = []
    # Every DynamoDB request as the store built it: the low-level client is what the store holds, so the
    # params here ARE the typed wire form ({"S": ...}), before botocore serialises them.
    real_dynamodb.meta.events.register(
        "before-parameter-build.dynamodb",
        lambda params, model, **kwargs: captured.append((model.name, dict(params))))

    def factory(name, *args, **kwargs):
        return real_dynamodb if name == "dynamodb" else MagicMock()

    m = load_handler("vectorIndexer", boto_client_factory=factory)
    m.s3_client = MagicMock()
    _serve(m, DOCUMENT)
    m.s3_client.head_object.return_value = {"VersionId": "v2", "Metadata": {}}
    m.s3_client.list_objects_v2.return_value = {"Contents": []}
    m.asset_storage_table = MagicMock()
    m.asset_storage_table.get_item.return_value = {
        "Item": {"databaseId": "db1", "assetId": "a1", "assetLocation": {"Key": "a1/"}}}
    m.s3_asset_buckets_table = MagicMock()
    m.s3_asset_buckets_table.query.return_value = {
        "Items": [{"bucketId": "bucket-guid", "bucketName": "assets", "baseAssetsPrefix": "/"}]}
    return m, stubber, captured


def _requests(captured, operation):
    """The captured params of every request of one operation, in issue order."""
    return [params for name, params in captured if name == operation]


def _resolve(params, token):
    """An expression token -> the literal it stands for: a `:value` placeholder, a `#name` alias, or itself."""
    if token.startswith(":"):
        (value,) = params["ExpressionAttributeValues"][token].values()
        return value
    if token.startswith("#"):
        return params.get("ExpressionAttributeNames", {})[token]
    return token


def _inequalities(params):
    """Every `<attribute> <> <value>` clause of the FilterExpression, resolved by meaning."""
    return {(_resolve(params, left), _resolve(params, right))
            for left, right in re.findall(r"([\w#:]+)\s*<>\s*([\w#:]+)", params.get("FilterExpression", ""))}


def _values(params):
    return [value for entry in params["ExpressionAttributeValues"].values() for value in entry.values()]


# The wire form each bool flag serialises to: a string attribute, never BOOL.
FLAG_WIRE = {True: {"S": "true"}, False: {"S": "false"}}


def _assert_flags(item, is_latest, is_archived):
    assert item["isLatest"] == FLAG_WIRE[is_latest]
    assert item["isArchived"] == FLAG_WIRE[is_archived]


@pytest.mark.unit
def test_latest_whole_file_write_is_query_transact_then_the_segment_sweep_query(stubbed):
    m, stubber, captured = stubbed
    sibling = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/model.glb#v1"},
               "versionId": {"S": "v1"}, "isLatest": {"S": "true"}}
    stubber.add_response("query", {"Items": [sibling], "Count": 1, "ScannedCount": 1})
    stubber.add_response("transact_write_items", {})
    stubber.add_response("query", EMPTY_PAGE)
    with stubber:
        outcome = m.handle_embedding_ready(dict(DETAIL))
        stubber.assert_no_pending_responses()
    assert outcome.ok and outcome.action == "put"
    m.s3_client.delete_object.assert_called_once()
    siblings_query, sweep_query = _requests(captured, "Query")
    # The sibling read is scoped to the file and excludes the document's own version.
    assert "/model.glb#" in _values(siblings_query)
    assert ("versionId", "v2") in _inequalities(siblings_query)
    # One sibling: the Put and one demotion ride in a single transaction.
    (transaction,) = _requests(captured, "TransactWriteItems")
    puts = [action["Put"] for action in transaction["TransactItems"] if "Put" in action]
    updates = [action["Update"] for action in transaction["TransactItems"] if "Update" in action]
    assert len(puts) == 1 and len(updates) == 1
    assert puts[0]["Item"]["fileVersionKey"] == {"S": "/model.glb#v2"}
    _assert_flags(puts[0]["Item"], is_latest=True, is_archived=False)
    assert updates[0]["Key"] == {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/model.glb#v1"}}
    # The sweep reads the version's segment prefix; its empty page issues no delete.
    assert "/model.glb#v2#" in _values(sweep_query)


@pytest.mark.unit
def test_non_latest_write_is_a_plain_put_item_then_the_segment_sweep_query(stubbed):
    m, stubber, captured = stubbed
    m.s3_client.head_object.return_value = {"VersionId": "v3", "Metadata": {}}
    stubber.add_response("put_item", {})
    stubber.add_response("query", EMPTY_PAGE)
    with stubber:
        outcome = m.handle_embedding_ready(dict(DETAIL))
        stubber.assert_no_pending_responses()
    assert outcome.ok and outcome.action == "put"
    (put,) = _requests(captured, "PutItem")
    assert put["Item"]["fileVersionKey"] == {"S": "/model.glb#v2"}
    # The item that must read as not-latest: a "false" stored truthy would be the whole defect.
    _assert_flags(put["Item"], is_latest=False, is_archived=False)
    (sweep_query,) = _requests(captured, "Query")
    assert "/model.glb#v2#" in _values(sweep_query)
    m.s3_client.delete_object.assert_called_once()


@pytest.mark.unit
def test_a_latest_segment_document_is_one_put_item_with_no_query(stubbed):
    m, stubber, captured = stubbed
    _serve(m, {**DOCUMENT, **VIDEO_SEGMENT})
    stubber.add_response("put_item", {})
    with stubber:
        outcome = m.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT})
        stubber.assert_no_pending_responses()
    assert outcome.ok and outcome.action == "put"
    # No sibling Query (the whole-file document and the S3 ObjectCreated rule own the flip), no sweep.
    assert _requests(captured, "Query") == []
    assert _requests(captured, "TransactWriteItems") == []
    (put,) = _requests(captured, "PutItem")
    assert put["Item"]["fileVersionKey"] == {"S": "/model.glb#v2#t0000083456"}
    assert put["Item"]["segmentKind"] == {"S": "videoTime"}
    _assert_flags(put["Item"], is_latest=True, is_archived=False)
