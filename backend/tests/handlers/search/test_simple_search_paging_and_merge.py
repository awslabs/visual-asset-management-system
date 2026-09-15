# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-004 (HIGH): POST /search/simple must be able to return file-index hits.

Three separate mechanisms combined to make the file half of the dual index unreachable:

1.  ``SearchRequestModel.from_`` carries ``alias="from"`` and the model leaves
    ``allow_population_by_field_name`` False, so the compatibility model built as
    ``SearchRequestModel(from_=...)`` silently dropped the offset under ``extra='ignore'``
    and ``_apply_pagination`` always sliced ``hits[0:size]``.
2.  the offset was ALSO applied server-side in both index queries, so the two
    implementations of paging fought each other, and
3.  ``search_dual_index`` appended every file hit after every asset hit with the
    cross-index re-sort commented out, so the head of that concatenation -- the page the
    caller receives -- is asset hits only whenever the asset index fills it.

A fourth, named by the finding's verifier: a ``fileKey``/``fileExtension``-only request
produces no clause at all on the asset index, so the builder fell through to ``match_all``
and answered a file lookup with a page of every accessible asset.

## Why the assertions are shaped this way

The reachability assertions read each hit's ``str_rectype`` discriminator rather than
counting hits, because which index a hit came from is the whole question. Each negative
assertion is paired with a positive control in the same class -- an offset test that
returned [] would satisfy "no unrelated asset hits" without proving anything.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


@pytest.fixture
def search_module():
    """The real search module, loaded by file path with boto3 stubbed.

    The root conftest registers mock ``handlers``/``common`` packages that shadow the real
    ones, so a plain import yields ``tests/mocks/handlers/search/search.py``. Same approach
    as ``test_database_prefilter_object_type.py``.
    """
    saved = {
        name: sys.modules.get(name)
        for name in ("handlers.auth", "handlers.authz", "common.dynamodb")
    }

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub

    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_simple_paging", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _hit(index_type, ordinal, score):
    return {
        "_index": f"vams-{index_type}",
        "_id": f"{index_type}-{ordinal}",
        "_score": score,
        "_source": {
            "str_rectype": index_type,
            "str_databaseid": "db1",
            "str_assetname": f"{index_type}-{ordinal:04d}",
            "list_tags": [],
        },
    }


def _index_page(index_type, count, score_offset):
    """One index's already-sorted answer: descending _score, as OpenSearch returns it.

    The half-point score offset makes the two indexes' scores interleave, which is the
    realistic case and the only one where the merge is observable -- identical scores would
    let a stable sort keep the concatenation order and the assertion would pass or fail for
    reasons unrelated to the merge.
    """
    hits = [
        _hit(index_type, ordinal, count - ordinal + score_offset)
        for ordinal in range(count)
    ]
    return {
        "took": 3,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {"hits": hits, "total": {"value": count, "relation": "eq"}},
        "aggregations": {},
    }


def _simple_post(module, body, asset_count, file_count):
    """Drive handle_simple_post_request through the REAL dual-index search and processor.

    Only the OpenSearch client and the query builder are stubbed. Stubbing
    ``search_dual_index`` itself would bypass the cross-index merge, so the assertion would
    read the concatenation the merge exists to replace.
    """
    manager = module.DualIndexSearchManager()
    manager.client = MagicMock()
    manager.asset_index = "vams-asset"
    manager.file_index = "vams-file"

    def _search(body=None, index=None):
        if index == "vams-asset":
            return _index_page("asset", asset_count, 0.0)
        return _index_page("file", file_count, 0.5)

    manager.client.search.side_effect = _search

    query_builder = MagicMock()
    query_builder.build_simple_dual_index_queries.return_value = (
        {"sort": ["_score"]},
        {"sort": ["_score"]},
    )

    processor = module.DualIndexResponseProcessor(module.DatabaseAccessManager())

    event = {
        "requestContext": {"http": {"method": "POST", "path": "/search/simple"}},
        "body": json.dumps(body),
        "headers": {"authorization": "Bearer test-token"},
    }
    response = module.handle_simple_post_request(
        event, manager, query_builder, processor, {"tokens": ["user1"]}
    )
    return response, json.loads(response["body"])


