# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keyword search pages neither repeat nor skip a record when hits tie on every requested sort key (#1145).

Every page is a separate request that re-runs the query from offset 0 and cuts its page out of the
answer. OpenSearch returns documents equal on every sort key in no defined order, and that order can
change between requests (a document is rewritten, another shard copy answers). Each keyword search
sort therefore ends with keys that are unique per document. The fake below applies each query's sort
the way OpenSearch does and returns ties in a different order on every request, so a sort without a
unique last key shows up as repeated and missing records across pages.
"""

import json

import pytest

from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager, _values
from backend.tests.handlers.authz.search_authz_harness import ALLOW_ALL, CLAIMS, ROLE, load_search_module, policies
from backend.tests.handlers.authz.test_search_counts_honour_permissions import _asset, _file, _install_rows, _manager


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_sort_tiebreaker")


def _entry(entry):
    """(field, descending) of one sort entry, read independently of the handler."""
    if isinstance(entry, str):
        return entry, entry == "_score"
    (field, config), = entry.items()
    order = config.get("order") if isinstance(config, dict) else config
    return field, (order == "desc") if order in ("asc", "desc") else field == "_score"


def _opensearch_key(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return value


def _opensearch_sort(docs, field, descending):
    """``docs`` ordered on ``field`` as OpenSearch orders them: a document without a value last in either
    direction, a document with several values by its smallest ascending and its largest descending.
    Stable, so the order of documents equal on ``field`` is kept."""
    present = [d for d in docs if _values(d, field)]
    missing = [d for d in docs if not _values(d, field)]
    pick = max if descending else min
    present.sort(key=lambda d: pick(_opensearch_key(v) for v in _values(d, field)), reverse=descending)
    return present + missing


class _TiesInAnyOrder(FakeSearchManager):
    """The in-memory search with each query's sort applied, every document scoring the same, and the
    documents equal on every sort key in an order that differs per request and per window."""

    def __init__(self, module, assets, files, request_number):
        super().__init__(module, assets, files)
        self._request_number = request_number

    def _answer(self, query, index_type):
        sources = self.indexes[index_type]
        if sources:
            # This request's storage order, standing in for rewritten documents or another shard copy
            shift = (self._request_number * 7 + len(self.queries) * 3) % len(sources)
            ordered = sources[shift:] + sources[:shift]
            if self._request_number % 2:
                ordered.reverse()
            for entry in reversed(query.get("sort") or []):
                field, descending = _entry(entry)
                if field != "_score":
                    ordered = _opensearch_sort(ordered, field, descending)
            self.indexes[index_type] = ordered
        try:
            return super()._answer(query, index_type)
        finally:
            self.indexes[index_type] = sources


def _run(module, monkeypatch, fake, body, simple=False):
    manager = _manager(module, monkeypatch)
    processor = module.DualIndexResponseProcessor(manager)
    event = {"body": json.dumps(body)}
    if simple:
        response = module.handle_simple_post_request(event, fake, module.SimpleSearchQueryBuilder(manager),
                                                     processor, CLAIMS)
    else:
        response = module.handle_post_request(event, fake, module.DualIndexQueryBuilder(manager), processor, CLAIMS)
    assert response["statusCode"] == 200, response["body"]
    return json.loads(response["body"])


def _walk(module, monkeypatch, assets, files, size, entity_types, simple=False, sort=None):
    """The ids of every page of ``size``, followed as a client follows them, one request per page."""
    ids, offset, request_number = [], 0, 0
    while True:
        body = {"entityTypes": list(entity_types), "from": offset, "size": size}
        if sort is not None:
            body["sort"] = sort
        if not simple:
            body["aggregations"] = False
        fake = _TiesInAnyOrder(module, assets, files, request_number)
        page = [h["_id"] for h in _run(module, monkeypatch, fake, body, simple=simple)["hits"]["hits"]]
        if not page:
            return ids
        ids.extend(page)
        offset += size
        request_number += 1
        assert request_number < 200, "paging did not end"


ASSETS = [_asset(n, "typeX", ["t1"]) for n in range(37)]
FILES = [_file(n, a) for n, a in enumerate(ASSETS)]
# Twenty-nine files of one asset: every file ties on the asset's name
ONE_ASSET = [_asset(900, "typeX", ["t1"], name="shared-name")]
ONE_ASSET_FILES = [dict(_file(900 + n, ONE_ASSET[0]), str_key=f"/part-{n:02d}.glb") for n in range(29)]


def _assert_each_once(ids, expected):
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    missing = sorted(set(expected) - set(ids))
    assert not repeated and not missing, f"repeated {repeated}, never returned {missing}"
    assert len(ids) == len(expected)


@pytest.mark.unit
class TestPagesListEachRecordOnce:
    @pytest.mark.parametrize("size", [4, 10])
    def test_asset_pages_of_a_search_without_text(self, search_module, monkeypatch, size):
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            ids = _walk(search_module, monkeypatch, ASSETS, [], size, ("asset",))
        _assert_each_once(ids, [a["_id"] for a in ASSETS])

    @pytest.mark.parametrize("size", [4, 10])
    def test_file_pages_of_a_search_without_text(self, search_module, monkeypatch, size):
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            ids = _walk(search_module, monkeypatch, ASSETS, FILES, size, ("file",))
        _assert_each_once(ids, [f["_id"] for f in FILES])

    def test_pages_over_both_indexes(self, search_module, monkeypatch):
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            ids = _walk(search_module, monkeypatch, ASSETS, FILES, 6, ("asset", "file"))
        _assert_each_once(ids, [d["_id"] for d in ASSETS + FILES])

    @pytest.mark.parametrize("size", [3, 5])
    def test_simple_search_pages(self, search_module, monkeypatch, size):
        _install_rows(search_module, monkeypatch, ASSETS)
        with policies([ALLOW_ALL], (ROLE,)):
            ids = _walk(search_module, monkeypatch, ASSETS, FILES, size, ("file",), simple=True)
        _assert_each_once(ids, [f["_id"] for f in FILES])

    def test_files_of_one_asset_sorted_by_asset_name(self, search_module, monkeypatch):
        """Every file of an asset carries the asset's name, so a sort on it ties on every file."""
        _install_rows(search_module, monkeypatch, ONE_ASSET)
        sort = [{"field": "str_assetname", "order": "asc"}]
        with policies([ALLOW_ALL], (ROLE,)):
            ids = _walk(search_module, monkeypatch, ONE_ASSET, ONE_ASSET_FILES, 4, ("file",), sort=sort)
        _assert_each_once(ids, [f["_id"] for f in ONE_ASSET_FILES])

    def test_a_descending_sort_on_a_tied_field(self, search_module, monkeypatch):
        _install_rows(search_module, monkeypatch, ASSETS)
        sort = [{"field": "str_assettype", "order": "desc"}]
        with policies([ALLOW_ALL], (ROLE,)):
            ids = _walk(search_module, monkeypatch, ASSETS, [], 5, ("asset",), sort=sort)
        _assert_each_once(ids, [a["_id"] for a in ASSETS])


