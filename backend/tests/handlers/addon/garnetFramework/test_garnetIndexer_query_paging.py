# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-031 / S2-BACKEND-032 -- the Garnet Framework indexers must read every page.

The Garnet NGSI-LD indexers carried the pre-fix shape of the reads `assetIndexer.py` already
fixed: the relationship flags were four single-call `len(response.get('Items', [])) > 0`
existence checks with a `relationshipType` FilterExpression, and every metadata, attribute,
asset-link and current-version read returned whatever the first 1 MB page held.

Two distinct defects, and only one of them is about a filter:

*   **Existence checks.** DynamoDB applies a `FilterExpression` AFTER the page is read, so empty
    `Items` alongside a present `LastEvaluatedKey` is the normal shape for "the match is on a
    later page". An asset with thousands of links therefore reported `hasChildren: false` to the
    digital twin while the one `parentChild` link sat beyond the first page.
*   **Full-set reads.** The metadata, attribute and asset-link collectors truncate outright once
    the matched set passes 1 MB, and `get_asset_version_info` -- an `isCurrentVersion` filter over
    every version of the asset -- returns `{}` when the current version lands on a later page.

Both are routed through the shared `common.dynamodb` helpers (`query_has_match`,
`query_all_items`), so `backend/tests/common/test_dynamodb_query_all_items.py` owns the loop
FORM and this file owns the WIRING: that each converted call site actually goes through a helper,
with the right table, index and filter. One positive arm per converted site, because a fix that
converts the parentChild pair and leaves the version lookup or the related pair single-page
satisfies any assertion stated only about `hasChildren`.

The two findings share this one file rather than taking one each. They are not two subjects: both
name the same owner ruling, the same three modules, and the same eleven call sites — the paging
port is a single change, and the only thing distinguishing them is which of the two findings the
Garnet copy was noticed under. Two files would have had to split the four relationship checks from
the seven collectors, which is not a seam either finding draws, and would have duplicated the
module loader, the page builders and the `_flag_pagers` router that all of them need.

**No live smoke arm, by owner ruling, not by convenience:** *"Don't test garnet framework for now as
we don't have that deployed in the account and the changes are minimal."* The add-on is off on the
smoke target (`app.addons.useGarnetFramework.enabled` false, no `garnetApiEndpoint`), so this file
is the whole of the coverage; `docs/review/SMOKE-WAVES.md` records both findings as unit-only
Wave S1 entries against that same ruling. Reproducing the truncation live would additionally need a
link partition over 1 MB, which a smoke run cannot seed.

Two construction facts this file depends on, both learned the hard way:

*   `get_asset_relationship_flags` issues FOUR reads on the SAME table inside ONE broad
    `except Exception` that returns all-False. A page script that runs out mid-function raises
    `StopIteration`, the except swallows it, and the all-False result is byte-identical to the
    correct answer for "no relationships" -- so a stub must route by `IndexName` +
    `FilterExpression` and give every check a terminal page, and the negative arm must additionally
    assert that `logger.exception` was NOT called.
*   `get_file_metadata` returns a 2-TUPLE `(metadata, attributes)`, so `len(...)` on its result is
    always 2. And every metadata collector keys a dict by `metadataKey` and skips
    `is_excluded_metadata_record` keys, so scripted rows need distinct non-excluded keys or a count
    assertion is vacuous.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.pagingStub import Pager, RoutedPager

_GARNET_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "garnetFramework",
)