def _complex_post(module, body, asset_count, file_count):
    """The same drive-through for ``POST /search``, which shares the cross-index merge.

    ``search_dual_index`` and ``DualIndexResponseProcessor`` are the same objects on both
    endpoints, so the merge reaches the complex path too -- and its page came off the same
    asset-then-file concatenation. Only the query builder and the OpenSearch client are
    stubbed, for the same reason as above.
    """
    manager = module.DualIndexSearchManager()
    manager.client = MagicMock()
    manager.asset_index = "vams-asset"
    manager.file_index = "vams-file"

    def _search(body=None, index=None):
        if index == "vams-asset":
            return _index_page("asset", asset_count, 0.0)
        return _index_page("file", file_count, 0.5)

    manager.client.search.side_effect = _search

    query_builder = MagicMock()
    query_builder.build_dual_index_queries.return_value = (
        {"sort": ["_score"]},
        {"sort": ["_score"]},
    )

    processor = module.DualIndexResponseProcessor(module.DatabaseAccessManager())

    event = {
        "requestContext": {"http": {"method": "POST", "path": "/search"}},
        "body": json.dumps(body),
        "headers": {"authorization": "Bearer test-token"},
    }
    response = module.handle_post_request(
        event, manager, query_builder, processor, {"tokens": ["user1"]}
    )
    return response, json.loads(response["body"])


def _returned_index_types(body):
    """Which index each returned hit came from, read off the _source discriminator."""
    return {hit["_source"]["str_rectype"] for hit in body["hits"]["hits"]}


def _expected_merged_ids(asset_count, file_count):
    """The one global order the two indexes' answers must merge into: _score, descending.

    Derived from the scores ``_index_page`` hands out rather than from the sort the handler
    runs, so it states the contract instead of restating the implementation. The half-point
    offset makes every score distinct, so the expected order does not depend on the sort
    being stable.
    """
    scored = [(f"asset-{o}", asset_count - o + 0.0) for o in range(asset_count)]
    scored += [(f"file-{o}", file_count - o + 0.5) for o in range(file_count)]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [hit_id for hit_id, _ in scored]


@pytest.mark.unit
class TestSimpleSearchReachesTheFileIndex:
    def test_file_hits_survive_a_full_page_of_asset_hits(self, search_module):
        response, body = _simple_post(
            search_module, {"query": "x", "size": 100}, asset_count=100, file_count=100
        )

        assert response["statusCode"] == 200
        assert "file" in _returned_index_types(body), (
            "no file-index hit reached the caller; the page is the head of the "
            "asset-then-file concatenation"
        )

    def test_asset_hits_are_still_returned(self, search_module):
        """Positive control: the merge must not simply swap which index is lost."""
        _, body = _simple_post(
            search_module, {"query": "x", "size": 100}, asset_count=100, file_count=100
        )
        assert "asset" in _returned_index_types(body)

    def test_the_page_is_ordered_by_score_across_both_indexes(self, search_module):
        _, body = _simple_post(
            search_module, {"query": "x", "size": 100}, asset_count=100, file_count=100
        )
        scores = [hit["_score"] for hit in body["hits"]["hits"]]
        assert scores == sorted(scores, reverse=True)

    def test_the_response_body_carries_the_shard_tally(self, search_module):
        """S2-BACKEND-071 at the handler: the field must serialize under its alias.

        The model-level round trip is asserted in
        ``tests/models/test_search_response_shards_contract.py``; this is the half only the
        handler can answer -- that it serializes ``by_alias``, so the wire key is the
        OpenSearch one and not the pydantic-legal field name.
        """
        _, body = _simple_post(
            search_module, {"query": "x", "size": 5}, asset_count=2, file_count=2
        )
        assert "_shards" in body, "the shard tally is absent from the response body"
        assert "shards" not in body
        assert body["_shards"]["successful"] == 2