@pytest.mark.unit
class TestTheSortEndsWithAUniqueKey:
    def _request(self, module, **body):
        return module.SearchRequestModel(**body)

    def test_the_default_sort_of_each_index(self, search_module):
        builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
        assert builder._build_sort_config(None, "asset") == ["_score"] + [
            {"str_databaseid.keyword": {"order": "asc"}}, {"str_assetid.keyword": {"order": "asc"}},
            {"bool_archived": {"order": "asc"}}]
        assert builder._build_sort_config(["_score"], "file") == ["_score"] + [
            {"str_databaseid.keyword": {"order": "asc"}}, {"str_assetid.keyword": {"order": "asc"}},
            {"bool_archived": {"order": "asc"}}, {"str_key.keyword": {"order": "asc"}}]

    def test_a_key_the_caller_names_keeps_its_direction_and_is_not_repeated(self, search_module):
        builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
        sort = builder._build_sort_config([{"field": "str_key", "order": "desc"}], "file")
        assert sort == [
            {"str_key.keyword": {"order": "desc"}}, {"str_databaseid.keyword": {"order": "asc"}},
            {"str_assetid.keyword": {"order": "asc"}}, {"bool_archived": {"order": "asc"}}]

    def test_the_simple_search_sort(self, search_module):
        builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
        request = search_module.SimpleSearchRequestModel(query="x")
        for index_type in ("asset", "file"):
            sort = builder._build_simple_index_query(request, ["db1"], index_type)["sort"]
            assert sort[0] == "_score"
            assert [next(iter(e)) for e in sort[1:]] == list(search_module.SORT_TIEBREAKER_FIELDS[index_type])

    def test_the_file_keys_extend_the_asset_keys(self, search_module):
        """The merge of a search over both indexes applies the file query's sort to both; it can order
        two asset hits as the asset index did only when the asset keys are the file keys' prefix."""
        keys = search_module.SORT_TIEBREAKER_FIELDS
        assert keys["file"][: len(keys["asset"])] == keys["asset"]


