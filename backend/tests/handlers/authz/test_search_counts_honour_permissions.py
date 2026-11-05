# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search totals and aggregation buckets count only records the caller may read.

Each test drives a search endpoint handler end to end -- the real query builders, permission filters,
policy generation, Casbin evaluation and response processor -- against an in-memory OpenSearch that
evaluates the queries the builders emit (``opensearch_dsl_oracle.FakeSearchManager``). Only the role,
constraint and asset-table reads are stubbed.
"""

import copy
import json
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager
from backend.tests.handlers.authz.search_authz_harness import (
    ALLOW_ALL, CLAIMS, ROLE, constraint, crit, load_search_module, policies,
)

DB = "cnt-db-a"


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_counts")


def _asset(n, asset_type, tags, name=None, archived=False, database_id=DB):
    return {"_id": f"a{n}", "str_rectype": "asset", "str_databaseid": database_id, "str_assetid": f"a{n}",
            "str_assetname": name or f"asset-{n}", "str_assettype": asset_type, "list_tags": tags,
            "bool_archived": archived}


def _file(n, asset, archived=False):
    return {"_id": f"f{n}", "str_rectype": "file", "str_databaseid": asset["str_databaseid"],
            "str_assetid": asset["str_assetid"], "str_key": f"file-{n}.glb", "str_fileext": "glb",
            "str_assetname": asset["str_assetname"], "list_tags": asset["list_tags"], "bool_archived": archived}


def _row(asset):
    return {"databaseId": asset["str_databaseid"] + ("#deleted" if asset["bool_archived"] else ""),
            "assetId": asset["str_assetid"], "assetName": asset["str_assetname"],
            "assetType": asset["str_assettype"], "tags": asset["list_tags"]}


def _install_rows(module, monkeypatch, assets):
    rows = {(r["databaseId"], r["assetId"]): r for r in (_row(a) for a in assets)}
    monkeypatch.setattr(module, "batch_get_items", lambda resource, table, keys, **kw: (
        [copy.deepcopy(rows[(k["databaseId"], k["assetId"])]) for k in keys
         if (k["databaseId"], k["assetId"]) in rows], []))
    table = MagicMock()
    table.get_item.side_effect = lambda Key: (
        {"Item": copy.deepcopy(rows[(Key["databaseId"], Key["assetId"])])}
        if (Key["databaseId"], Key["assetId"]) in rows else {})
    monkeypatch.setattr(module, "asset_storage_table", table)


def _manager(module, monkeypatch):
    manager = module.DatabaseAccessManager()
    monkeypatch.setattr(manager, "get_accessible_databases",
                        lambda claims_and_roles, show_deleted=False, **kw: [DB])
    return manager


def _search(module, monkeypatch, fake, body, simple=False):
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


def _bucket_keys(result, aggregation):
    return {b["key"]: b["doc_count"] for b in result["aggregations"].get(aggregation, {}).get("buckets", [])}


# Ten assets in one database: four of type typeX tagged t1, six of type typeY tagged secret.
TEN = [_asset(n, "typeX", ["t1"]) for n in range(4)] + [_asset(n, "typeY", ["secret"]) for n in range(4, 10)]
TYPE_X_ONLY = [constraint("type-x", [crit("databaseId", "equals", DB), crit("assetType", "equals", "typeX")])]
ASSET_BROWSE = {"aggregations": True, "entityTypes": ["asset"], "from": 0, "size": 10}


@pytest.mark.unit
class TestConstrainedTotalsAndFacets:
    def test_a_type_constraint_counts_only_readable_assets(self, search_module, monkeypatch):
        """A role limited to typeX sees 4 of 10: the total, the facet buckets and the bucket keys."""
        _install_rows(search_module, monkeypatch, TEN)
        fake = FakeSearchManager(search_module, TEN, [])
        with policies(TYPE_X_ONLY, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert sorted(h["_id"] for h in result["hits"]["hits"]) == ["a0", "a1", "a2", "a3"]
        assert result["hits"]["total"] == {"value": 4, "relation": "eq"}
        assert result["aggregationTotal"] == 4
        assert _bucket_keys(result, "str_assettype") == {"typeX": 4}
        assert _bucket_keys(result, "list_tags") == {"t1": 4}
        assert _bucket_keys(result, "str_databaseid") == {DB: 4}

    def test_a_tag_deny_removes_the_tag_from_the_facets(self, search_module, monkeypatch):
        _install_rows(search_module, monkeypatch, TEN)
        fake = FakeSearchManager(search_module, TEN, [])
        constraints = [ALLOW_ALL, constraint("no-secret", [crit("tags", "is_one_of", "secret")], effect="deny")]
        with policies(constraints, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert result["hits"]["total"] == {"value": 4, "relation": "eq"}
        assert "secret" not in _bucket_keys(result, "list_tags")
        assert "typeY" not in _bucket_keys(result, "str_assettype")

    def test_the_total_is_not_capped_at_the_fetch_window(self, search_module, monkeypatch):
        """Page 1 of 10 fetches 20 documents; the caller can read 300 and the total says so."""
        assets = [_asset(n, "typeX", ["t1"]) for n in range(300)] + [_asset(n, "typeY", []) for n in range(300, 350)]
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(TYPE_X_ONLY, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert len(result["hits"]["hits"]) == 10
        assert result["hits"]["total"] == {"value": 300, "relation": "eq"}
        assert result["aggregationTotal"] == 300

    def test_the_admin_role_query_is_unchanged(self, search_module, monkeypatch):
        """An unrestricted role adds no clause to the query and keeps every aggregation."""
        _install_rows(search_module, monkeypatch, TEN)
        fake = FakeSearchManager(search_module, TEN, [])
        with policies(shipped_constraints.seeded_constraints("admin"), ("admin",)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        asset_query, _ = fake.queries[0]
        assert asset_query["query"]["bool"]["filter"] == [{"terms": {"str_databaseid.keyword": [DB]}}]
        assert set(asset_query["aggs"]) == {"str_assettype", "str_databaseid", "list_tags"}
        assert result["hits"]["total"] == {"value": 10, "relation": "eq"}
        assert _bucket_keys(result, "str_assettype") == {"typeX": 4, "typeY": 6}

    def test_archived_records_follow_the_both_forms_rule(self, search_module, monkeypatch):
        """database-user allows only `databaseId equals <db>`, which refuses the archived form."""
        assets = [_asset(n, "typeX", []) for n in range(3)] + [_asset(n, "typeX", [], archived=True)
                                                               for n in range(3, 5)]
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        constraints = shipped_constraints.template_constraints("database-user.json", "tpl",
                                                                {"DATABASE_ID": DB, "TAG_VALUE": "x"})
        with policies(constraints, ("tpl",)):
            result = _search(search_module, monkeypatch, fake, dict(ASSET_BROWSE, includeArchived=True))

        assert sorted(h["_id"] for h in result["hits"]["hits"]) == ["a0", "a1", "a2"]
        assert result["hits"]["total"] == {"value": 3, "relation": "eq"}
        assert _bucket_keys(result, "str_assettype") == {"typeX": 3}


@pytest.mark.unit
class TestDottedValues:
    """Asset types are stored as extensions ('.obj'); in a rule value '.' is any character but a line
    break, as Casbin reads it, and the filter says exactly that."""

    def _assets(self):
        return ([_asset(n, ".obj", ["t1"]) for n in range(14)]
                + [_asset(n, "xobj", ["t2"]) for n in range(14, 16)]
                + [_asset(n, ".stl", ["t3"]) for n in range(16, 30)])

    def test_a_type_rule_on_an_extension_counts_exactly(self, search_module, monkeypatch):
        assets = self._assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        constraints = [constraint("obj", [crit("databaseId", "equals", DB), crit("assetType", "equals", ".obj")])]
        with policies(constraints, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert len(result["hits"]["hits"]) == 10
        assert result["hits"]["total"] == {"value": 16, "relation": "eq"}
        assert _bucket_keys(result, "str_assettype") == {".obj": 14, "xobj": 2}
        assert result["aggregationTotal"] == 16

    def test_an_escaped_dot_is_a_literal(self, search_module, monkeypatch):
        assets = self._assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        constraints = [constraint("obj", [crit("assetType", "equals", "\\.obj")])]
        with policies(constraints, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert result["hits"]["total"] == {"value": 14, "relation": "eq"}
        assert _bucket_keys(result, "str_assettype") == {".obj": 14}


@pytest.mark.unit
class TestRulesTheIndexCannotDecide:
    # typeX translates; the name pattern does not, so the filter is a range
    MIXED = [constraint("type-x", [crit("assetType", "equals", "typeX")]),
             constraint("name", [crit("assetName", "contains", "proj[A-Z]a")])]

    def _assets(self):
        return ([_asset(n, "typeX", ["t1"]) for n in range(30)]
                + [_asset(n, "typeY", ["t2"], name="projXa") for n in range(30, 40)]
                + [_asset(n, "typeY", ["secret"], name="other") for n in range(40, 70)])

    def test_a_truncated_answer_reports_a_lower_bound(self, search_module, monkeypatch):
        assets = self._assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(self.MIXED, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert result["hits"]["total"]["relation"] == "gte"
        assert 30 <= result["hits"]["total"]["value"] <= 40
        # buckets count only sure reads: the typeX assets
        assert _bucket_keys(result, "str_assettype") == {"typeX": 30}
        assert "secret" not in _bucket_keys(result, "list_tags")
        assert "t2" not in _bucket_keys(result, "list_tags")
        assert "vams_authz_readable_count_asset" not in result["aggregations"]

    def test_a_complete_answer_is_exact_and_lists_every_readable_record(self, search_module, monkeypatch):
        assets = self._assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(self.MIXED, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, dict(ASSET_BROWSE, size=100))

        returned = {h["_id"] for h in result["hits"]["hits"]}
        assert returned == {f"a{n}" for n in range(40)}
        assert result["hits"]["total"] == {"value": 40, "relation": "eq"}

    def test_simple_search_reports_a_lower_bound_too(self, search_module, monkeypatch):
        assets = self._assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(self.MIXED, (ROLE,)):
            result = _search(search_module, monkeypatch, fake,
                             {"entityTypes": ["asset"], "from": 0, "size": 5}, simple=True)

        assert result["hits"]["total"]["relation"] == "gte"
        assert 30 <= result["hits"]["total"]["value"] <= 40
        assert result["aggregations"] == {}


@pytest.mark.unit
class TestFacetsUnderAnInexactFilter:
    """A rule the index cannot decide leaves the lower bound empty; the facets are then the buckets
    of the hits that passed, never more than the caller may read."""

    UNDECIDABLE = [constraint("name", [crit("databaseId", "equals", DB), crit("assetName", "contains", "proj[A-Z]a")])]

    def _assets(self):
        return ([_asset(n, ".obj", ["t1"], name="projXa") for n in range(14)]
                + [_asset(n, ".stl", ["secret"], name="other") for n in range(14, 40)])

    def test_a_complete_answer_gives_exact_facets(self, search_module, monkeypatch):
        assets = self._assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(self.UNDECIDABLE, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, dict(ASSET_BROWSE, size=100))

        assert result["hits"]["total"] == {"value": 14, "relation": "eq"}
        assert _bucket_keys(result, "str_assettype") == {".obj": 14}
        assert _bucket_keys(result, "list_tags") == {"t1": 14}
        assert _bucket_keys(result, "str_databaseid") == {DB: 14}
        assert result["aggregationTotal"] == 14

    def test_a_truncated_answer_counts_only_hits_that_passed(self, search_module, monkeypatch):
        assets = self._assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(self.UNDECIDABLE, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, dict(ASSET_BROWSE, size=5))

        # the window holds 10 candidates, all readable
        assert result["hits"]["total"] == {"value": 10, "relation": "gte"}
        assert _bucket_keys(result, "str_assettype") == {".obj": 10}
        assert "secret" not in _bucket_keys(result, "list_tags")

    def test_lower_bound_buckets_are_kept_when_larger(self, search_module, monkeypatch):
        """typeX translates and the name rule does not: 30 typeX assets are sure reads even though
        the window holds 20 of them."""
        assets = ([_asset(n, "typeX", ["t1"]) for n in range(30)]
                  + [_asset(n, "typeY", ["t2"], name="projXa") for n in range(30, 40)])
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(TestRulesTheIndexCannotDecide.MIXED, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert _bucket_keys(result, "str_assettype") == {"typeX": 30}

    def test_the_aggregated_fields_match_the_query_builder(self, search_module):
        builder = search_module.DualIndexQueryBuilder.__new__(search_module.DualIndexQueryBuilder)
        for index_type, fields in search_module.AGGREGATED_FIELDS.items():
            aggregations = builder._build_aggregations(index_type)
            assert tuple(aggregations) == fields
            for aggregation in aggregations.values():
                (terms,) = aggregation["aggs"].values()
                assert terms["terms"]["size"] == search_module.AGGREGATION_BUCKET_LIMIT


@pytest.mark.unit
class TestShippedDatabaseReadonlyOnFiles:
    """The shipped database-readonly template (`databaseId equals <db>`) on a file search: files of an
    archived asset are refused, and the file index cannot tell which parents are archived."""

    def _data(self):
        live = [_asset(n, ".glb", ["t1"]) for n in range(12)]
        archived = [_asset(n, ".glb", ["t1"], archived=True) for n in range(12, 16)]
        other_db = [_asset(n, ".glb", ["t1"], database_id="cnt-db-b") for n in range(16, 18)]
        files = [_file(n, a) for n, a in enumerate(live + archived + other_db)]
        # the files of archived assets were flipped to archived by their delete markers
        for f in files[12:16]:
            f["bool_archived"] = True
        return live + archived + other_db, files

    def _run(self, search_module, monkeypatch, size, include_archived):
        assets, files = self._data()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, files)
        constraints = shipped_constraints.template_constraints("database-readonly.json", "tpl",
                                                                {"DATABASE_ID": DB, "TAG_VALUE": "x"})
        body = {"aggregations": True, "entityTypes": ["file"], "from": 0, "size": size,
                "includeArchived": include_archived}
        with policies(constraints, ("tpl",)):
            return _search(search_module, monkeypatch, fake, body)

    @pytest.mark.parametrize("include_archived", [False, True])
    def test_a_complete_answer_is_exact(self, search_module, monkeypatch, include_archived):
        result = self._run(search_module, monkeypatch, 100, include_archived)

        assert sorted(h["_id"] for h in result["hits"]["hits"]) == sorted(f"f{n}" for n in range(12))
        assert result["hits"]["total"] == {"value": 12, "relation": "eq"}
        assert _bucket_keys(result, "str_fileext") == {"glb": 12}
        assert _bucket_keys(result, "str_databaseid") == {DB: 12}

    @pytest.mark.parametrize("include_archived", [False, True])
    def test_a_truncated_answer_is_a_minimum_with_facets(self, search_module, monkeypatch, include_archived):
        result = self._run(search_module, monkeypatch, 3, include_archived)

        assert result["hits"]["total"]["relation"] == "gte"
        assert 3 <= result["hits"]["total"]["value"] <= 12
        buckets = _bucket_keys(result, "str_fileext")
        assert 0 < buckets["glb"] <= 12
        assert set(_bucket_keys(result, "str_assetid")) <= {f"a{n}" for n in range(12)}


def _counts_from_hits(hits, field):
    counts = {}
    for hit in hits:
        raw = hit["_source"].get(field)
        for value in (raw if isinstance(raw, list) else [] if raw is None else [raw]):
            counts[value] = counts.get(value, 0) + 1
    return counts


# Role shapes from the live smoke of this change, over data shaped like its test database
SPEC_ROLES = {
    "database equals": [constraint("r", [crit("databaseId", "equals", DB)])],
    "database and extension type": [constraint("r", [crit("databaseId", "equals", DB),
                                                     crit("assetType", "equals", ".obj")])],
    "name prefix": [constraint("r", [crit("assetName", "starts_with", "proj-")])],
    "database with a tag deny": [constraint("r", [crit("databaseId", "equals", DB)]),
                                 constraint("d", [crit("tags", "is_one_of", "secret")], effect="deny")],
    "name with a dot": [constraint("r", [crit("assetName", "contains", "proj.a")])],
    "combined with a class": [constraint("r", [], [crit("databaseId", "equals", DB),
                                                   crit("assetName", "starts_with", "proj-b-0"),
                                                   crit("assetName", "contains", "misc-b-1[0-4]")]),
                              constraint("d", [crit("tags", "is_one_of", "secret")], effect="deny")],
    "untranslatable name class": [constraint("r", [crit("assetName", "contains", "[pP]roj-a")])],
}


def _spec_assets():
    assets = []
    for n in range(30):
        name = ("proj-a-%d" if n < 10 else "proj-b-%d" if n < 20 else "misc-b-%d") % n
        assets.append(_asset(n, ".obj" if n % 3 else ".stl", [["t1"], ["t2"], ["secret"]][n % 3], name=name,
                             archived=n in (5, 15, 25)))
    return assets


@pytest.mark.unit
class TestCountsSpecInvariants:
    """The assertions of web/e2e/search.counts.spec.ts "asset totals and facets count only listed
    records", for each role shape the live smoke ran."""

    @pytest.mark.parametrize("role", sorted(SPEC_ROLES))
    def test_one_exact_page_lists_what_the_facets_count(self, search_module, monkeypatch, role):
        assets = _spec_assets()
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(SPEC_ROLES[role], (ROLE,)):
            answer = _search(search_module, monkeypatch, fake, dict(ASSET_BROWSE, size=100))

        total = answer["hits"]["total"]
        assert total["relation"] == "eq"
        hits = answer["hits"]["hits"]
        assert len(hits) == total["value"] > 0
        assert _bucket_keys(answer, "str_assettype") == _counts_from_hits(hits, "str_assettype")
        assert _bucket_keys(answer, "list_tags") == _counts_from_hits(hits, "list_tags")
        if answer["aggregationTotal"] is not None:
            assert answer["aggregationTotal"] <= total["value"]

    @pytest.mark.parametrize("role", sorted(SPEC_ROLES))
    def test_a_small_page_never_counts_more_than_the_readable_set(self, search_module, monkeypatch, role):
        assets = _spec_assets()
        _install_rows(search_module, monkeypatch, assets)
        with policies(SPEC_ROLES[role], (ROLE,)):
            full = _search(search_module, monkeypatch, FakeSearchManager(search_module, assets, []),
                           dict(ASSET_BROWSE, size=100))
            small = _search(search_module, monkeypatch, FakeSearchManager(search_module, assets, []),
                            dict(ASSET_BROWSE, size=2))

        readable = full["hits"]["total"]["value"]
        assert small["hits"]["total"]["value"] <= readable
        for name in ("str_assettype", "list_tags", "str_databaseid"):
            truth = _bucket_keys(full, name)
            for key, count in _bucket_keys(small, name).items():
                assert count <= truth.get(key, 0), (name, key)
        if small["aggregationTotal"] is not None:
            assert small["aggregationTotal"] <= readable