def _load(file_name, suffix):
    """Load an independent copy of a Garnet indexer by file path.

    A suite-private module name, so this file cannot end up asserting against another suite's
    copy of the module, and so a failed load leaves the other Garnet tests untouched.
    """
    path = os.path.abspath(os.path.join(_GARNET_DIR, file_name))
    spec = importlib.util.spec_from_file_location(
        f"{file_name[:-3]}_query_paging_{suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def asset_indexer():
    return _load("garnetDataIndexAsset.py", "asset")


@pytest.fixture(scope="module")
def file_indexer():
    return _load("garnetDataIndexFile.py", "file")


@pytest.fixture(scope="module")
def database_indexer():
    return _load("garnetDataIndexDatabase.py", "database")


def _metadata_page(keys, last_key=None):
    """A metadata page carrying distinct, non-excluded keys."""
    page = {"Items": [{"metadataKey": key, "metadataValue": f"v-{key}",
                       "metadataValueType": "string"} for key in keys]}
    if last_key is not None:
        page["LastEvaluatedKey"] = {"metadataKey": last_key}
    return page


def _attribute_page(keys, last_key=None):
    page = {"Items": [{"attributeKey": key, "attributeValue": f"v-{key}",
                       "attributeValueType": "string"} for key in keys]}
    if last_key is not None:
        page["LastEvaluatedKey"] = {"attributeKey": last_key}
    return page


def _link_page(link_ids, last_key=None):
    page = {"Items": [{"assetLinkId": link_id} for link_id in link_ids]}
    if last_key is not None:
        page["LastEvaluatedKey"] = {"assetLinkId": last_key}
    return page


def _flag_pagers(**per_check):
    """A `query` stub for the four relationship checks, routed on IndexName + FilterExpression.

    Keys are `children`, `parents`, `related_from`, `related_to`. Every check named gets its own
    page sequence; a check not named gets a single terminal empty page -- so the script can never
    run out mid-function and hand the shared `except Exception` a StopIteration to swallow.
    """
    pagers = {
        name: Pager(*per_check.get(name, ({"Items": []},)), name=f"flags:{name}")
        for name in ("children", "parents", "related_from", "related_to")
    }

    def query(**kwargs):
        index = kwargs.get("IndexName")
        # The relationship type has to be read out of the boto3 condition OBJECT: `str()` of one
        # renders `<boto3.dynamodb.conditions.Equals object at 0x...>`, so a substring test on it
        # is always False and every read would route to the parentChild pagers -- which looks like
        # a broken fix rather than a broken stub.
        relationship_type = kwargs["FilterExpression"].get_expression()["values"][1]
        is_related = relationship_type == "related"
        if index == "fromAssetGSI":
            key = "related_from" if is_related else "children"
        else:
            key = "related_to" if is_related else "parents"
        return pagers[key](**kwargs)

    return query, pagers


@pytest.mark.unit
class TestRelationshipFlagsPageUntilAMatchIsFound:
    """Each of the four filtered existence checks must page, and each independently."""

    @pytest.mark.parametrize(
        "check, flag",
        [("children", "has_children"), ("parents", "has_parents")],
    )
    def test_a_parent_child_link_on_a_later_page_sets_the_flag(
            self, asset_indexer, check, flag):
        query, pagers = _flag_pagers(**{check: (
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "p1"}},
            {"Items": [{"relationshipType": "parentChild"}]},
        )})
        with patch.object(asset_indexer, "asset_links_table", MagicMock(query=query)):
            flags = asset_indexer.get_asset_relationship_flags("db1", "asset1")

        assert flags[flag] is True
        pagers[check].assert_paged_to_exhaustion()

    @pytest.mark.parametrize("check", ["related_from", "related_to"])
    def test_a_related_link_on_a_later_page_sets_has_related(self, asset_indexer, check):
        """Both directions, separately: `has_related` is an `or` of two reads, so a fix that pages
        one of them satisfies any arm stated over the pair."""
        query, pagers = _flag_pagers(**{check: (
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "r1"}},
            {"Items": [{"relationshipType": "related"}]},
        )})
        with patch.object(asset_indexer, "asset_links_table", MagicMock(query=query)):
            flags = asset_indexer.get_asset_relationship_flags("db1", "asset1")

        assert flags["has_related"] is True
        pagers[check].assert_paged_to_exhaustion()

    def test_no_links_at_all_reports_every_flag_false_without_an_error(self, asset_indexer):
        """The paired control, and it needs the logger assertion to say anything.

        All-False is also exactly what the function's broad `except Exception` returns, so a
        NameError in a helper, a mis-shaped stub or a swallowed StopIteration would pass an
        all-False assertion. Asserting that nothing was logged as an exception is what separates
        "there are no links" from "the read blew up". The reads are counted too: four checks over
        four terminal pages is four reads, and the function completing at all is part of the
        assertion -- a loop that paged on a truthy value would never return.
        """
        query, pagers = _flag_pagers()
        table = MagicMock(query=query)
        with patch.object(asset_indexer, "asset_links_table", table), \
                patch.object(asset_indexer.logger, "exception") as logged:
            flags = asset_indexer.get_asset_relationship_flags("db1", "asset1")

        assert flags == {"has_children": False, "has_parents": False, "has_related": False}
        logged.assert_not_called()
        assert sum(len(pager.calls) for pager in pagers.values()) == 4, \
            {name: pager.calls for name, pager in pagers.items()}

    def test_a_first_page_match_costs_one_read_for_that_check(self, asset_indexer):
        """Must-still-work: the paged helper must not have added a round trip. Asserted per CHECK,
        not on the function -- correct code reads four times in total."""
        query, pagers = _flag_pagers(children=({"Items": [{"relationshipType": "parentChild"}]},))
        with patch.object(asset_indexer, "asset_links_table", MagicMock(query=query)):
            flags = asset_indexer.get_asset_relationship_flags("db1", "asset1")

        assert flags["has_children"] is True
        # A first-page match must cost no extra round trip. Both bounds: the non-emptiness guard
        # catches a stub that was never read (see the class docstring -- an unread stub makes this
        # boolean pass having proved nothing), the upper bound catches an added read.
        assert pagers["children"].calls, "the children pager was never read"
        assert len(pagers["children"].calls) <= 1, pagers["children"].calls
        assert pagers["children"].resumed_from == []


