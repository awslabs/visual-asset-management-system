# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The child-tree walk is bounded, a failed walk is not an empty tree, and a read that did not
complete is not a permission denial.

Three properties of ``GET /database/{databaseId}/asset/{assetId}/asset-links``:

*   **Bounded.** ``build_child_tree`` expands each asset once and reuses the subtree, so a
    component shared by several parents costs one expansion instead of one per path that reaches
    it -- in a cross-linked lattice the difference is one expansion per distinct root-to-leaf path
    against one per distinct asset. MAX_TREE_DEPTH and MAX_TREE_NODES cap what one request walks
    and emits, and a walk that reaches either says so through ``treeTruncated``.
*   **Honest about failure.** A traversal that throws partway has produced a partial tree.
    Returning it as *the* tree makes a throttle indistinguishable from an asset that genuinely has
    no children -- the one answer a caller cannot check.
*   **Honest about a read that did not complete.** ``batch_get_item`` answers a partially
    throttled request with HTTP 200 and an ``UnprocessedKeys`` map, so nothing raises and the
    ``except`` fallback to individual gets never runs. A key left in that map is absent from the
    details, and reading absence as "the caller may not see it" reports a throttle as an
    authorization problem.
"""

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.assetLinks import assetLinksService as als
# Imported under the SAME module name the handler raises it from. `sys.path` carries both
# `backend/` and `backend/backend/`, so `backend.backend.models.common` and `models.common` are
# two distinct module objects holding two distinct classes -- `pytest.raises` against the wrong
# one does not match the exception that was actually raised, and the failure reads as "no
# exception raised" even though the traceback shows one.
from models.common import VAMSGeneralErrorResponse

MOD = "backend.backend.handlers.assetLinks.assetLinksService"
CLAIMS = {"tokens": ["u"], "roles": ["someRole"]}
DB = "db1"

# A lattice of LATTICE_WIDTH assets per level, every asset a parent of every asset on the next
# level. Distinct assets grow linearly with the level count; distinct root-to-leaf paths grow
# exponentially, and it is the paths an un-memoized walk expands.
LATTICE_LEVELS = 10
LATTICE_WIDTH = 2
LATTICE_DISTINCT_ASSETS = 1 + LATTICE_LEVELS * LATTICE_WIDTH
LATTICE_PATHS = LATTICE_WIDTH ** LATTICE_LEVELS


def _asset(database_id, asset_id):
    return {"databaseId": database_id, "assetId": asset_id, "assetName": f"Name of {asset_id}"}


def _child_link(parent, child):
    """A parentChild link, each endpoint given as a ``(databaseId, assetId)`` pair."""
    return {
        "assetLinkId": f"{parent[1]}->{child[1]}",
        "fromAssetDatabaseId": parent[0],
        "fromAssetId": parent[1],
        "toAssetDatabaseId": child[0],
        "toAssetId": child[1],
        "relationshipType": als.RelationshipType.PARENT_CHILD,
        "assetLinkAliasId": "",
    }


def _related_link(link_id, other):
    return {
        "assetLinkId": link_id,
        "fromAssetDatabaseId": DB,
        "fromAssetId": "root",
        "toAssetDatabaseId": other[0],
        "toAssetId": other[1],
        "relationshipType": als.RelationshipType.RELATED,
        "assetLinkAliasId": "",
    }


def _lattice(levels, width):
    """``{assetKey: [child links]}`` for a fully cross-linked lattice rooted at ``root``."""
    rows = [[(DB, "root")]]
    rows += [[(DB, f"L{level}_{i}") for i in range(width)] for level in range(1, levels + 1)]
    children = {}
    for level in range(len(rows) - 1):
        for parent in rows[level]:
            children[f"{parent[0]}:{parent[1]}"] = [
                _child_link(parent, child) for child in rows[level + 1]]
    return children


def _chain(length):
    """``{assetKey: [child link]}`` for a straight parent->child chain rooted at ``root``."""
    names = ["root"] + [f"c{i}" for i in range(1, length + 1)]
    return {f"{DB}:{names[i]}": [_child_link((DB, names[i]), (DB, names[i + 1]))]
            for i in range(len(names) - 1)}


def _chain_with_shortcut(length, shortcut_at):
    """A chain of ``length`` assets that the root also links to directly at ``shortcut_at``.

    The shortcut is listed FIRST, so the walk reaches that asset at depth 1 and caches a subtree
    reaching most of the way down the chain; the chain branch then meets the same asset far deeper.
    Grafting the cached subtree in whole at that depth is what can carry the emitted tree past the
    depth ceiling even though no single expansion ever walked that far.
    """
    children = _chain(length)
    children[f"{DB}:root"] = [_child_link((DB, "root"), (DB, f"c{shortcut_at}"))] \
        + children[f"{DB}:root"]
    return children


class GraphReader:
    """Serves one un-paged page per asset key from a ``{assetKey: [links]}`` graph.

    Reads are recorded with whether they carried a ``FilterExpression``, which is what separates a
    tree expansion from the two GSI reads the flat relationship listing makes.
    """

    def __init__(self, children):
        self.children = children
        self.reads = []

    def __call__(self, **kwargs):
        asset_key = kwargs["KeyConditionExpression"].get_expression()["values"][1]
        self.reads.append((asset_key, "FilterExpression" in kwargs))
        if kwargs.get("IndexName") == "toAssetGSI":
            return {"Items": []}
        return {"Items": list(self.children.get(asset_key, []))}

    @property
    def expansions(self):
        return [key for key, filtered in self.reads if filtered]


class DeferringBatchReader:
    """``batch_get_item`` that answers HTTP 200 with keys deferred into ``UnprocessedKeys``.

    This is the shape of a partial throttle or a 16 MB response cap: no exception is raised, so a
    caller that reads only ``Responses`` simply never sees those assets. With no deferred keys it
    is an ordinary reader that resolves everything asked of it.
    """

    def __init__(self, defer_keys=(), defer_rounds=0):
        self.defer_keys = set(defer_keys)
        self.defer_rounds = defer_rounds
        self.rounds = 0
        self.requested = []

    def __call__(self, RequestItems):
        table = als.asset_storage_table_name
        keys = RequestItems[table]["Keys"]
        self.requested.append([f"{k['databaseId']}:{k['assetId']}" for k in keys])
        self.rounds += 1

        served, deferred = [], []
        for key in keys:
            composite = f"{key['databaseId']}:{key['assetId']}"
            if composite in self.defer_keys and self.rounds <= self.defer_rounds:
                deferred.append(key)
            else:
                served.append(_asset(key["databaseId"], key["assetId"]))

        response = {"Responses": {table: served}}
        if deferred:
            response["UnprocessedKeys"] = {table: {"Keys": deferred}}
        return response


def _child_nodes(node):
    """The children of a tree node: a model at the top level, a dict below it."""
    children = node.get("children") if isinstance(node, dict) else node.children
    return children or []


def _count_nodes(nodes):
    return sum(1 + _count_nodes(_child_nodes(node)) for node in nodes)


def _depth(nodes):
    return 0 if not nodes else 1 + max(_depth(_child_nodes(node)) for node in nodes)


def _one_page(from_items=(), to_items=()):
    def reader(**kwargs):
        if kwargs.get("IndexName") == "toAssetGSI":
            return {"Items": list(to_items)}
        return {"Items": list(from_items)}
    return reader


@pytest.fixture
def tree_reader():
    """The links table plus authorized asset lookups; each test installs its own graph.

    ``batch_get_asset_details`` is deliberately NOT stubbed -- the real one runs against a reader
    that resolves every key, so the fan-out assertions count the reads the handler actually makes.
    Yields the table stub together with the per-child lookup mock.
    """
    table = MagicMock()
    with ExitStack() as stack:
        stack.enter_context(patch(f"{MOD}.asset_links_table", table))
        lookup = stack.enter_context(
            patch(f"{MOD}.get_asset_details", side_effect=lambda a, d: _asset(d, a)))
        stack.enter_context(patch(f"{MOD}.check_asset_permission", return_value=True))
        dynamo = stack.enter_context(patch(f"{MOD}.dynamodb"))
        dynamo.batch_get_item.side_effect = DeferringBatchReader()
        yield MagicMock(table=table, lookup=lookup, dynamo=dynamo)


@pytest.fixture
def listing_reader():
    """The links table for the flat listing, with the real ``batch_get_asset_details`` in play."""
    table = MagicMock()
    with ExitStack() as stack:
        stack.enter_context(patch(f"{MOD}.asset_links_table", table))
        stack.enter_context(
            patch(f"{MOD}.get_asset_details", side_effect=lambda a, d: _asset(d, a)))
        stack.enter_context(patch(f"{MOD}.check_asset_permission", return_value=True))
        # The retry interval is real time in the request path and is not what is asserted here.
        stack.enter_context(patch(f"{MOD}.BATCH_GET_RETRY_BASE_SECONDS", 0, create=True))
        yield table


@pytest.mark.unit
class TestChildTreeIsBounded:
    """One expansion per asset, not one per path, and a ceiling on both depth and node count."""

    def test_a_shared_subtree_is_expanded_once(self, tree_reader):
        """``current_path`` is copied per branch, so it prunes cycles and memoizes nothing."""
        reader = GraphReader(_lattice(LATTICE_LEVELS, LATTICE_WIDTH))
        tree_reader.table.query.side_effect = reader

        als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert len(reader.expansions) <= LATTICE_DISTINCT_ASSETS, (
            f"{len(reader.expansions)} expansions for {LATTICE_DISTINCT_ASSETS} distinct assets "
            f"({LATTICE_PATHS} distinct paths) -- shared subtrees are being re-expanded")

    def test_the_per_child_asset_lookup_is_bounded_by_distinct_assets(self, tree_reader):
        """One uncached ``get_item`` per child per path is the other half of the fan-out."""
        tree_reader.table.query.side_effect = GraphReader(_lattice(LATTICE_LEVELS, LATTICE_WIDTH))

        als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert tree_reader.lookup.call_count <= LATTICE_DISTINCT_ASSETS, (
            f"{tree_reader.lookup.call_count} asset lookups for {LATTICE_DISTINCT_ASSETS} "
            "distinct assets")

    def test_the_node_ceiling_truncates_and_says_so(self, tree_reader):
        tree_reader.table.query.side_effect = GraphReader(_lattice(LATTICE_LEVELS, LATTICE_WIDTH))

        with patch(f"{MOD}.MAX_TREE_NODES", 50):
            response = als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert _count_nodes(response.children) <= 50
        assert response.treeTruncated is True

    def test_the_depth_ceiling_truncates_a_deep_chain_and_says_so(self, tree_reader):
        tree_reader.table.query.side_effect = GraphReader(_chain(40))

        with patch(f"{MOD}.MAX_TREE_DEPTH", 10):
            response = als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert _depth(response.children) == 10
        assert response.treeTruncated is True

    def test_the_depth_ceiling_holds_for_a_reused_subtree(self, tree_reader):
        """A reused expansion is grafted in whole, so it is charged against the depth ceiling.

        The chain is met twice: once at depth 1 through the shortcut, where the walk caches a
        subtree seven levels deep, and once through the chain itself at depth 8. Reusing the cached
        subtree there emits a tree 15 deep against a ceiling of 10 -- and reports no truncation,
        because no single expansion ever walked past the ceiling.
        """
        tree_reader.table.query.side_effect = GraphReader(_chain_with_shortcut(15, 8))

        with patch(f"{MOD}.MAX_TREE_DEPTH", 10):
            response = als.get_asset_links_for_asset("root", DB, True, CLAIMS)
            depth = _depth(response.children)

        assert depth <= 10, f"{depth} levels emitted against a ceiling of 10"
        assert response.treeTruncated is True

    def test_an_untruncated_walk_reports_treeTruncated_false(self, tree_reader):
        tree_reader.table.query.side_effect = GraphReader(_chain(3))

        response = als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert _depth(response.children) == 3
        assert response.treeTruncated is False

    def test_a_plain_hierarchy_is_returned_whole(self, tree_reader):
        """Positive control: the ceilings and the memo must not change an ordinary hierarchy.

        ``c1`` is a child of the root AND of ``c2``, so the shared-subtree case is exercised: its
        grandchild has to appear under both parents even though it is expanded once.
        """
        tree_reader.table.query.side_effect = GraphReader({
            f"{DB}:root": [_child_link((DB, "root"), (DB, "c1")),
                           _child_link((DB, "root"), (DB, "c2"))],
            f"{DB}:c1": [_child_link((DB, "c1"), (DB, "g1"))],
            f"{DB}:c2": [_child_link((DB, "c2"), (DB, "c1"))],
        })

        response = als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert [node.assetId for node in response.children] == ["c1", "c2"]
        assert [node["assetId"] for node in _child_nodes(response.children[0])] == ["g1"]
        under_c2 = _child_nodes(response.children[1])
        assert [node["assetId"] for node in under_c2] == ["c1"]
        assert [node["assetId"] for node in _child_nodes(under_c2[0])] == ["g1"]

    def test_a_cycle_is_still_pruned(self, tree_reader):
        """Positive control: reusing an expansion must not defeat the path-based cycle guard."""
        tree_reader.table.query.side_effect = GraphReader({
            f"{DB}:root": [_child_link((DB, "root"), (DB, "c1"))],
            f"{DB}:c1": [_child_link((DB, "c1"), (DB, "root"))],
        })

        response = als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert [node.assetId for node in response.children] == ["c1"]
        under_c1 = _child_nodes(response.children[0])
        assert [node["assetId"] for node in under_c1] == ["root"]
        assert _child_nodes(under_c1[0]) == []


@pytest.mark.unit
class TestFailedTraversalIsNotAnEmptyTree:
    """A traversal failure must not be served as an authoritative empty hierarchy."""

    @staticmethod
    def _throttle_after_the_first_expansion():
        expansions = {"n": 0}

        def reader(**kwargs):
            if kwargs.get("IndexName") == "toAssetGSI":
                return {"Items": []}
            if "FilterExpression" not in kwargs:
                return {"Items": [_child_link((DB, "root"), (DB, "c1"))]}
            expansions["n"] += 1
            if expansions["n"] == 1:
                return {"Items": [_child_link((DB, "root"), (DB, "c1"))]}
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException",
                           "Message": "throttled"}},
                "Query")

        return reader

    def test_a_throttled_expansion_does_not_read_as_a_childless_asset(self, tree_reader):
        tree_reader.table.query.side_effect = self._throttle_after_the_first_expansion()

        with pytest.raises(VAMSGeneralErrorResponse):
            als.get_asset_links_for_asset("root", DB, True, CLAIMS)

    def test_a_throttled_expansion_is_not_answered_with_http_200(self, tree_reader):
        event = {
            "pathParameters": {"assetId": "root", "databaseId": DB},
            "queryStringParameters": {"childTreeView": "true"},
            "requestContext": {"http": {"method": "GET",
                                        "path": f"/database/{DB}/asset/root/asset-links"}},
            "headers": {"Authorization": "Bearer t"},
        }
        tree_reader.table.query.side_effect = self._throttle_after_the_first_expansion()

        with patch(f"{MOD}.request_to_claims", return_value=CLAIMS), \
                patch(f"{MOD}.CasbinEnforcer") as enforcer:
            enforcer.return_value.enforceAPI.return_value = True
            response = als.lambda_handler(event, MagicMock())

        assert response["statusCode"] != 200
        assert "Unable to build the asset link tree" in json.loads(response["body"])["message"]

    def test_a_childless_asset_still_answers_with_an_empty_tree(self, tree_reader):
        """Positive control: the error path must not turn "no children" into a failure."""
        tree_reader.table.query.side_effect = GraphReader({})

        response = als.get_asset_links_for_asset("root", DB, True, CLAIMS)

        assert response.children == []
        assert response.unauthorizedCounts.children == 0


@pytest.mark.unit
class TestUnprocessedKeysAreNotUnauthorized:
    """A key the batch read did not return is not a key the caller is denied."""

    @staticmethod
    def _two_related_links():
        return [_related_link("l1", (DB, "r1")), _related_link("l2", (DB, "r2"))]

    def _listing(self, listing_reader, reader):
        listing_reader.query.side_effect = _one_page(from_items=self._two_related_links())
        with patch(f"{MOD}.dynamodb") as dynamo:
            dynamo.batch_get_item.side_effect = reader
            return als.get_asset_links_for_asset("root", DB, False, CLAIMS)

    def test_a_deferred_key_is_re_requested_and_still_listed(self, listing_reader):
        reader = DeferringBatchReader(defer_keys={f"{DB}:r2"}, defer_rounds=1)

        response = self._listing(listing_reader, reader)

        assert sorted(node.assetId for node in response.related) == ["r1", "r2"]
        assert response.unauthorizedCounts.related == 0
        assert len(reader.requested) == 2, "the deferred key was never re-requested"

    def test_a_key_unretrieved_after_the_budget_is_not_counted_unauthorized(self, listing_reader):
        reader = DeferringBatchReader(defer_keys={f"{DB}:r2"}, defer_rounds=99)

        response = self._listing(listing_reader, reader)

        assert [node.assetId for node in response.related] == ["r1"]
        assert response.unresolvedCounts.related == 1
        assert response.unauthorizedCounts.related == 0
        assert len(reader.requested) == als.MAX_BATCH_GET_ATTEMPTS

    def test_a_batch_with_nothing_deferred_is_requested_once(self, listing_reader):
        """Positive control: the happy path must not pick up a retry."""
        reader = DeferringBatchReader()

        response = self._listing(listing_reader, reader)

        assert sorted(node.assetId for node in response.related) == ["r1", "r2"]
        assert len(reader.requested) == 1
        assert response.unauthorizedCounts.related == 0

    def test_a_child_the_tree_walk_could_not_read_is_not_counted_unauthorized(self, tree_reader):
        """The tree walk resolves its own children, so it needs the same distinction.

        ``c1`` stays deferred past the retry budget while ``c2`` resolves: the sibling must still be
        emitted, and the missing one counted as unresolved rather than denied. Asserted against
        ``build_child_tree`` rather than the whole listing, because the flat relationship pass over
        the root's own links counts them a second time into the same models.
        """
        tree_reader.table.query.side_effect = GraphReader({
            f"{DB}:root": [_child_link((DB, "root"), (DB, "c1")),
                           _child_link((DB, "root"), (DB, "c2"))],
        })
        tree_reader.dynamo.batch_get_item.side_effect = DeferringBatchReader(
            defer_keys={f"{DB}:c1"}, defer_rounds=99)
        unauthorized = als.UnauthorizedCountsModel()
        unresolved = als.UnresolvedCountsModel()

        with patch(f"{MOD}.BATCH_GET_RETRY_BASE_SECONDS", 0):
            children, truncated = als.build_child_tree(
                "root", DB, CLAIMS, unauthorized, unresolved)

        assert [node.assetId for node in children] == ["c2"]
        assert unresolved.children == 1
        assert unauthorized.children == 0
        assert truncated is False

    def test_a_denied_link_is_still_counted_unauthorized(self, listing_reader):
        """Positive control: a real permission denial must keep reading as a denial.

        Asserted on ``unauthorizedCounts`` alone, so it holds whether or not a separate unresolved
        counter exists -- the point is that splitting the two did not empty this one.
        """
        listing_reader.query.side_effect = _one_page(
            from_items=[_related_link("l1", (DB, "r1"))])

        with patch(f"{MOD}.dynamodb") as dynamo, \
                patch(f"{MOD}.check_asset_permission",
                      side_effect=lambda asset, claims, action="GET": asset["assetId"] == "root"):
            dynamo.batch_get_item.side_effect = DeferringBatchReader()
            response = als.get_asset_links_for_asset("root", DB, False, CLAIMS)

        assert response.related == []
        assert response.unauthorizedCounts.related == 1
