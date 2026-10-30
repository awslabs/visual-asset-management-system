# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An archived asset's compliance data is decided as opening the asset is (D90).

A role made from the Database Admin template plus a deny on `databaseId contains #deleted` for the
asset and database object types (the documented way to hide archived records) was refused the
archived asset by the asset routes, but compliance showed it: the state read, the overview counts,
the sweep's `skippedArchived`, a cascade's node. Compliance decided an archived asset on the
compliance permission of the database's archive alone.

Now `AssetAccess` decides it on both the compliance permission and the asset GET the asset
routes make on the archived row, with the row's own fields. The role-set parity tests
(`test_complianceArchivedAssetAccess`, `test_complianceArchivedCounts`,
`test_complianceCascadeAuditArchived`) cover every surface for every shipped role and the two D90
roles. These tests cover the mechanics:

- whether the asset check is decided per database or per asset is the engine's answer
  (`policy_object_fields`), so the shipped templates keep the reads they had;
- a decision per asset reads each archived row once per request and checks each once;
- the denials of those checks are written in one batch, not one audit write per archived asset;
- the cost of an audit listing over 1,000 archived assets (printed `COST` lines; the assertions
  bound it).
"""

import json
from time import perf_counter
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    claims_for, enforcer, mirror_engine, real_constants, rest_event,
)
from backend.tests.handlers.compliance.test_complianceArchivedAssetAccess import (
    ARCHIVE_CONTAINS_DENY_SET, DENIED_TAG, TAG_DENY_SET, VARIABLES, _enforcer, _role_set,
)
from common.compliance import assetAccess
from common.compliance.assetAccess import AssetAccess
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store

DB = "arch-db"
ARCHIVED_DB = f"{DB}#deleted"
OTHER_FIELDS = ["assetName", "assetType", "tags"]


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


@pytest.fixture
def role_enforcer():
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    with patch.object(authz_module, "log_authorization", MagicMock()):
        yield lambda set_name: _enforcer(*_role_set(set_name))
    authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


# ---------------------------------------------------------------------------------------------
# The engine's answer: does any of the caller's rules read an asset field but the database id?
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("template", shipped_constraints.template_names())
def test_only_a_template_with_a_rule_on_asset_fields_is_decided_per_asset(template, role_enforcer):
    """Every shipped template but `deny-tagged-assets.json` (its rules read `tags`) has asset rules
    on the database id alone, so its archived assets are decided per database: the reads they made
    before D90."""
    found = role_enforcer(f"template:{template}")
    assert found.policy_object_fields(OTHER_FIELDS) == (
        frozenset({"tags"}) if template == "deny-tagged-assets.json" else frozenset()), template


@pytest.mark.unit
def test_the_two_d90_roles(role_enforcer):
    assert role_enforcer(ARCHIVE_CONTAINS_DENY_SET).policy_object_fields(OTHER_FIELDS) == frozenset()
    assert role_enforcer(TAG_DENY_SET).policy_object_fields(OTHER_FIELDS) == frozenset({"tags"})


@pytest.mark.unit
def test_no_field_asked_reads_nothing_and_no_engine_is_conservative(role_enforcer):
    found = role_enforcer(TAG_DENY_SET)
    assert found.policy_object_fields([]) == frozenset()
    found.service_object._enforcer = None
    assert found.policy_object_fields(["tags", "assetName"]) == frozenset({"tags", "assetName"})


# ---------------------------------------------------------------------------------------------
# Per asset: each archived row read once, checked once; denials batched
# ---------------------------------------------------------------------------------------------
def _tagged_table(rows, live=None):
    """An asset-table stand-in holding archived `rows` (assetId -> row) under DB#deleted, and the
    `live` rows (assetId -> row, none by default) under DB, read by key or with BatchGetItem."""
    live = live or {}
    table = MagicMock(name="asset_table")
    table.name = "assets"

    def get_item(Key, **_):
        held = rows if Key["databaseId"] == ARCHIVED_DB else live if Key["databaseId"] == DB else {}
        return {"Item": dict(held[Key["assetId"]])} if Key["assetId"] in held else {}

    def batch_get_item(RequestItems):
        keys = RequestItems["assets"]["Keys"]
        return {"Responses": {"assets": [dict(live[key["assetId"]]) for key in keys
                                         if key["databaseId"] == DB and key["assetId"] in live]},
                "UnprocessedKeys": {}}
    table.get_item.side_effect = get_item
    table.meta.client.batch_get_item.side_effect = batch_get_item
    table.query.side_effect = lambda **_: {"Items": [dict(row) for row in rows.values()]}
    return table


def _tag_reader():
    """An enforcer stand-in whose policy reads tags and whose asset GET refuses DENIED_TAG."""
    instance = enforcer()
    instance.enforce.side_effect = lambda obj, action: not (
        obj.get("object__type") == "asset" and DENIED_TAG in (obj.get("tags") or []))
    mirror_engine(instance)
    instance.policy_object_fields.return_value = frozenset({"tags"})
    return instance


ROWS = {
    "a": {"databaseId": ARCHIVED_DB, "assetId": "a", "assetName": "A", "tags": [DENIED_TAG]},
    "b": {"databaseId": ARCHIVED_DB, "assetId": "b", "assetName": "B", "tags": []},
}


def _allow_all(_stored_id):
    return True


LIVE_ROWS = {
    "live": {"databaseId": DB, "assetId": "live", "assetName": "Live", "tags": []},
    "live-tagged": {"databaseId": DB, "assetId": "live-tagged", "tags": [DENIED_TAG]},
}


@pytest.mark.unit
def test_a_single_record_is_decided_on_its_own_row():
    """An archived asset on its archived row; a live one on its live row (D101), each one
    GetItem of its own key, and the live row only once the archived key is known to be empty."""
    table = _tagged_table(ROWS, LIVE_ROWS)
    archived = AssetAccess(table, _tag_reader())
    assert archived.decide(DB, "a", _allow_all) is False
    assert archived.decide(DB, "b", _allow_all) is True
    assert archived.decide(DB, "live", _allow_all) is True
    assert archived.decide(DB, "live-tagged", _allow_all) is False
    keys = [(call.kwargs["Key"]["databaseId"], call.kwargs["Key"]["assetId"])
            for call in table.get_item.call_args_list]
    assert keys == [(ARCHIVED_DB, "a"), (ARCHIVED_DB, "b"), (ARCHIVED_DB, "live"), (DB, "live"),
                    (ARCHIVED_DB, "live-tagged"), (DB, "live-tagged")]
    table.query.assert_not_called()
    table.meta.client.batch_get_item.assert_not_called()


@pytest.mark.unit
def test_a_listing_reads_the_archive_once_and_checks_each_distinct_archived_asset_once():
    table = _tagged_table(ROWS, LIVE_ROWS)
    reader = _tag_reader()
    archived = AssetAccess(table, reader)
    rows = ["live", "a", "b", "live-tagged", "a", "b", "live-gone"]
    # The listing notes the page's keys (`listing.query_page`'s `prepare`): their live rows are
    # read in one BatchGetItem when the first is needed.
    archived.expect((DB, asset_id) for asset_id in rows)
    verdicts = [archived.decide(DB, asset_id, _allow_all, cache_key="GET", listing=True)
                for asset_id in rows]
    assert verdicts == [True, False, True, False, False, True, True]
    assert archived.withheld(DB, _allow_all, cache_key="GET") == frozenset({"a"})
    assert archived.withholds(DB, "a", _allow_all, cache_key="GET") is True
    assert archived.withholds(DB, "b", _allow_all, cache_key="GET") is False
    assert table.query.call_count == 1
    table.get_item.assert_not_called()
    assert table.meta.client.batch_get_item.call_count == 1
    batch = table.meta.client.batch_get_item.call_args.kwargs["RequestItems"]["assets"]
    assert {key["assetId"] for key in batch["Keys"]} == {"live", "live-tagged", "live-gone"}
    assert set(batch["ExpressionAttributeNames"].values()) == {"databaseId", "assetId", "tags"}
    # One check per distinct (databaseId, tags) of the rows decided: two archived rows, and three
    # live ones (the untagged, the tagged, and the asset with no row, decided on the database).
    assert reader.service_object.enforce.call_count == 5
    reader.enforce.assert_not_called()


@pytest.mark.unit
def test_rows_that_agree_on_the_fields_the_rules_read_share_one_check():
    rows = {f"n{i}": {"databaseId": ARCHIVED_DB, "assetId": f"n{i}", "assetName": f"Name {i}",
                      "tags": [DENIED_TAG] if i % 2 else ["ok"]} for i in range(10)}
    reader = _tag_reader()
    archived = AssetAccess(_tagged_table(rows), reader)
    assert archived.withheld(DB, _allow_all) == frozenset(f"n{i}" for i in range(1, 10, 2))
    # The rules read `tags` only: two tag lists, two checks, whatever the names.
    assert reader.service_object.enforce.call_count == 2
    query = archived._asset_table.query.call_args.kwargs
    assert set(query["ExpressionAttributeNames"].values()) == {"databaseId", "assetId", "tags"}


@pytest.mark.unit
def test_a_record_that_names_an_asset_no_longer_archived_is_not_shown_per_asset():
    """Per asset, a record (a cascade node, a count on an audit entry) whose asset left the archive,
    or that counts archived assets without their ids, cannot be decided on the asset's fields."""
    archived = AssetAccess(_tagged_table(ROWS), _tag_reader())
    assert archived.archived_asset_visible(DB, "b", _allow_all, "GET") is True
    assert archived.archived_asset_visible(DB, "a", _allow_all, "GET") is False
    assert archived.archived_asset_visible(DB, "gone", _allow_all, "GET") is False
    assert archived.archived_asset_visible(DB, None, _allow_all, "GET") is False
    per_database = AssetAccess(_tagged_table(ROWS), enforcer())
    assert per_database.archived_asset_visible(DB, "gone", _allow_all, "GET") is True
    assert per_database.archived_asset_visible(DB, None, _allow_all, "GET") is True