@pytest.mark.unit
class TestAssetCollectorsReadEveryPage:
    def test_asset_metadata_spans_pages(self, asset_indexer):
        pager = Pager(_metadata_page(["alpha", "beta"], last_key="beta"),
                      _metadata_page(["gamma", "delta"]),
                      name="asset metadata")
        with patch.object(asset_indexer, "asset_file_metadata_table",
                          MagicMock(query=pager)):
            metadata = asset_indexer.get_asset_metadata("db1", "asset1")

        assert set(metadata) == {"alpha", "beta", "gamma", "delta"}
        pager.assert_paged_to_exhaustion()

    def test_the_current_version_on_a_later_page_is_found(self, asset_indexer):
        """`isCurrentVersion` is a FilterExpression over every version of the asset, so the current
        version of a heavily versioned asset lands past page one and the function returned `{}` --
        which reads downstream as "this asset has no version", not as a truncated read."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetVersionId": "v1"}},
            {"Items": [{"assetVersionId": "v9", "dateCreated": "2026-01-01T00:00:00Z",
                        "comment": "current", "versionAlias": "latest",
                        "isCurrentVersion": True}]},
            name="asset versions",
        )
        with patch.object(asset_indexer, "asset_versions_table", MagicMock(query=pager)):
            info = asset_indexer.get_asset_version_info("db1", "asset1")

        assert info.get("versionId") == "v9"
        pager.assert_paged_to_exhaustion()

    def test_asset_link_metadata_spans_pages(self, asset_indexer):
        pager = Pager(_metadata_page(["alpha"], last_key="alpha"),
                      _metadata_page(["beta"]),
                      name="asset link metadata")
        with patch.object(asset_indexer, "asset_links_metadata_table",
                          MagicMock(query=pager)):
            metadata = asset_indexer.get_asset_link_metadata("link1")

        assert set(metadata) == {"alpha", "beta"}
        pager.assert_paged_to_exhaustion()

    def test_every_asset_link_is_collected_from_both_directions(self, asset_indexer):
        """Unfiltered reads over the whole link set, so this one truncated outright rather than
        answering a wrong boolean -- an asset with thousands of links re-indexed only the first
        page's worth of links."""
        routed = RoutedPager(
            on="IndexName",
            fromAssetGSI=Pager(_link_page(["l1"], last_key="l1"), _link_page(["l2"]),
                               name="links from"),
            toAssetGSI=Pager(_link_page(["l3"], last_key="l3"), _link_page(["l4"]),
                             name="links to"),
        )
        with patch.object(asset_indexer, "asset_links_table", MagicMock(query=routed)):
            link_ids = asset_indexer.get_all_asset_links_for_asset("db1", "asset1")

        assert sorted(link_ids) == ["l1", "l2", "l3", "l4"]
        routed.assert_paged_to_exhaustion()


@pytest.mark.unit
class TestFileAndDatabaseCollectorsReadEveryPage:
    def test_file_metadata_and_attributes_each_span_pages(self, file_indexer):
        """`get_file_metadata` returns a 2-tuple, so it is unpacked -- `len()` on the result is 2
        whatever the reads returned, and asserting on that would pass against a single-page read."""
        metadata_pager = Pager(_metadata_page(["alpha"], last_key="alpha"),
                               _metadata_page(["beta"]),
                               name="file metadata")
        attribute_pager = Pager(_attribute_page(["width"], last_key="width"),
                                _attribute_page(["height"]),
                                name="file attributes")
        with patch.object(file_indexer, "asset_file_metadata_table",
                          MagicMock(query=metadata_pager)), \
                patch.object(file_indexer, "file_attribute_table",
                             MagicMock(query=attribute_pager)):
            metadata, attributes = file_indexer.get_file_metadata("db1", "asset1", "/f.glb")

        assert set(metadata) == {"alpha", "beta"}
        assert set(attributes) == {"width", "height"}
        metadata_pager.assert_paged_to_exhaustion()
        attribute_pager.assert_paged_to_exhaustion()

    def test_database_metadata_spans_pages(self, database_indexer):
        pager = Pager(_metadata_page(["alpha"], last_key="alpha"),
                      _metadata_page(["beta"]),
                      name="database metadata")
        with patch.object(database_indexer, "database_metadata_table",
                          MagicMock(query=pager)):
            metadata = database_indexer.get_database_metadata("db1")

        assert set(metadata) == {"alpha", "beta"}
        pager.assert_paged_to_exhaustion()