@pytest.mark.unit
class TestSimpleSearch:
    def test_the_total_is_the_callers_and_uncapped(self, search_module, monkeypatch):
        assets = [_asset(n, "typeX", []) for n in range(50)] + [_asset(n, "typeY", []) for n in range(50, 80)]
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, [])
        with policies(TYPE_X_ONLY, (ROLE,)):
            result = _search(search_module, monkeypatch, fake,
                             {"entityTypes": ["asset"], "from": 0, "size": 5}, simple=True)

        assert result["hits"]["total"] == {"value": 50, "relation": "eq"}
        assert len(result["hits"]["hits"]) == 5


@pytest.mark.unit
class TestFilesAndAssets:
    def test_file_totals_follow_the_parent_asset(self, search_module, monkeypatch):
        """A database-scoped role: files are exact only when no parent is archived, so the file index
        reports a lower bound, never counting a file of an archived (refused) asset."""
        live = [_asset(n, "typeX", []) for n in range(3)]
        archived = [_asset(n, "typeX", [], archived=True) for n in range(3, 5)]
        files = [_file(n, a) for n, a in enumerate(live + archived)]
        _install_rows(search_module, monkeypatch, live + archived)
        fake = FakeSearchManager(search_module, live + archived, files)
        constraints = [constraint("db", [crit("databaseId", "equals", DB)])]
        with policies(constraints, (ROLE,)):
            result = _search(search_module, monkeypatch, fake,
                             {"aggregations": True, "entityTypes": ["file"], "from": 0, "size": 100})

        assert sorted(h["_id"] for h in result["hits"]["hits"]) == ["f0", "f1", "f2"]
        assert result["hits"]["total"] == {"value": 3, "relation": "eq"}
        assert sum(_bucket_keys(result, "str_fileext").values()) <= 3

    def test_a_file_without_a_parent_row_does_not_suppress_counts(self, search_module, monkeypatch):
        assets = [_asset(n, "typeX", []) for n in range(3)]
        orphan = _file(9, _asset(99, "typeX", []))
        files = [_file(n, a) for n, a in enumerate(assets)] + [orphan]
        _install_rows(search_module, monkeypatch, assets)
        fake = FakeSearchManager(search_module, assets, files)
        with policies([ALLOW_ALL], (ROLE,)):
            result = _search(search_module, monkeypatch, fake,
                             {"aggregations": True, "entityTypes": ["file"], "from": 0, "size": 100})

        assert sorted(h["_id"] for h in result["hits"]["hits"]) == ["f0", "f1", "f2"]
        assert result["aggregations"] != {}


