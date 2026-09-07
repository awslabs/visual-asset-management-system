# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""DynamoDB paging in the asset and file indexers.

Guards S2-BACKEND-032: a DynamoDB query reads at most 1 MB before its
FilterExpression is applied, and none of these reads followed
`LastEvaluatedKey`. Two consequences, both silent:

* an existence check written as `len(Items) > 0` is a FALSE NEGATIVE whenever
  the matching link sits beyond the first page -- an asset with thousands of
  `related` links and one `parentChild` link reported has_children False;
* metadata and attribute reads returned part of the set, so a file or asset
  whose rows exceed 1 MB was indexed with metadata missing.

Every stub here TERMINATES: a page with no `LastEvaluatedKey` ends the walk. A
stub built from a bare `MagicMock` would not -- its `.get('LastEvaluatedKey')`
answers truthily forever and the loop would never exit. That is why the pages are
real dicts, and why this file completing at all is part of the assertion.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-links-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}

_INDEXING_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing"
)


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


def _load_indexer(module_name):
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
                f"{module_name}_paging_under_test",
                os.path.abspath(os.path.join(_INDEXING_DIR, f"{module_name}.py")),
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
    return _load_indexer("assetIndexer")


@pytest.fixture
def file_indexer():
    return _load_indexer("fileIndexer")


def _filter_value(condition):
    """The right-hand value of an `Attr(...).eq(...)` FilterExpression, or None."""
    if condition is None:
        return None
    return condition.get_expression()["values"][1]


class _PagedTable:
    """A table stub that serves a scripted page list per (IndexName, filter value).

    The last page of every script carries no `LastEvaluatedKey`, so any caller
    that follows the continuation key terminates.
    """

    def __init__(self, scripts):
        self._scripts = scripts
        self._cursor = {}
        self.start_keys = []

    def query(self, **kwargs):
        key = (kwargs.get("IndexName"), _filter_value(kwargs.get("FilterExpression")))
        pages = self._scripts[key]
        index = self._cursor.get(key, 0)
        assert index < len(pages), (
            f"the caller asked for page {index + 1} of {len(pages)} for {key}: "
            "the walk did not stop at the page without a continuation key")
        self._cursor[key] = index + 1
        self.start_keys.append(kwargs.get("ExclusiveStartKey"))
        return pages[index]


def _pages(*item_lists):
    """Build a page script: every page but the last carries a continuation key."""
    pages = []
    for position, items in enumerate(item_lists):
        page = {"Items": list(items)}
        if position < len(item_lists) - 1:
            page["LastEvaluatedKey"] = {"pk": f"page-{position}"}
        pages.append(page)
    return pages


@pytest.mark.unit
class TestQueryAllPages:
    @pytest.mark.parametrize("module_name", ["assetIndexer", "fileIndexer"])
    def test_accumulates_every_page_and_terminates(self, module_name):
        m = _load_indexer(module_name)
        table = _PagedTable({(None, None): _pages([{"i": 1}], [{"i": 2}], [{"i": 3}])})
        items = m.query_all_pages(table)
        assert items == [{"i": 1}, {"i": 2}, {"i": 3}]
        # The continuation key of each page is replayed on the next call.
        assert table.start_keys == [None, {"pk": "page-0"}, {"pk": "page-1"}]

    @pytest.mark.parametrize("module_name", ["assetIndexer", "fileIndexer"])
    def test_single_page_makes_no_second_call(self, module_name):
        """Positive control: a response without a continuation key ends the walk.
        The stub asserts on over-reading, so a non-terminating loop fails here
        rather than hanging."""
        m = _load_indexer(module_name)
        table = _PagedTable({(None, None): _pages([{"i": 1}])})
        assert m.query_all_pages(table) == [{"i": 1}]


@pytest.mark.unit
class TestQueryHasMatch:
    def test_match_on_a_later_page_is_found(self, asset_indexer):
        """The defect: DynamoDB applies the FilterExpression after the 1 MB read
        cap, so the first pages of a heavily linked asset come back empty."""
        m = asset_indexer
        table = _PagedTable({(None, None): _pages([], [], [{"assetLinkId": "l1"}])})
        assert m.query_has_match(table) is True

    def test_no_match_anywhere_is_false(self, asset_indexer):
        """Negative case with the same page count, so True is not a constant."""
        m = asset_indexer
        table = _PagedTable({(None, None): _pages([], [], [])})
        assert m.query_has_match(table) is False


