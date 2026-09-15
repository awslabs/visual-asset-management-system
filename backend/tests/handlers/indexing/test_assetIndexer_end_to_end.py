# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end asset indexing: only the OpenSearch client and the DynamoDB tables are stubbed.

The neighbouring files assert the two contracts one function at a time --
`test_indexer_batch_failure_reporting.py` patches `handle_asset_stream` and
`test_indexer_query_paging.py` calls `get_asset_relationship_flags` directly. Both
stop short of the chain the defects were actually reported against, so this file
drives `lambda_handler` with a real SQS/SNS-wrapped stream record and reads the
result off the document handed to OpenSearch:

* S2-BACKEND-031 -- `index_asset_document` converts every OpenSearch exception into
  `False`, and `process_asset_index_request` turns that into
  `IndexOperationResponse(success=False)`. Unless the handler reports the record in
  `batchItemFailures`, the event-source mapping reads the 200 as a whole-batch
  success and SQS deletes the message: the asset is never re-indexed. Covered from
  the raising client rather than from a stubbed `handle_asset_stream`, which is the
  finding's own suggested test and the only form that exercises the swallow.

* S2-BACKEND-032 -- relationship flags and MD_ metadata are read through paged
  queries whose FilterExpression DynamoDB applies after the 1 MB page. Asserting the
  flag on `get_asset_relationship_flags` leaves `build_asset_document` unproven, so
  here the assertion is on `bool_has_asset_children` / `MD_` in the body the client
  received.