@pytest.mark.unit
class TestSimpleSearchOffsetIsApplied:
    def test_the_offset_selects_a_later_page(self, search_module):
        _, body = _simple_post(
            search_module,
            {"query": "x", "from": 100, "size": 5},
            asset_count=200,
            file_count=0,
        )
        ids = [hit["_id"] for hit in body["hits"]["hits"]]
        assert ids == [f"asset-{i}" for i in range(100, 105)]

    def test_the_first_page_is_the_head(self, search_module):
        """Positive control for the offset test above."""
        _, body = _simple_post(
            search_module, {"query": "x", "size": 5}, asset_count=200, file_count=0
        )
        ids = [hit["_id"] for hit in body["hits"]["hits"]]
        assert ids == [f"asset-{i}" for i in range(0, 5)]

    def test_the_compatibility_model_must_be_built_under_the_alias(self, search_module):
        """The model-level fact the offset depends on.

        ``allow_population_by_field_name`` is False on SearchRequestModel, so a ``from_=``
        keyword is swallowed by ``extra='ignore'`` and reads back as None.
        """
        SearchRequestModel = search_module.SearchRequestModel

        assert SearchRequestModel(**{"from": 100}).from_ == 100
        assert SearchRequestModel(from_=100).from_ is None


@pytest.mark.unit
class TestADeepPageOfADualIndexAnswer:
    """All three mechanisms at once, which neither class above reaches.

    The offset tests run with ``file_count=0`` and the reachability tests run at offset 0, so
    each isolates one mechanism. A deep page of a DUAL-index answer is the only shape that
    needs all three to hold together: the offset has to be read off the alias, applied
    exactly once, and applied to the MERGED sequence rather than to either index's own answer.
    """

    def test_a_deep_page_is_the_slice_of_the_merged_sequence(self, search_module):
        _, body = _simple_post(
            search_module,
            {"query": "x", "from": 100, "size": 5},
            asset_count=200,
            file_count=200,
        )
        ids = [hit["_id"] for hit in body["hits"]["hits"]]
        assert ids == _expected_merged_ids(200, 200)[100:105], (
            "the deep page is not the merged sequence's slice; the offset was dropped, "
            f"applied twice, or applied to one index's own answer: {ids}"
        )

    def test_a_deep_page_carries_hits_from_both_indexes(self, search_module):
        _, body = _simple_post(
            search_module,
            {"query": "x", "from": 100, "size": 5},
            asset_count=200,
            file_count=200,
        )
        assert _returned_index_types(body) == {"asset", "file"}

    def test_a_deep_page_is_full(self, search_module):
        """Positive control: the offset selects a page, it does not empty one.

        Without this an offset assertion would be satisfied by an empty response, which is
        the outage a paging change is most likely to cause.
        """
        _, body = _simple_post(
            search_module,
            {"query": "x", "from": 100, "size": 5},
            asset_count=200,
            file_count=200,
        )
        assert len(body["hits"]["hits"]) == 5

    def test_a_deep_page_the_shorter_index_cannot_reach(self, search_module):
        """Variation: one index shorter than the offset.

        Every asset hit outranks nothing here -- 60 asset hits all score below the first 240
        file hits -- so the requested page falls entirely inside the file index. That page is
        one neither index's own offset could produce and the concatenation could never reach.
        """
        _, body = _simple_post(
            search_module,
            {"query": "x", "from": 100, "size": 10},
            asset_count=60,
            file_count=300,
        )
        ids = [hit["_id"] for hit in body["hits"]["hits"]]
        assert ids == _expected_merged_ids(60, 300)[100:110]
        assert _returned_index_types(body) == {"file"}

    def test_a_deep_page_of_a_single_index_answer(self, search_module):
        """Variation: entityTypes narrowing the answer to one index.

        The merge is skipped when only one index contributes, so this arm isolates the
        pagination step -- the half that had the offset applied server-side as well.
        """
        _, body = _simple_post(
            search_module,
            {"query": "x", "from": 100, "size": 5, "entityTypes": ["file"]},
            asset_count=200,
            file_count=200,
        )
        ids = [hit["_id"] for hit in body["hits"]["hits"]]
        assert ids == [f"file-{i}" for i in range(100, 105)]