@pytest.mark.unit
class TestAssetRelationshipFlags:
    def test_parent_child_link_beyond_the_first_page_sets_has_children(self, asset_indexer):
        """S2-BACKEND-032's own scenario: thousands of 'related' links fill the
        first page and the single 'parentChild' link lands on a later one."""
        m = asset_indexer
        table = _PagedTable({
            ("fromAssetGSI", "parentChild"): _pages([], [{"assetLinkId": "child"}]),
            ("toAssetGSI", "parentChild"): _pages([]),
            ("fromAssetGSI", "related"): _pages([]),
            ("toAssetGSI", "related"): _pages([]),
        })
        with patch.object(m, "asset_links_table", table):
            flags = m.get_asset_relationship_flags("db1", "a1")
        assert flags["has_children"] is True
        assert flags["has_parents"] is False
        assert flags["has_related"] is False

    def test_related_link_on_the_to_side_beyond_the_first_page(self, asset_indexer):
        m = asset_indexer
        table = _PagedTable({
            ("fromAssetGSI", "parentChild"): _pages([]),
            ("toAssetGSI", "parentChild"): _pages([]),
            ("fromAssetGSI", "related"): _pages([], []),
            ("toAssetGSI", "related"): _pages([], [{"assetLinkId": "rel"}]),
        })
        with patch.object(m, "asset_links_table", table):
            flags = m.get_asset_relationship_flags("db1", "a1")
        assert flags["has_related"] is True

    def test_no_links_leaves_every_flag_false(self, asset_indexer):
        """Positive control: the flags are derived, not hardcoded True."""
        m = asset_indexer
        table = _PagedTable({
            ("fromAssetGSI", "parentChild"): _pages([]),
            ("toAssetGSI", "parentChild"): _pages([]),
            ("fromAssetGSI", "related"): _pages([]),
            ("toAssetGSI", "related"): _pages([]),
        })
        with patch.object(m, "asset_links_table", table):
            flags = m.get_asset_relationship_flags("db1", "a1")
        assert flags == {"has_children": False, "has_parents": False, "has_related": False}


def _metadata_row(key, value):
    return {"metadataKey": key, "metadataValue": value, "metadataValueType": "string"}


@pytest.mark.unit
class TestAssetMetadataPaging:
    def test_metadata_from_every_page_reaches_the_document(self, asset_indexer):
        m = asset_indexer
        table = _PagedTable({
            ("DatabaseIdAssetIdFilePathIndex", None): _pages(
                [_metadata_row("first", "1")],
                [_metadata_row("second", "2")],
            )
        })
        with patch.object(m, "asset_file_metadata_table", table):
            metadata = m.get_asset_metadata("db1", "a1")
        assert set(metadata) == {"first", "second"}


@pytest.mark.unit
class TestAssetVersionInfoPaging:
    def test_current_version_beyond_the_first_page_is_found(self, asset_indexer):
        """isCurrentVersion is a FilterExpression, so the current version of a
        long-lived asset can sit on a later page."""
        m = asset_indexer
        table = _PagedTable({
            (None, True): _pages([], [{"assetVersionId": "v9", "isCurrentVersion": True}]),
        })
        with patch.object(m, "asset_versions_table", table):
            info = m.get_asset_version_info("db1", "a1")
        assert info, "no version info returned for a current version on page 2"


@pytest.mark.unit
class TestFileMetadataPaging:
    def test_metadata_and_attributes_from_every_page(self, file_indexer):
        m = file_indexer
        metadata_table = _PagedTable({
            ("DatabaseIdAssetIdFilePathIndex", None): _pages(
                [_metadata_row("md_first", "1")],
                [_metadata_row("md_second", "2")],
            )
        })
        attribute_table = _PagedTable({
            ("DatabaseIdAssetIdFilePathIndex", None): _pages(
                [_metadata_row("ab_first", "1")],
                [_metadata_row("ab_second", "2")],
            )
        })
        with patch.object(m, "asset_file_metadata_table", metadata_table), \
                patch.object(m, "file_attribute_table", attribute_table):
            metadata, attributes = m.get_file_metadata("db1", "a1", "/part.stp")
        assert set(metadata) == {"md_first", "md_second"}
        assert set(attributes) == {"ab_first", "ab_second"}


@pytest.mark.unit
class TestPermanentDeleteLookupPaging:
    def test_second_page_match_prevents_the_single_match_shortcut(self, file_indexer):
        """Step 1 short-circuits on `len(items) == 1`. When the second
        same-assetId asset is on page 2, a first-page-only read takes that
        shortcut on page 1's record -- whose bucket is NOT the event's -- and so
        resolves nothing at all. Only a read that reaches page 2 finds the record
        the event belongs to."""
        m = file_indexer
        table = _PagedTable({
            ("assetIdGSI", None): _pages(
                [{"assetId": "a1", "databaseId": "dbA", "bucketId": "bA"}],
                [{"assetId": "a1", "databaseId": "dbB", "bucketId": "bB"}],
            )
        })

        def bucket_details(bucket_id):
            return {"bucketId": bucket_id,
                    "bucketName": "bucket" if bucket_id == "bB" else "other-bucket",
                    "baseAssetsPrefix": "prefix-b/"}

        with patch.object(m, "asset_storage_table", table), \
                patch.object(m, "get_bucket_details", side_effect=bucket_details):
            database_id, ok = m.lookup_database_id_for_permanent_delete(
                "a1", "bucket", "prefix-b/")

        assert (database_id, ok) == ("dbB", True)

    def test_single_asset_still_resolves(self, file_indexer):
        """Positive control: the paging change must not break the ordinary
        single-match case. The one record is registered in the event's bucket at
        its root prefix, which the event spells `/` and the registration `''`."""
        m = file_indexer
        table = _PagedTable({
            ("assetIdGSI", None): _pages(
                [{"assetId": "a1", "databaseId": "dbA", "bucketId": "bA"}])
        })
        details = MagicMock(return_value={
            "bucketId": "bA", "bucketName": "bucket", "baseAssetsPrefix": ""})
        with patch.object(m, "asset_storage_table", table), \
                patch.object(m, "get_bucket_details", details):
            assert m.lookup_database_id_for_permanent_delete(
                "a1", "bucket", "/") == ("dbA", True)
        assert details.called, "it was never called at all"
        assert details.call_count <= 1, (
            "the single-match branch never consulted the bucket registration, so its "
            "agreement with the event's bucket was never checked")