The page scripts are served by the shared `backend.tests.pagingStub` helpers, whose
failure derives from `BaseException`. That is load-bearing here rather than a detail:
`get_asset_relationship_flags` and `get_asset_metadata` both catch `Exception` and
degrade to all-flags-False / empty metadata, so a stub that failed with an ordinary
assertion would be swallowed and a walk that over-read or resumed from a cursor it was
never handed would read as a plausible document instead of an error.
"""

import importlib.util
import json
import os
import sys
import types
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.pagingStub import Pager, PagingLoopDidNotTerminate

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-links-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}

_INDEXING_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing"
)

# The ids must satisfy the real validators: databaseId is the ID pattern, assetId the
# filename pattern. `validate()` runs for real inside process_asset_index_request.
DATABASE_ID = "smoke-db"
ASSET_ID = "asset1"


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


def _load_asset_indexer():
    """Load the real assetIndexer module by file path with boto3/SSM stubbed."""
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz")}
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub
    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetIndexer_e2e_under_test",
                os.path.abspath(os.path.join(_INDEXING_DIR, "assetIndexer.py")),
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


@pytest.fixture
def asset_indexer():
    return _load_asset_indexer()


#######################
# Stubs
#######################


def _filter_value(condition):
    """The right-hand value of an `Attr(...).eq(...)` FilterExpression, or None."""
    if condition is None:
        return None
    return condition.get_expression()["values"][1]


class _PagedTable:
    """A table stub routing each read to its own shared `Pager`, keyed on (IndexName, filter value).

    Only the ROUTE is local: `pagingStub.RoutedPager` routes on a single read kwarg, and
    these reads are distinguished by IndexName and relationshipType together. The paging
    behaviour itself comes from `Pager`, so the guarantees are the shared ones -- pages are
    served by cursor, which is what lets one batch index several assets with each walk
    starting from the front, and an over-read or an unhanded cursor raises
    `PagingLoopDidNotTerminate` rather than an `Exception` the handler would swallow.
    """

    def __init__(self, scripts):
        self._pagers = scripts
        for key, pager in scripts.items():
            pager.name = f"{key[0]} {key[1]}" if key[1] else str(key[0])

    def query(self, **kwargs):
        key = (kwargs.get("IndexName"), _filter_value(kwargs.get("FilterExpression")))
        pager = self._pagers.get(key)
        if pager is None:
            raise PagingLoopDidNotTerminate(
                f"unrouted read: {key!r} is not one of {sorted(self._pagers, key=repr)}")
        return pager(**kwargs)

    def assert_paged_to_exhaustion(self, *keys):
        """Assert each named route resumed from every cursor a later page answers."""
        for key in keys:
            self._pagers[key].assert_paged_to_exhaustion()


def _pages(*item_lists):
    """Build a `Pager` over a page script: every page but the last carries a continuation key."""
    pages = []
    for position, items in enumerate(item_lists):
        page = {"Items": list(items)}
        if position < len(item_lists) - 1:
            page["LastEvaluatedKey"] = {"pk": f"page-{position}"}
        pages.append(page)
    return Pager(*pages)


def _link_pages(overrides=None):
    """A links-table stub keyed on (GSI, relationshipType), defaulting each to one empty page.

    All four reads are scripted here rather than at each call site because
    `get_asset_relationship_flags` issues every one of them, so a test that overrides only
    the route it cares about still describes the whole read set it is asserting over.
    """
    scripts = {
        ("fromAssetGSI", "parentChild"): _pages([]),
        ("toAssetGSI", "parentChild"): _pages([]),
        ("fromAssetGSI", "related"): _pages([]),
        ("toAssetGSI", "related"): _pages([]),
    }
    scripts.update(overrides or {})
    return _PagedTable(scripts)


def _metadata_row(key, value):
    return {"metadataKey": key, "metadataValue": value, "metadataValueType": "string"}


class _SelectiveClient:
    """An OpenSearch client that rejects documents whose id ends with a marker.

    A blanket-raising MagicMock cannot distinguish "the failed record was reported"
    from "every record was reported", which is the assertion that matters.
    """

    def __init__(self, reject_suffix="#bad1"):
        self._reject_suffix = reject_suffix
        self.indexed = []

    def index(self, index=None, id=None, body=None, **kwargs):
        from opensearchpy.exceptions import TransportError
        self.indexed.append((id, body))
        if id.endswith(self._reject_suffix):
            raise TransportError(500, "server_error", {})
        return {"result": "updated"}


def _lookup_stubs(module, os_client, links_table=None, metadata_table=None):
    """Patch the whole lookup chain process_asset_index_request walks."""
    assets = MagicMock()
    assets.get_item.side_effect = lambda Key: {
        "Item": {
            "databaseId": Key["databaseId"],
            "assetId": Key["assetId"],
            "assetName": "Asset One",
            "assetType": "glb",
            "bucketId": "b1",
            "assetLocation": {"Key": f"{Key['assetId']}/"},
        }
    }
    buckets = MagicMock()
    buckets.query.return_value = {
        "Items": [{"bucketId": "b1", "bucketName": "bkt", "baseAssetsPrefix": "/"}]
    }
    versions = MagicMock()
    versions.query.return_value = {"Items": []}

    return [
        patch.object(module, "asset_storage_table", assets),
        patch.object(module, "s3_asset_buckets_table", buckets),
        patch.object(module, "asset_file_metadata_table",
                     metadata_table or _PagedTable(
                         {("DatabaseIdAssetIdFilePathIndex", None): _pages([])})),
        patch.object(module, "asset_versions_table", versions),
        patch.object(module, "asset_links_table", links_table or _link_pages()),
        patch.object(module.opensearch_manager, "get_client", return_value=os_client),
    ]


def _invoke(module, event, os_client, links_table=None, metadata_table=None):
    with ExitStack() as stack:
        for context in _lookup_stubs(module, os_client, links_table, metadata_table):
            stack.enter_context(context)
        return module.lambda_handler(event, MagicMock())


def _asset_record(message_id, asset_id):
    """An SQS record carrying an SNS-wrapped asset-table stream record."""
    stream_record = {
        "eventSourceARN": "arn:aws:dynamodb:us-east-1:1:table/test-asset-table/stream/x",
        "eventName": "MODIFY",
        "dynamodb": {"NewImage": {"databaseId": {"S": DATABASE_ID},
                                  "assetId": {"S": asset_id}}},
    }
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification",
                            "Message": json.dumps(stream_record)}),
    }


def _link_record(message_id, from_asset_id, to_asset_id):
    """An SQS record carrying an SNS-wrapped asset-links stream record."""
    stream_record = {
        "eventSourceARN": "arn:aws:dynamodb:us-east-1:1:table/test-links-table/stream/x",
        "eventName": "INSERT",
        "dynamodb": {"NewImage": {
            "assetLinkId": {"S": "l1"},
            "fromAssetDatabaseId:fromAssetId": {"S": f"{DATABASE_ID}:{from_asset_id}"},
            "toAssetDatabaseId:toAssetId": {"S": f"{DATABASE_ID}:{to_asset_id}"},
        }},
    }
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification",
                            "Message": json.dumps(stream_record)}),
    }


def _identifiers(response):
    assert "batchItemFailures" in response, \
        "no batchItemFailures field: the event-source mapping reads that as a whole-batch success"
    return [entry["itemIdentifier"] for entry in response["batchItemFailures"]]


#######################
# S2-BACKEND-031
#######################


@pytest.mark.unit
class TestOpenSearchFailureIsReportedForRedrive:
    """`index_asset_document` returns False on any OpenSearch exception. The 200 the
    handler returns is a whole-batch success unless the record is reported, so an
    OpenSearch 5xx used to delete the message and leave the asset unindexed."""

    def test_index_exception_reports_the_record(self, asset_indexer):
        m = asset_indexer
        client = _SelectiveClient(reject_suffix=f"#{ASSET_ID}")
        response = _invoke(m, {"Records": [_asset_record("msg-bad", ASSET_ID)]}, client)

        assert client.indexed, "the document never reached OpenSearch; the test proves nothing"
        assert _identifiers(response) == ["msg-bad"]

    def test_only_the_failed_record_is_reported(self, asset_indexer):
        """A clean record in the same batch must not be redriven, or the batch never
        drains."""
        m = asset_indexer
        client = _SelectiveClient()
        response = _invoke(m, {"Records": [_asset_record("msg-good", "good1"),
                                           _asset_record("msg-bad", "bad1")]}, client)

        assert [doc_id for doc_id, _ in client.indexed] == \
            [f"{DATABASE_ID}#good1", f"{DATABASE_ID}#bad1"]
        assert _identifiers(response) == ["msg-bad"]

    def test_malformed_sqs_body_reports_the_record(self, asset_indexer):
        """A body that is not JSON produces success=False inside the record loop. It is
        redriven to the queue's redrive policy, which dead-letters it after
        maxReceiveCount rather than dropping it silently."""
        m = asset_indexer
        client = _SelectiveClient()
        response = _invoke(m, {"Records": [{"messageId": "msg-bad-json",
                                            "eventSource": "aws:sqs",
                                            "body": "{not json"}]}, client)

        assert _identifiers(response) == ["msg-bad-json"]

    def test_links_record_reports_when_either_side_fails(self, asset_indexer):
        """An asset-links record produces TWO results -- one per linked asset -- so the
        per-record failure slice, not the last result, decides whether it is reported.
        Redriving re-indexes the side that succeeded too, which is an idempotent upsert
        keyed by the document id."""
        m = asset_indexer
        client = _SelectiveClient()
        response = _invoke(m, {"Records": [_link_record("msg-link", "good1", "bad1")]},
                           client)

        assert [doc_id for doc_id, _ in client.indexed] == \
            [f"{DATABASE_ID}#good1", f"{DATABASE_ID}#bad1"]
        assert _identifiers(response) == ["msg-link"]

    def test_successful_batch_reports_nothing(self, asset_indexer):
        """Positive control: an indexable asset still returns 200, still reaches
        OpenSearch, and is NOT redriven. Without this, reporting every record would
        look like a fix."""
        m = asset_indexer
        client = _SelectiveClient()
        response = _invoke(m, {"Records": [_asset_record("msg-1", "good1")]}, client)

        assert response["statusCode"] == 200
        assert [doc_id for doc_id, _ in client.indexed] == [f"{DATABASE_ID}#good1"]
        assert _identifiers(response) == []


#######################
# S2-BACKEND-032
#######################


@pytest.mark.unit
class TestPagedLookupsReachTheIndexedDocument:
    """DynamoDB applies a FilterExpression after the 1 MB page, so the first pages of a
    heavily linked asset come back empty and a metadata set larger than a page is read
    in part. The assertion is on the document body OpenSearch received, because
    `build_asset_document` is what copies these onto it."""

    def test_relationship_flags_from_a_later_page_reach_the_document(self, asset_indexer):
        """The finding's own scenario: 'related' links fill the first page and the single
        'parentChild' link lands on a later one."""
        m = asset_indexer
        client = _SelectiveClient()
        links = _link_pages({
            ("fromAssetGSI", "parentChild"): _pages([], [{"assetLinkId": "child"}]),
            ("toAssetGSI", "related"): _pages([], [{"assetLinkId": "rel"}]),
        })

        response = _invoke(m, {"Records": [_asset_record("msg-1", ASSET_ID)]},
                           client, links_table=links)

        # The document assertions come first so a failure here attributes to the paging
        # defect rather than to the batch-reporting one asserted below.
        (_, body), = client.indexed
        assert body["bool_has_asset_children"] is True
        assert body["bool_has_assets_related"] is True
        # Nothing was on either parent page, so this one stays False -- the flags are
        # derived, not set True as a group.
        assert body["bool_has_asset_parents"] is False
        # The flags being right is not on its own proof that the second page was reached:
        # state that over the cursors, so a stub whose first page happened to answer
        # cannot satisfy this.
        links.assert_paged_to_exhaustion(("fromAssetGSI", "parentChild"),
                                         ("toAssetGSI", "related"))
        assert _identifiers(response) == []

    def test_metadata_from_a_later_page_reaches_the_document(self, asset_indexer):
        m = asset_indexer
        client = _SelectiveClient()
        metadata = _PagedTable({("DatabaseIdAssetIdFilePathIndex", None): _pages(
            [_metadata_row("alpha", "one")],
            [_metadata_row("omega", "two")],
        )})

        _invoke(m, {"Records": [_asset_record("msg-1", ASSET_ID)]},
                client, metadata_table=metadata)

        (_, body), = client.indexed
        assert body["MD_"] == {"alpha": "one", "omega": "two"}
        metadata.assert_paged_to_exhaustion(("DatabaseIdAssetIdFilePathIndex", None))

    def test_single_page_asset_indexes_unchanged(self, asset_indexer):
        """Positive control: the ordinary asset -- one page of metadata, no links -- still
        indexes, with the flags False rather than defaulted True by the paged reads."""
        m = asset_indexer
        client = _SelectiveClient()
        metadata = _PagedTable({("DatabaseIdAssetIdFilePathIndex", None): _pages(
            [_metadata_row("alpha", "one")])})

        response = _invoke(m, {"Records": [_asset_record("msg-1", ASSET_ID)]},
                           client, metadata_table=metadata)

        assert response["statusCode"] == 200
        (doc_id, body), = client.indexed
        assert doc_id == f"{DATABASE_ID}#{ASSET_ID}"
        assert body["MD_"] == {"alpha": "one"}
        assert body["bool_has_asset_children"] is False
        assert body["bool_has_asset_parents"] is False
        assert body["bool_has_assets_related"] is False
        assert _identifiers(response) == []