@pytest.mark.unit
class TestTheComplexEndpointSharesTheMerge:
    """``POST /search`` collects its hits through the same two calls.

    The merge lives in ``search_dual_index`` and the page is cut in
    ``DualIndexResponseProcessor``, both shared, so the complex endpoint's page was the head
    of the same asset-then-file concatenation. Its offset was always read off a model parsed
    from the request body, so the alias half never applied to it -- which is why the merge is
    the only half asserted here.
    """

    def test_the_page_is_ordered_across_both_indexes(self, search_module):
        response, body = _complex_post(
            search_module,
            {"query": "x", "from": 100, "size": 5},
            asset_count=200,
            file_count=200,
        )
        assert response["statusCode"] == 200
        ids = [hit["_id"] for hit in body["hits"]["hits"]]
        assert ids == _expected_merged_ids(200, 200)[100:105], (
            f"the complex endpoint's page is not the merged sequence's slice: {ids}"
        )
        assert _returned_index_types(body) == {"asset", "file"}

    def test_the_page_is_full(self, search_module):
        """Positive control: the merge selects a page, it does not empty one."""
        _, body = _complex_post(
            search_module,
            {"query": "x", "from": 100, "size": 5},
            asset_count=200,
            file_count=200,
        )
        assert len(body["hits"]["hits"]) == 5


@pytest.mark.unit
class TestSimpleSearchQueryPagesInPythonOnly:
    def _query(self, module, **request_kwargs):
        builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
        request = module.SimpleSearchRequestModel(**request_kwargs)
        return builder._build_simple_index_query(request, ["db1"], "file")

    def test_the_index_query_fetches_from_zero(self, search_module):
        query = self._query(search_module, **{"query": "x", "from": 100, "size": 10})
        assert query["from"] == 0, (
            "the offset is applied server-side as well as in Python, so the requested "
            "page is skipped twice"
        )

    def test_the_index_query_buffers_for_the_authorization_filter(self, search_module):
        query = self._query(search_module, **{"query": "x", "from": 100, "size": 10})
        assert query["size"] >= 110, (
            "fewer records are fetched than the requested page needs, so the offset "
            "cannot be honoured after per-hit filtering"
        )
        assert query["size"] <= search_module.OPENSEARCH_MAX_RESULT_WINDOW


@pytest.mark.unit
class TestCombinedResultsAreMergeSorted:
    def test_both_indexes_hits_are_interleaved_by_the_sort_key(self, search_module):
        manager = search_module.DualIndexSearchManager()
        manager.client = MagicMock()
        manager.asset_index = "vams-asset"
        manager.file_index = "vams-file"

        def _search(body=None, index=None):
            kind = "asset" if index == "vams-asset" else "file"
            scores = {"asset": [3.0, 1.0], "file": [4.0, 2.0]}[kind]
            hits = [_hit(kind, ordinal, score) for ordinal, score in enumerate(scores)]
            return {
                "took": 1,
                "timed_out": False,
                "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
                "hits": {"hits": hits, "total": {"value": len(hits)}},
            }

        manager.client.search.side_effect = _search
        query = {"sort": ["_score"]}
        results = manager.search_dual_index(query, query, ["asset", "file"])

        scores = [hit["_score"] for hit in results["hits"]["hits"]]
        assert scores == sorted(scores, reverse=True), (
            f"the two indexes' hits were concatenated, not merged: {scores}"
        )
        assert [hit["_index_type"] for hit in results["hits"]["hits"]] == [
            "file", "asset", "file", "asset",
        ]

    def test_a_single_index_answer_is_left_in_its_own_order(self, search_module):
        """Control: OpenSearch already ordered a single index, so nothing is re-sorted."""
        manager = search_module.DualIndexSearchManager()
        manager.client = MagicMock()
        manager.asset_index = "vams-asset"
        manager.file_index = "vams-file"

        hits = [_hit("asset", ordinal, score)
                for ordinal, score in enumerate([1.0, 5.0, 3.0])]
        manager.client.search.return_value = {
            "took": 1,
            "timed_out": False,
            "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
            "hits": {"hits": hits, "total": {"value": len(hits)}},
        }

        results = manager.search_dual_index({"sort": ["_score"]}, {}, ["asset"])
        assert [hit["_score"] for hit in results["hits"]["hits"]] == [1.0, 5.0, 3.0]