@pytest.mark.unit
def test_without_an_enforcer_no_asset_is_visible():
    """Without an enforcer whether the caller has asset rules cannot be told, so the asset GET is
    asked and fails closed, for a live asset as for an archived one (D101)."""
    archived = AssetAccess(_tagged_table(ROWS, LIVE_ROWS))
    assert archived.decide(DB, "b", _allow_all) is False
    assert archived.decide(DB, "live", _allow_all) is False
    assert archived.withheld(DB, _allow_all) == frozenset({"a", "b"})


@pytest.mark.unit
def test_the_denials_are_written_in_one_batch():
    assetAccess._PENDING_DENIALS.clear()
    # Each with its own tag list, so each is its own check and its own denial.
    many = {f"t{i}": {"databaseId": ARCHIVED_DB, "assetId": f"t{i}", "tags": [DENIED_TAG, f"x{i}"]}
            for i in range(25)}
    archived = AssetAccess(_tagged_table(many), _tag_reader())
    assert len(archived.withheld(DB, _allow_all)) == 25
    with patch("customLogging.auditLogging.log_authorization_denials") as write:
        assetAccess.flush_denials()
        assetAccess.flush_denials()
    write.assert_called_once()
    claims, records = write.call_args.args
    assert claims["tokens"]
    assert len(records) == 25
    assert {record["action"] for record in records} == {"GET"}
    assert {record["obj"]["assetId"] for record in records} == set(many)