@pytest.mark.unit
class TestTheStubsInThisFileAreActuallyRead:
    """The guard that keeps every arm above from being a test that cannot fail.

    A paged read is the exact shape of the mock-shadow hazard: one source file can exist as several
    module objects, and `patch.object` on the wrong one leaves the function resolving the real
    attribute through its own `__globals__`. Here that does not merely weaken the arms, it INVERTS
    them: an unpatched `asset_links_table` is a `MagicMock`, `response.get('Items')` on a MagicMock
    is truthy, so `query_has_match` returns True on its first read and
    `assert flags['has_children'] is True` PASSES with the stub never read at all.

    Measured, not assumed. Loading the same file twice and patching the second copy while calling
    the first gives `has_children=True` with a stub read count of 0; every arm above survives that
    only because `Pager.assert_paged_to_exhaustion()` carries a read floor, which reports
    "nothing read this pager, so it says nothing about reaching the final page". These two tests
    state that protection instead of relying on it.
    """

    _PAGED_FUNCTIONS = {
        "garnetDataIndexAsset.py": ["get_asset_relationship_flags", "get_asset_metadata",
                                    "get_asset_version_info", "get_asset_link_metadata",
                                    "get_all_asset_links_for_asset"],
        "garnetDataIndexFile.py": ["get_file_metadata"],
        "garnetDataIndexDatabase.py": ["get_database_metadata"],
    }

    @pytest.mark.parametrize("file_name", sorted(_PAGED_FUNCTIONS))
    def test_every_converted_function_resolves_names_through_the_patched_module(self, file_name):
        """`patch.object(module, 'some_table', ...)` writes into `module.__dict__`, and the function
        reads `some_table` out of its `__globals__`. Those must be the same mapping, or the stub is
        written somewhere nothing reads."""
        module = _load(file_name, f"globals_{file_name[:-3]}")

        for function_name in self._PAGED_FUNCTIONS[file_name]:
            function = getattr(module, function_name)
            assert function.__globals__ is module.__dict__, (
                f"{file_name}:{function_name} resolves its names through a different mapping than "
                "the module object these tests patch")

    def test_an_unread_stub_fails_rather_than_passing(self, asset_indexer):
        """The positive control on the read floor: with the table NOT patched, the arm's own
        assertion still passes (the MagicMock answers truthily) while the pager reports zero reads.

        So the boolean alone cannot distinguish a working fix from an unread stub, and the read
        count is what does. Asserted in-band on this file's own double.
        """
        _, pagers = _flag_pagers(children=(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "p1"}},
            {"Items": [{"relationshipType": "parentChild"}]},
        ))

        # Deliberately NOT patched onto the module: this is the shadow shape.
        flags = asset_indexer.get_asset_relationship_flags("db1", "asset1")

        assert flags["has_children"] is True, (
            "an unpatched MagicMock table no longer answers truthily; the read-floor rationale in "
            "this file needs revisiting")
        assert sum(len(pager.calls) for pager in pagers.values()) == 0
        with pytest.raises(AssertionError, match="nothing read this pager"):
            pagers["children"].assert_paged_to_exhaustion()


@pytest.mark.unit
class TestTheBucketLookupIsDeliberatelyNotPaged:
    """The one shape Rule 14 allows, pinned so a later sweep does not "fix" it.

    Each `get_bucket_details` read is a pure KeyConditionExpression with `Limit=1` and NO
    FilterExpression, so the single item it reads is the item asked for. Asserted from the source
    rather than by calling it, because the property being pinned is the absence of a filter.
    """

    @pytest.mark.parametrize(
        "file_name", ["garnetDataIndexAsset.py", "garnetDataIndexFile.py",
                      "garnetDataIndexDatabase.py"],
    )
    def test_get_bucket_details_is_a_keyed_limit_one_read(self, file_name):
        import ast
        import inspect

        module = _load(file_name, f"bucket_{file_name[:-3]}")
        source = inspect.getsource(module.get_bucket_details)
        calls = [node for node in ast.walk(ast.parse(source.lstrip()))
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "query"]

        # Non-emptiness first, because `calls[0]` below indexes it -- an empty list would raise
        # IndexError rather than report what it found. Then an upper bound: the claim is that this
        # function issues no SECOND read, not that the extractor found exactly one node.
        assert calls, f"no table.query call was found at all, so the shape is unverified: {source}"
        assert len(calls) <= 1, source
        kwargs = {keyword.arg for keyword in calls[0].keywords}
        assert kwargs == {"KeyConditionExpression", "Limit"}, kwargs
