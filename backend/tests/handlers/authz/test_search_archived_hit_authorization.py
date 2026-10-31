# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search hits are authorized on the record each hit stands for, as that record is stored.

Archiving an asset rewrites its row under ``<databaseId>#deleted``. The asset listings and the
single-asset GET with ``showArchived`` (``assetService.get_assets``, ``assetService.get_all_assets``,
``assetService.get_asset_details`` + ``authorize_single_asset``) hand that stored row to
``CasbinEnforcer.enforce``, and ``enforce`` decides a ``#deleted`` id on both the stored id and the
database id. The asset's file listing with ``includeArchived`` (``assetFiles.list_asset_files`` ->
``get_asset_with_permissions``) decides every file, archived or not, on the asset row it reads by the
live key.

Search documents carry the database id without the suffix, so the search response processor builds
the id it checks from what the hit stands for:

* an asset hit with ``bool_archived`` set is checked as ``<str_databaseid>#deleted``;
* a file hit is checked as its parent asset row, with that row's ``databaseId`` as stored. A live
  parent gives a live decision even when the file itself is archived; a parent read from the archived
  partition gives the stored form.

The matrix runs every shipped permission template, and the two roles a deployment seeds, over five
kinds of hit inside and outside the template's database. It asserts that search returns exactly the
hits whose stored record the same enforcer allows, the decision the listing and GET routes make.