@pytest.mark.unit
def test_the_handlers_flush_the_denials_of_the_request():
    """Each compliance handler that makes archived-asset checks writes their denials once its
    response is built."""
    from handlers.compliance import (complianceAuditService, complianceCascadeService,
                                     complianceQuarantineService, complianceSchemaBindingService)
    for module in (evaluate_svc, complianceAuditService, complianceCascadeService,
                   complianceQuarantineService, complianceSchemaBindingService):
        # A request without tokens is refused inside the handler; the denials are flushed anyway.
        with patch.object(module, "request_to_claims", claims_for()), \
                patch.object(module, "flush_denials") as flush:
            response = module.lambda_handler(rest_event("GET", "/compliance/x"), MagicMock())
        assert response["statusCode"] == 403, module.__name__
        assert flush.call_count == 1, module.__name__


# ---------------------------------------------------------------------------------------------
# Cost: the audit listing of a database with 1,000 archived assets
# ---------------------------------------------------------------------------------------------
# The overview and the quarantine list leave every archived asset out for every role (D91), so
# the listing where D90 decides what a role sees is the audit trail: each entry of an archived
# asset is listed only for a role that may see that asset.
ARCHIVED_COUNT = 1000
LIVE_COUNT = 200
PAGE = 100


class _Counting:
    """An asset table whose reads (calls and items returned) are counted."""

    def __init__(self, table):
        self._table = table
        self.calls = {"query": 0, "get_item": 0}
        self.items = 0

    def query(self, **kwargs):
        self.calls["query"] += 1
        response = self._table.query(**kwargs)
        self.items += len(response.get("Items", []))
        return response

    def get_item(self, **kwargs):
        self.calls["get_item"] += 1
        return self._table.get_item(**kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


def _archived_id(index):
    return f"z{index:05d}"


def _audit_listing_cost(set_name, role_enforcer):
    real = role_enforcer(set_name)
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = real.enforce
    mirror_engine(stand_in, real)
    engine_calls = []
    check_seconds = []

    class CountingEngine:
        """The real engine, its calls (the archived-asset checks) counted and timed."""

        def enforce(self, obj, action):
            engine_calls.append(obj.get("object__type"))
            started = perf_counter()
            try:
                return real.service_object.enforce(obj, action)
            finally:
                check_seconds.append(perf_counter() - started)
    stand_in.service_object = CountingEngine()
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, audit_svc))
        for active in patches:
            active.start()
        try:
            assets, audit = doubles["assetStorage"], doubles["complianceAuditStorage"]
            doubles["databaseStorage"].put_item(Item={"databaseId": DB})
            with assets.batch_writer() as asset_batch, audit.batch_writer() as audit_batch:
                for index in range(ARCHIVED_COUNT + LIVE_COUNT):
                    archived = index < ARCHIVED_COUNT
                    asset_id = _archived_id(index) if archived else f"a{index:05d}"
                    asset_batch.put_item(Item={
                        "databaseId": ARCHIVED_DB if archived else DB, "assetId": asset_id,
                        "assetName": f"Asset {index}", "assetType": ".glb",
                        "description": "x" * 400,
                        "tags": [DENIED_TAG] if archived and index % 10 == 0 else ["ok"],
                        **({"status": "archived"} if archived else {})})
                    # The archived assets' entries are the newest, so they fill the first page.
                    audit_batch.put_item(Item=build_audit_item(
                        DB, asset_id, "compliance_check", "SYSTEM_USER", {},
                        timestamp=f"2026-10-{'29' if archived else '01'}T00:00:00.{index:06d}+00:00",
                        asset_name=f"Asset {index}"))
            counting = _Counting(doubles["assetStorage"])
            with patch.object(audit_svc, "asset_table", counting), \
                    patch.object(audit_svc, "CasbinEnforcer", return_value=stand_in), \
                    patch.object(audit_svc, "request_to_claims", claims_for("cost-user")), \
                    patch("customLogging.auditLogging.log_authorization_denials") as denials:
                started = perf_counter()
                response = audit_svc.lambda_handler(
                    rest_event("GET", "/compliance/audit", None,
                               {"databaseId": DB, "maxItems": str(PAGE)}), MagicMock())
                elapsed = perf_counter() - started
            assert response["statusCode"] == 200, response
            listed = [entry["assetId"] for entry in json.loads(response["body"])["entries"]]
            return {"listed": len(listed), "archived listed": sum(a.startswith("z") for a in listed),
                    "tagged listed": sum(a.startswith("z") and int(a[1:]) % 10 == 0 for a in listed),
                    "reads": dict(counting.calls), "items": counting.items,
                    "asset checks": engine_calls.count("asset"),
                    "denial batches": denials.call_count,
                    "denials": sum(len(c.args[1]) for c in denials.call_args_list),
                    "check seconds": round(sum(check_seconds), 3),
                    "seconds": round(elapsed, 3)}
        finally:
            for active in reversed(patches):
                active.stop()