@pytest.mark.unit
class TestWithoutUsableFilters:
    def test_a_refused_filtered_query_falls_back_without_counts(self, search_module, monkeypatch):
        _install_rows(search_module, monkeypatch, TEN)
        fake = FakeSearchManager(search_module, TEN, [], refuse_first=True)
        with policies(TYPE_X_ONLY, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert len(fake.queries) == 2
        fallback_asset, _ = fake.queries[1]
        assert "aggs" not in fallback_asset
        assert sorted(h["_id"] for h in result["hits"]["hits"]) == ["a0", "a1", "a2", "a3"]
        assert result["hits"]["total"] == {"value": 4, "relation": "eq"}
        assert result["aggregations"] == {}
        assert result["aggregationTotal"] is None

    def test_an_unrestricted_caller_is_not_retried(self, search_module, monkeypatch):
        """With nothing added to the query there is no different query to fall back to."""
        _install_rows(search_module, monkeypatch, TEN)
        fake = FakeSearchManager(search_module, TEN, [], refuse_first=True)
        manager = _manager(search_module, monkeypatch)
        event = {"body": json.dumps(ASSET_BROWSE)}
        with policies(shipped_constraints.seeded_constraints("admin"), ("admin",)):
            response = search_module.handle_post_request(
                event, fake, search_module.DualIndexQueryBuilder(manager),
                search_module.DualIndexResponseProcessor(manager), CLAIMS)
        assert response["statusCode"] != 200
        assert len(fake.queries) == 1

    def test_no_policy_lines_means_no_aggregations(self, search_module, monkeypatch):
        _install_rows(search_module, monkeypatch, TEN)
        fake = FakeSearchManager(search_module, TEN, [])
        with policies(TYPE_X_ONLY, (ROLE,)), patch.object(
            authz_module.CasbinEnforcerService, "effective_object_policies", return_value=None
        ):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        asset_query, _ = fake.queries[0]
        assert "aggs" not in asset_query
        assert result["hits"]["total"] == {"value": 4, "relation": "eq"}
        assert result["aggregations"] == {}
        assert result["aggregationTotal"] is None

    def test_an_exact_filter_the_hit_check_contradicts_withholds_counts(self, search_module, monkeypatch):
        """Should the per-hit check refuse a hit an exact filter admitted, the response falls back to
        the hits that passed and reports no aggregations."""
        _install_rows(search_module, monkeypatch, TEN)
        fake = FakeSearchManager(search_module, TEN, [])
        original = search_module.DualIndexResponseProcessor._hit_decision

        def refuse_a0(self, hit, claims):
            if hit["_id"] == "a0":
                return False, True
            return original(self, hit, claims)

        monkeypatch.setattr(search_module.DualIndexResponseProcessor, "_hit_decision", refuse_a0)
        with policies(TYPE_X_ONLY, (ROLE,)):
            result = _search(search_module, monkeypatch, fake, ASSET_BROWSE)

        assert result["hits"]["total"] == {"value": 3, "relation": "eq"}
        assert result["aggregations"] == {}
        assert result["aggregationTotal"] is None


@pytest.mark.unit
class TestQueryShape:
    def test_the_filter_joins_the_existing_bool_filter(self, search_module, monkeypatch):
        fake = FakeSearchManager(search_module, [], [])
        with policies(TYPE_X_ONLY, (ROLE,)):
            _search(search_module, monkeypatch, fake, dict(ASSET_BROWSE, query="pump"))
        asset_query, _ = fake.queries[0]
        bool_query = asset_query["query"]["bool"]
        assert bool_query["filter"][0] == {"terms": {"str_databaseid.keyword": [DB]}}
        assert len(bool_query["filter"]) == 2
        assert "must" in bool_query and "must_not" in bool_query

    def test_an_inexact_filter_narrows_every_aggregation(self, search_module, monkeypatch):
        fake = FakeSearchManager(search_module, [], [])
        with policies(TestRulesTheIndexCannotDecide.MIXED, (ROLE,)):
            _search(search_module, monkeypatch, fake, ASSET_BROWSE)
        asset_query, _ = fake.queries[0]
        assert "vams_authz_readable_count_asset" in asset_query["aggs"]
        for name in ("str_assettype", "str_databaseid", "list_tags"):
            assert len(asset_query["aggs"][name]["filter"]["bool"]["filter"]) == 2