This runs the real policy generation and Casbin evaluation; only the role and constraint reads and the
asset-table reads are stubbed. search.py is loaded by path, with ``handlers.authz`` pointing at the
real authz module that this directory's conftest makes importable.
"""

import contextlib
import copy
import importlib.util
import json
import os
import sys
import types
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

USER_ID = "search-user"
SCOPED_DB = "tpl-db"
OTHER_DB = "other-db"
ARCHIVED = "#deleted"
TEMPLATE_ROLE = "tpl"
TEMPLATE_VARIABLES = {"DATABASE_ID": SCOPED_DB, "TAG_VALUE": "locked"}
CLAIMS = {"tokens": [USER_ID], "roles": [], "mfaEnabled": True}

# The five kinds of hit, in the order they are returned.
LIVE_ASSET = "live asset"
ARCHIVED_ASSET = "archived asset"
LIVE_FILE = "live file"
FILE_OF_ARCHIVED_ASSET = "file of an archived asset"
ARCHIVED_FILE_OF_LIVE_ASSET = "archived file of a live asset"
KINDS = (LIVE_ASSET, ARCHIVED_ASSET, LIVE_FILE, FILE_OF_ARCHIVED_ASSET, ARCHIVED_FILE_OF_LIVE_ASSET)
ARCHIVED_RECORD_KINDS = (ARCHIVED_ASSET, FILE_OF_ARCHIVED_ASSET)


def _boto_client(name, *args, **kwargs):
    client = MagicMock()
    if name == "ssm":
        client.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
    return client


@pytest.fixture
def search_module():
    names = ("handlers.auth", "handlers.authz", "common.dynamodb")
    saved = {name: sys.modules.get(name) for name in names}

    sys.modules["handlers.authz"] = authz_module
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": [USER_ID]})
    sys.modules["handlers.auth"] = auth_stub
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "search_under_test_archived_hits", os.path.abspath(_SEARCH_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    return module


# ---------------------------------------------------------------------------------------------
# Records and hits
# ---------------------------------------------------------------------------------------------
# One search hit and the stored asset row the listing or GET route decides it on.
Cell = namedtuple("Cell", "kind database_id hit record")


def _asset_row(database_id, asset_id, archived):
    row = {
        "databaseId": database_id + ARCHIVED if archived else database_id,
        "assetId": asset_id,
        "assetName": f"name-{asset_id}",
        "assetType": ".glb",
        "description": "part",
        "tags": [],
    }
    if archived:
        # assetService.get_assets adds the status to a row read from the archived partition
        row["status"] = "archived"
    return row


def _asset_hit(row):
    """An asset document as assetIndexer writes it: the live database id, the archive flag, and the
    suffix only in the document id."""
    database_id = row["databaseId"].replace(ARCHIVED, "")
    archived = row["databaseId"].endswith(ARCHIVED)
    return {
        "_index": "vams-asset",
        "_id": f"{row['databaseId']}#{row['assetId']}",
        "_score": 1.0,
        "_index_type": "asset",
        "_source": {
            "str_rectype": "asset",
            "str_databaseid": database_id,
            "str_assetid": row["assetId"],
            "str_assetname": row["assetName"],
            "str_assettype": row["assetType"],
            "list_tags": row["tags"],
            "bool_archived": archived,
        },
    }


def _file_hit(database_id, asset_id, key, archived):
    """A file document as fileIndexer writes it: always the live database id."""
    return {
        "_index": "vams-file",
        "_id": f"{database_id}/{asset_id}/{key}",
        "_score": 1.0,
        "_index_type": "file",
        "_source": {
            "str_rectype": "file",
            "str_databaseid": database_id,
            "str_assetid": asset_id,
            "str_assetname": f"name-{asset_id}",
            "str_key": key,
            "bool_archived": archived,
            "list_tags": [],
        },
    }


def _cells(database_id):
    live = _asset_row(database_id, "live-asset", archived=False)
    archived = _asset_row(database_id, "archived-asset", archived=True)
    return [
        Cell(LIVE_ASSET, database_id, _asset_hit(live), live),
        Cell(ARCHIVED_ASSET, database_id, _asset_hit(archived), archived),
        Cell(LIVE_FILE, database_id, _file_hit(database_id, "live-asset", "live.bin", False), live),
        Cell(FILE_OF_ARCHIVED_ASSET, database_id,
             _file_hit(database_id, "archived-asset", "model.bin", True), archived),
        Cell(ARCHIVED_FILE_OF_LIVE_ASSET, database_id,
             _file_hit(database_id, "live-asset", "old.bin", True), live),
    ]


CELLS = _cells(SCOPED_DB) + _cells(OTHER_DB)


def _ids(cells):
    return [cell.hit["_id"] for cell in cells]


def _select(*kinds, database_ids=(SCOPED_DB, OTHER_DB)):
    return [cell for cell in CELLS if cell.kind in kinds and cell.database_id in database_ids]


class _AssetTable:
    """Asset rows by (databaseId, assetId), served through batch_get_items and get_item."""

    def __init__(self, rows):
        self.rows = {(row["databaseId"], row["assetId"]): row for row in rows}

    def batch_get_items(self, dynamodb_resource, table_name, keys, **kwargs):
        found = [self.rows[(key["databaseId"], key["assetId"])] for key in keys
                 if (key["databaseId"], key["assetId"]) in self.rows]
        return copy.deepcopy(found), []

    def get_item(self, Key):
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": copy.deepcopy(row)} if row else {}


def _install_table(module, monkeypatch, rows):
    table = _AssetTable(rows)
    monkeypatch.setattr(module, "batch_get_items", table.batch_get_items)
    asset_table = MagicMock()
    asset_table.get_item.side_effect = table.get_item
    monkeypatch.setattr(module, "asset_storage_table", asset_table)
    return table


# ---------------------------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------------------------
def _template_set(name):
    return shipped_constraints.template_constraints(name, TEMPLATE_ROLE, TEMPLATE_VARIABLES), (TEMPLATE_ROLE,)


SHIPPED_SETS = {f"template {name}": (lambda name=name: _template_set(name))
                for name in shipped_constraints.template_names()}
SHIPPED_SETS.update({
    "seeded admin": lambda: (shipped_constraints.seeded_constraints("admin"), ("admin",)),
    "seeded basicReadOnly": lambda: (shipped_constraints.seeded_constraints("basicReadOnly"),
                                     ("basicReadOnly",)),
})

SYNTHETIC_ROLE = "searcher"


def _constraint(constraint_id, criteria_and=(), effect="allow", criteria_or=(), object_type="asset"):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": list(criteria_and),
        "criteriaOr": list(criteria_or),
        "groupPermissions": [{"groupId": SYNTHETIC_ROLE, "permission": "GET", "permissionType": effect}],
        "userPermissions": [],
    }


def _crit(operator, value, field="databaseId"):
    return {"field": field, "operator": operator, "value": value}


ALLOW_ALL_ASSETS = _constraint("allow-all", [_crit("contains", ".*")])


@contextlib.contextmanager
def _policies(constraints, roles):
    """Real policy text and enforcer for USER_ID holding `roles`, built from `constraints`."""
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    try:
        with patch.object(
            authz_module.CasbinEnforcerService,
            "_read_current_user_roles_from_table",
            return_value=[{"userId": USER_ID, "roleName": role} for role in roles],
        ), patch.object(
            authz_module.CasbinEnforcerService,
            "_read_policies_batch_optimized",
            return_value=copy.deepcopy(list(constraints)),
        ):
            yield
    finally:
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}


def _listing_decision(cell):
    """The decision the listing / GET routes make: the stored row, typed as an asset, checked for GET
    (assetService.get_assets, assetFiles.get_asset_with_permissions)."""
    return authz_module.CasbinEnforcer(CLAIMS).enforce({**copy.deepcopy(cell.record), "object__type": "asset"},
                                                       "GET")


def _opensearch_response(hits):
    return {
        "took": 1,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {"hits": copy.deepcopy(hits), "total": {"value": len(hits), "relation": "eq"}},
    }


def _search(module, cells):
    """Ids of the hits the response processor returns for an includeArchived search over `cells`."""
    request = module.SearchRequestModel(**{"from": 0, "size": 100, "aggregations": False,
                                           "includeArchived": True})
    processor = module.DualIndexResponseProcessor(module.DatabaseAccessManager())
    result = processor.process_dual_search_response(
        _opensearch_response([cell.hit for cell in cells]), request, CLAIMS
    )
    return [hit["_id"] for hit in result.dict(by_alias=True)["hits"]["hits"]]


def _rows(cells):
    return [cell.record for cell in cells]


# ---------------------------------------------------------------------------------------------
# Search returns what the listing decision allows
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestSearchMatchesTheListingDecisionForShippedRoles:
    @pytest.mark.parametrize("set_name", sorted(SHIPPED_SETS))
    def test_search_returns_exactly_the_hits_whose_stored_record_is_allowed(
        self, search_module, monkeypatch, set_name
    ):
        constraints, roles = SHIPPED_SETS[set_name]()
        _install_table(search_module, monkeypatch, _rows(CELLS))

        with _policies(constraints, roles):
            expected = _ids([cell for cell in CELLS if _listing_decision(cell)])
            returned = _search(search_module, CELLS)

        assert returned == expected, set_name

    def test_the_matrix_covers_a_role_that_tells_archived_records_apart(self):
        """At least one shipped role allows a live record and refuses its archived one, so the matrix
        compares search with the listing on a cell where they can differ."""
        separating = []
        for set_name, build in SHIPPED_SETS.items():
            constraints, roles = build()
            with _policies(constraints, roles):
                decisions = {(cell.kind, cell.database_id): _listing_decision(cell) for cell in CELLS}
            if decisions[(LIVE_ASSET, SCOPED_DB)] and not decisions[(ARCHIVED_ASSET, SCOPED_DB)]:
                separating.append(set_name)

        assert "template database-user.json" in separating
        assert "template database-readonly.json" in separating


@pytest.mark.unit
class TestDatabaseScopedTemplatesInSearch:
    """The database-user and database-readonly templates allow only `databaseId equals <db>`."""

    @pytest.mark.parametrize("template_name", ["database-user.json", "database-readonly.json"])
    def test_archived_records_are_withheld_and_live_ones_returned(self, search_module, monkeypatch,
                                                                 template_name):
        _install_table(search_module, monkeypatch, _rows(CELLS))

        with _policies(*_template_set(template_name)):
            returned = _search(search_module, CELLS)

        assert returned == _ids(_select(LIVE_ASSET, LIVE_FILE, ARCHIVED_FILE_OF_LIVE_ASSET,
                                        database_ids=(SCOPED_DB,)))


# ---------------------------------------------------------------------------------------------
# Synthetic rules on the database id
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestRulesOnTheDatabaseIdInSearch:
    def test_an_allow_covering_both_forms_returns_archived_hits(self, search_module, monkeypatch):
        _install_table(search_module, monkeypatch, _rows(CELLS))

        with _policies([_constraint("allow-db", [_crit("contains", SCOPED_DB)])], (SYNTHETIC_ROLE,)):
            returned = _search(search_module, CELLS)

        assert returned == _ids(_select(*KINDS, database_ids=(SCOPED_DB,)))

    def test_a_deny_on_the_suffix_withholds_every_archived_record(self, search_module, monkeypatch):
        """An archived file of a live asset is decided on the live asset, as the file listing does."""
        _install_table(search_module, monkeypatch, _rows(CELLS))
        constraints = [ALLOW_ALL_ASSETS, _constraint("deny-archived", [_crit("contains", ARCHIVED)],
                                                     effect="deny")]

        with _policies(constraints, (SYNTHETIC_ROLE,)):
            returned = _search(search_module, CELLS)

        assert returned == _ids(_select(LIVE_ASSET, LIVE_FILE, ARCHIVED_FILE_OF_LIVE_ASSET))
        assert not set(returned) & set(_ids(_select(*ARCHIVED_RECORD_KINDS)))

    def test_a_deny_on_the_database_withholds_its_archived_records(self, search_module, monkeypatch):
        _install_table(search_module, monkeypatch, _rows(CELLS))
        constraints = [ALLOW_ALL_ASSETS, _constraint("deny-db", [_crit("equals", SCOPED_DB)], effect="deny")]

        with _policies(constraints, (SYNTHETIC_ROLE,)):
            returned = _search(search_module, CELLS)

        assert returned == _ids(_select(*KINDS, database_ids=(OTHER_DB,)))

    def test_an_allow_naming_only_the_stored_id_returns_nothing(self, search_module, monkeypatch):
        """The database-id form must pass too, so the suffixed value alone grants no hit."""
        _install_table(search_module, monkeypatch, _rows(CELLS))

        with _policies([_constraint("allow-stored", [_crit("equals", SCOPED_DB + ARCHIVED)])],
                       (SYNTHETIC_ROLE,)):
            returned = _search(search_module, CELLS)

        assert returned == []

    def test_a_file_hit_without_a_parent_row_is_withheld(self, search_module, monkeypatch):
        cells = _select(*KINDS, database_ids=(SCOPED_DB,))
        orphans = [
            Cell(LIVE_FILE, SCOPED_DB, _file_hit(SCOPED_DB, "missing-asset", "a.bin", False), None),
            Cell(ARCHIVED_FILE_OF_LIVE_ASSET, SCOPED_DB,
                 _file_hit(SCOPED_DB, "missing-asset", "b.bin", True), None),
        ]
        _install_table(search_module, monkeypatch, _rows(cells))

        with _policies([ALLOW_ALL_ASSETS], (SYNTHETIC_ROLE,)):
            returned = _search(search_module, cells + orphans)

        assert returned == _ids(cells)


# ---------------------------------------------------------------------------------------------
# What reaches the enforcer, and the database pre-filter
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestTheIdEachHitIsDecidedOn:
    def test_each_hit_is_decided_on_its_records_stored_database_id(self, search_module, monkeypatch):
        cells = _select(*KINDS, database_ids=(SCOPED_DB,))
        _install_table(search_module, monkeypatch, _rows(cells))
        seen = []
        original_enforce = authz_module.CasbinEnforcer.enforce

        def recording_enforce(self, obj, act):
            seen.append(obj["databaseId"])
            return original_enforce(self, obj, act)

        monkeypatch.setattr(authz_module.CasbinEnforcer, "enforce", recording_enforce)

        with _policies([ALLOW_ALL_ASSETS], (SYNTHETIC_ROLE,)):
            returned = _search(search_module, cells)

        assert returned == _ids(cells)
        assert seen == [
            SCOPED_DB,             # live asset
            SCOPED_DB + ARCHIVED,  # archived asset
            SCOPED_DB,             # live file
            SCOPED_DB + ARCHIVED,  # file of an archived asset
            SCOPED_DB,             # archived file of a live asset
        ]

    def test_a_hit_from_a_deleted_database_is_still_skipped(self, search_module, monkeypatch):
        hit = _asset_hit(_asset_row(SCOPED_DB, "a1", archived=False))
        hit["_source"]["str_databaseid"] = SCOPED_DB + ARCHIVED
        cell = Cell(LIVE_ASSET, SCOPED_DB, hit, None)
        _install_table(search_module, monkeypatch, [])

        with _policies([ALLOW_ALL_ASSETS], (SYNTHETIC_ROLE,)):
            returned = _search(search_module, [cell])

        assert returned == []


@pytest.mark.unit
class TestDatabasePrefilterIsUnchanged:
    """The query is restricted to the live ids of the databases the caller may GET, whether or not
    archived records are included; archived records are decided per hit."""

    @pytest.mark.parametrize("include_archived", [True, False])
    def test_both_query_builders_filter_on_live_database_ids(self, search_module, monkeypatch,
                                                             include_archived):
        calls = []

        def accessible(claims_and_roles, show_deleted=False, **kwargs):
            calls.append(show_deleted)
            return [SCOPED_DB]

        manager = search_module.DatabaseAccessManager()
        monkeypatch.setattr(manager, "get_accessible_databases", accessible)
        advanced = search_module.DualIndexQueryBuilder(manager).build_dual_index_queries(
            search_module.SearchRequestModel(**{"query": "part", "includeArchived": include_archived}), CLAIMS
        )
        simple = search_module.SimpleSearchQueryBuilder(manager).build_simple_dual_index_queries(
            search_module.SimpleSearchRequestModel(**{"query": "part", "includeArchived": include_archived}),
            CLAIMS,
        )

        prefilter = {"terms": {"str_databaseid.keyword": [SCOPED_DB]}}
        archived_exclusion = {"term": {"bool_archived": True}}
        assert calls == [False, False]
        for query in (*advanced, *simple):
            bool_query = query["query"]["bool"]
            assert prefilter in bool_query["filter"]
            assert (archived_exclusion in bool_query.get("must_not", [])) is (not include_archived)


@pytest.mark.unit
class TestBothSearchEndpoints:
    """The search and simple-search endpoints share the per-hit decision."""

    @pytest.mark.parametrize("endpoint", ["search", "simple"])
    def test_a_database_scoped_role_gets_the_same_hits_from_either_endpoint(
        self, search_module, monkeypatch, endpoint
    ):
        cells = _select(*KINDS, database_ids=(SCOPED_DB,))
        _install_table(search_module, monkeypatch, _rows(cells))
        manager = search_module.DatabaseAccessManager()
        monkeypatch.setattr(manager, "get_accessible_databases",
                            lambda claims_and_roles, show_deleted=False, **kwargs: [SCOPED_DB])
        search_manager = MagicMock()
        search_manager.is_available.return_value = True
        search_manager.search_dual_index.side_effect = (
            lambda asset_query, file_query, entity_types: _opensearch_response([cell.hit for cell in cells])
        )
        processor = search_module.DualIndexResponseProcessor(manager)
        body = {"query": "part", "includeArchived": True}
        event = {"body": json.dumps(body)}

        with _policies(*_template_set("database-user.json")):
            if endpoint == "search":
                response = search_module.handle_post_request(
                    event, search_manager, search_module.DualIndexQueryBuilder(manager), processor, CLAIMS
                )
            else:
                response = search_module.handle_simple_post_request(
                    event, search_manager, search_module.SimpleSearchQueryBuilder(manager), processor, CLAIMS
                )

        assert response["statusCode"] == 200
        returned = [hit["_id"] for hit in json.loads(response["body"])["hits"]["hits"]]
        assert returned == _ids(_select(LIVE_ASSET, LIVE_FILE, ARCHIVED_FILE_OF_LIVE_ASSET,
                                        database_ids=(SCOPED_DB,)))