@pytest.mark.unit
class TestCriteriaForeignToAnIndexMatchNothing:
    """A fileKey lookup must not answer with every accessible asset."""

    def _clause(self, module, index_type, **request_kwargs):
        builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
        request = module.SimpleSearchRequestModel(**request_kwargs)
        return builder._build_simple_query_clause(request, ["db1"], index_type)

    def test_a_file_key_lookup_matches_no_asset(self, search_module):
        clause = self._clause(search_module, "asset", fileKey="/folder/part.glb")
        assert clause == {"match_none": {}}

    def test_a_file_extension_lookup_matches_no_asset(self, search_module):
        clause = self._clause(search_module, "asset", fileExtension="glb")
        assert clause == {"match_none": {}}

    def test_an_asset_type_lookup_matches_no_file(self, search_module):
        clause = self._clause(search_module, "file", assetType="model")
        assert clause == {"match_none": {}}

    def test_the_file_index_still_answers_the_file_key_lookup(self, search_module):
        """Positive control: the criterion is applied where it belongs."""
        clause = self._clause(search_module, "file", fileKey="/folder/part.glb")
        assert clause != {"match_none": {}}
        assert json.dumps(clause).count("/folder/part.glb") == 1

    def test_a_criterion_both_indexes_carry_reaches_both(self, search_module):
        for index_type in ("asset", "file"):
            clause = self._clause(search_module, index_type, assetName="widget")
            assert clause != {"match_none": {}}

    def test_a_mixed_request_is_not_narrowed(self, search_module):
        """One applicable criterion is enough to keep the index in the answer."""
        clause = self._clause(
            search_module, "asset", assetName="widget", fileExtension="glb"
        )
        assert clause != {"match_none": {}}

    def test_a_criterion_free_browse_still_matches_everything(self, search_module):
        clause = self._clause(search_module, "asset")
        assert clause != {"match_none": {}}

    def test_a_file_key_lookup_scoped_to_a_database_matches_no_asset(self, search_module):
        """The headline shape: the UI sends databaseId alongside the file lookup.

        `databaseId` is applied as a bool filter on str_databaseid, never as a search clause,
        so it cannot select a record on either index -- it can only narrow a selection already
        made. A narrowing that asked merely "was any shared criterion supplied?" therefore let
        this request through, leaving the asset query with filters and no match clause; the
        builder then falls back to match_all and the caller's page is every accessible asset
        in the database.
        """
        clause = self._clause(
            search_module, "asset", fileKey="/folder/part.glb", databaseId="db1"
        )
        assert clause == {"match_none": {}}, (
            f"a {{fileKey, databaseId}} lookup still reaches the asset index: "
            f"{json.dumps(clause)[:600]}"
        )

    def test_the_same_request_is_answered_by_the_file_index(self, search_module):
        """Positive control: the request is not narrowed away entirely."""
        clause = self._clause(
            search_module, "file", fileKey="/folder/part.glb", databaseId="db1"
        )
        assert clause != {"match_none": {}}
        rendered = json.dumps(clause)
        assert rendered.count("/folder/part.glb") == 1
        assert "str_databaseid.keyword" in rendered, (
            f"the databaseId filter was dropped from the index that can answer: {rendered}"
        )

    def test_an_asset_type_lookup_scoped_to_a_database_matches_no_file(self, search_module):
        """The mirror case, so the fix is not one-directional."""
        clause = self._clause(search_module, "file", assetType="model", databaseId="db1")
        assert clause == {"match_none": {}}

    def test_a_database_only_browse_still_matches_everything_on_both_indexes(
        self, search_module
    ):
        """The over-narrowing catcher: databaseId alone is a browse, not a foreign criterion.

        If the filter-only classification were folded into the ONLY lists instead, a
        database browse would answer with match_none on both indexes and the database page
        would come back empty.
        """
        for index_type in ("asset", "file"):
            clause = self._clause(search_module, index_type, databaseId="db1")
            assert clause != {"match_none": {}}, (
                f"a databaseId-only browse was narrowed away on the {index_type} index"
            )

    def test_the_filter_only_criteria_are_never_built_as_a_search_clause(self, search_module):
        """The premise of the classification, read off the built query rather than restated.

        A criterion is filter-only because the builder puts it in `filter`, not in `must`.
        Asserted against a request that also carries a native match criterion, so the query
        has both sections and the placement is observable.
        """
        builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
        for criterion in builder.FILTER_ONLY_CRITERIA:
            request = search_module.SimpleSearchRequestModel(
                assetName="widget", **{criterion: "db1"}
            )
            clause = builder._build_simple_query_clause(request, ["db1"], "asset")
            bool_query = clause["bool"]
            assert "db1" in json.dumps(bool_query.get("filter", [])), (
                f"{criterion} is classified filter-only but appears in no filter clause: "
                f"{json.dumps(bool_query)}"
            )
            assert "db1" not in json.dumps(bool_query.get("must", [])), (
                f"{criterion} is classified filter-only but the builder emits it as a search "
                f"clause: {json.dumps(bool_query)}"
            )

    def test_every_criterion_is_matchable_on_at_least_one_index(self, search_module):
        """Nothing may be foreign to both indexes, or it could never return a hit."""
        builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
        asset_native = set(builder._matching_criteria_for_index("asset"))
        file_native = set(builder._matching_criteria_for_index("file"))
        for criterion in builder.SIMPLE_SEARCH_CRITERIA:
            if criterion in builder.FILTER_ONLY_CRITERIA:
                assert criterion not in asset_native and criterion not in file_native
                continue
            assert criterion in asset_native or criterion in file_native, (
                f"{criterion} can match on neither index, so supplying it always yields "
                f"match_none"
            )

    def test_the_criterion_classification_matches_the_index_mappings(self, search_module):
        """The premise: a criterion is foreign because the index has no such field.

        Read off ``models/indexing.py`` rather than restated, so the constant lists cannot
        drift from the mappings the indexer actually creates.
        """
        import re

        indexing_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "backend", "models", "indexing.py"
        )
        source = open(os.path.abspath(indexing_path), encoding="utf-8").read()
        file_block = source[
            source.index("class FileIndexMapping"): source.index("class AssetIndexMapping")
        ]
        asset_block = source[source.index("class AssetIndexMapping"):]

        def mapped(block, field):
            return bool(re.search(r'"' + field + r'"\s*:', block))

        builder = search_module.SimpleSearchQueryBuilder
        criterion_field = {
            "fileKey": "str_key",
            "fileExtension": "str_fileext",
            "assetType": "str_assettype",
            "assetName": "str_assetname",
            "assetId": "str_assetid",
        }

        for criterion in builder.FILE_ONLY_CRITERIA:
            field = criterion_field[criterion]
            assert mapped(file_block, field)
            assert not mapped(asset_block, field), (
                f"{criterion} is treated as file-only but the asset index maps {field}"
            )

        for criterion in builder.ASSET_ONLY_CRITERIA:
            field = criterion_field[criterion]
            assert mapped(asset_block, field)
            assert not mapped(file_block, field), (
                f"{criterion} is treated as asset-only but the file index maps {field}"
            )

        for criterion in ("assetName", "assetId"):
            assert criterion not in builder.FILE_ONLY_CRITERIA
            assert criterion not in builder.ASSET_ONLY_CRITERIA
            field = criterion_field[criterion]
            assert mapped(file_block, field) and mapped(asset_block, field)

    def test_the_criteria_list_matches_the_request_model(self, search_module):
        """A new request field must be classified, not silently ignored by the guard."""
        model_fields = set(search_module.SimpleSearchRequestModel.__fields__)
        declared = set(search_module.SimpleSearchQueryBuilder.SIMPLE_SEARCH_CRITERIA)
        assert declared <= model_fields, declared - model_fields

        # The rest of the model: paging, archive inclusion, the geo filter (both indexes
        # carry geo_MD_location) and entityTypes, which selects which index is queried at
        # all rather than describing what to match.
        non_criteria = {"includeArchived", "geoSearch", "from_", "size", "entityTypes"}
        assert model_fields - declared == non_criteria, (
            f"unclassified simple-search fields: {model_fields - declared - non_criteria}"
        )