@pytest.mark.unit
@pytest.mark.parametrize("set_name", [
    "template:database-admin.json", ARCHIVE_CONTAINS_DENY_SET, TAG_DENY_SET,
], ids=["database admin", "archive deny", "tag deny"])
def test_the_cost_of_an_audit_listing_over_1000_archived_assets(set_name, role_enforcer):
    cost = _audit_listing_cost(set_name, role_enforcer)
    print(f"COST {set_name}: {cost}")
    if set_name == "template:database-admin.json":
        # Allowed on the archive: one asset check for the database (the archive's answer also
        # decides its live assets), and nothing read.
        assert cost["listed"] == PAGE and cost["archived listed"] == PAGE
        assert cost["reads"] == {"query": 0, "get_item": 0}
        assert cost["asset checks"] == 1 and cost["denials"] == 0
    elif set_name == ARCHIVE_CONTAINS_DENY_SET:
        # Refused the archive: two asset checks, the archive and the database's live assets (the
        # archive's answer decides the live assets only when it allows), and the archived ids read
        # once (keys only), as for a role refused the compliance object on the archive.
        assert cost["archived listed"] == 0
        assert cost["reads"]["get_item"] == 0 and cost["reads"]["query"] == 1
        assert cost["items"] == ARCHIVED_COUNT
        assert cost["asset checks"] == 2
        assert cost["denial batches"] == 1 and cost["denials"] == 1
    else:
        # One Query of the archive, projected on the field the rules read (`tags`), and one asset
        # check per distinct tag list (two here, not one per archived asset); the one denial is
        # written in one batch. The tagged archived assets are left out, the others listed.
        assert cost["listed"] == PAGE
        assert cost["tagged listed"] == 0 and cost["archived listed"] == PAGE
        assert cost["reads"] == {"query": 1, "get_item": 0}
        assert cost["items"] == ARCHIVED_COUNT
        assert cost["asset checks"] == 2
        assert cost["denial batches"] == 1 and cost["denials"] == 1