# ---------------------------------------------------------------------------
# Paging TERMINATION: the walks must end on the key's ABSENCE, not its value.
# ---------------------------------------------------------------------------

# Reads the stub will serve before it gives up. Small on purpose: the point is that a walk which
# cannot terminate fails with a message here instead of hanging the suite.
MAX_STUB_READS = 8


def _magicmock_shaped_response(items):
    """A response with the hazardous shape of a bare `MagicMock`: every `.get(key)` answers
    truthily, while `key in response` is False (`MagicMock.__contains__` defaults to False).

    That is the shape of any under-stubbed DynamoDB reader, and it is why the walks page on the
    key's ABSENCE: `response.get('LastEvaluatedKey')` here is a truthy child mock forever. `Items`
    is a real list so a walk that does terminate can still accumulate the page.
    """
    response = MagicMock()
    continuation = response.continuation_sentinel  # one stable, truthy object
    response.get.side_effect = (
        lambda key, default=None: list(items) if key == 'Items' else continuation)
    return response


class _CappedPager:
    """Serves `responses` in order (repeating the last), recording the cursor of every read.

    Two failure modes of a paging loop are turned into a named failure rather than a hang: asking
    for more than MAX_STUB_READS reads, and re-reading a cursor it has already used (which is what
    a walk does when the continuation value never changes).
    """

    def __init__(self, responses):
        self._responses = responses
        self.cursors = []

    def query(self, **kwargs):
        cursor = kwargs.get('ExclusiveStartKey')
        if len(self.cursors) >= MAX_STUB_READS:
            raise AssertionError(
                f"the walk asked for read {len(self.cursors) + 1} of a response that carries no "
                "LastEvaluatedKey: it pages on the key's VALUE, which never reports a last page")
        if any(cursor is used for used in self.cursors):
            raise AssertionError(
                "the walk re-read the same ExclusiveStartKey, so it is not advancing")
        self.cursors.append(cursor)
        index = min(len(self.cursors) - 1, len(self._responses) - 1)
        return self._responses[index]


@pytest.mark.unit
class TestPagingTerminatesOnKeyAbsence:
    """DynamoDB omits `LastEvaluatedKey` on the last page, so absence is the end of the walk. A walk
    that reads the VALUE instead is correct against real DynamoDB and non-terminating against a
    stub whose `.get` answers truthily -- the mechanism that ran this suite past its timeout without
    naming a test. These assertions hold the terminating form in place."""

    @pytest.mark.parametrize("module_name", ["assetIndexer", "fileIndexer"])
    def test_query_all_pages_stops_when_the_key_is_absent(self, module_name):
        m = _load_indexer(module_name)
        table = _CappedPager([_magicmock_shaped_response([{"i": 1}])])
        items = m.query_all_pages(table)
        assert items == [{"i": 1}], "the page's items were not accumulated"
        assert len(table.cursors) == 1, (
            f"one page took {len(table.cursors)} reads; the walk did not stop at the page with no "
            "continuation key")

    def test_query_has_match_stops_when_the_key_is_absent(self, asset_indexer):
        """The existence walk has its own loop, so it needs its own assertion. An empty page keeps
        it going, which is exactly the case a truthy continuation value never ends."""
        table = _CappedPager([_magicmock_shaped_response([])])
        assert asset_indexer.query_has_match(table) is False
        assert len(table.cursors) == 1, (
            f"one empty page took {len(table.cursors)} reads")

    @pytest.mark.parametrize("module_name", ["assetIndexer", "fileIndexer"])
    def test_a_genuine_continuation_key_is_still_followed(self, module_name):
        """Positive control for the two assertions above: the same capped harness serves a second
        page when `LastEvaluatedKey` is really PRESENT, so 'it stopped after one read' is a property
        of the response shape rather than a stub that refuses to be read twice."""
        m = _load_indexer(module_name)
        table = _CappedPager([
            {"Items": [{"i": 1}], "LastEvaluatedKey": {"pk": "p1"}},
            {"Items": [{"i": 2}]},
        ])
        assert m.query_all_pages(table) == [{"i": 1}, {"i": 2}]
        assert table.cursors == [None, {"pk": "p1"}]