def _hit(index_type, doc_id, **source):
    return {"_id": doc_id, "_index_type": index_type, "_score": 1.0, "_source": source}


@pytest.mark.unit
class TestTheMergeKeepsEachIndexOrder:
    """The merge of the two indexes' answers must order two hits of one index as that index did."""

    def _merge(self, module, hits, sort):
        manager = module.DualIndexSearchManager.__new__(module.DualIndexSearchManager)
        return [h["_id"] for h in manager._sort_combined_results(hits, sort)]

    def test_a_hit_without_the_sort_value_stays_last_ascending(self, search_module):
        sort = [{"str_description.keyword": {"order": "asc"}}] + [
            {f: {"order": "asc"}} for f in search_module.SORT_TIEBREAKER_FIELDS["file"]]
        # The asset index's order for this sort: the asset without a description last
        assets = [_hit("asset", "a1", str_description="alpha", str_databaseid="d", str_assetid="a1"),
                  _hit("asset", "a2", str_description="beta", str_databaseid="d", str_assetid="a2"),
                  _hit("asset", "a0", str_databaseid="d", str_assetid="a0")]
        files = [_hit("file", "f0", str_databaseid="d", str_assetid="a0", str_key="/x")]
        merged = self._merge(search_module, assets + files, sort)
        assert [i for i in merged if i.startswith("a")] == ["a1", "a2", "a0"]

    def test_a_hit_without_the_sort_value_stays_last_descending(self, search_module):
        sort = [{"str_description.keyword": {"order": "desc"}}]
        assets = [_hit("asset", "a2", str_description="beta"), _hit("asset", "a1", str_description="alpha"),
                  _hit("asset", "a0")]
        merged = self._merge(search_module, assets + [_hit("file", "f0")], sort)
        assert [i for i in merged if i.startswith("a")] == ["a2", "a1", "a0"]

    def test_a_list_value_sorts_by_its_smallest_ascending_and_largest_descending(self, search_module):
        ascending = [_hit("asset", "a1", list_tags=["z", "a"]), _hit("asset", "a2", list_tags=["m"])]
        merged = self._merge(search_module, ascending + [_hit("file", "f0", list_tags=["q"])],
                             [{"list_tags.keyword": {"order": "asc"}}])
        assert [i for i in merged if i.startswith("a")] == ["a1", "a2"]

        descending = [_hit("asset", "a1", list_tags=["a", "z"]), _hit("asset", "a2", list_tags=["m"])]
        merged = self._merge(search_module, descending + [_hit("file", "f0", list_tags=["q"])],
                             [{"list_tags.keyword": {"order": "desc"}}])
        assert [i for i in merged if i.startswith("a")] == ["a1", "a2"]
