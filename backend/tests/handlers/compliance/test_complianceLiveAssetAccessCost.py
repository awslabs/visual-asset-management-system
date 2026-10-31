# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the live-asset decision (D101) costs, in reads and engine checks, at 5,000 assets.

One database holds 5,000 live assets (every tenth tagged `locked`) and 50 archived ones, each with a
quarantined compliance record and an audit entry. For each kind of caller the tests count the
asset-table reads (Query, GetItem, BatchGetItem), the reads of compliance records by the overview
(Query, and BatchGetItem for the counts leaving withheld assets out), and the asset checks made on
the engine, for:

- the overview first page (rows and per-state counts), the Quarantine first page and the audit
  first page (database path), each at its default page size (100);
- a single record (the state read) of a tagged and of an untagged asset;
- Evaluate All.

The callers: no asset rules (the global Compliance Admin template); asset rules on the database id
alone (Database Admin, which may see the archive, and Database User, which may not); asset rules
that read `tags` (Database Admin plus the shipped `deny-tagged-assets.json` and a GET deny on the
tag, the round-16b role). The printed `COST` lines are the figures in the D101 report; the
assertions bound them.
"""

import json
from time import perf_counter
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, claims_for, mirror_engine, real_constants, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceLiveAssetAccess import (
    TAG, _enforcer, _tag_read_deny, _template,
)
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceTrigger as trigger

DB = "cost-live-db"
SCHEMA = "cost-live-schema"
LIVE_COUNT = 5000
ARCHIVED_COUNT = 50
SERVICES = (store, evaluate_svc, quarantine_svc, audit_svc, trigger)
HANDLERS = (evaluate_svc, quarantine_svc, audit_svc)

CALLERS = {
    "no asset rules (compliance-global-admin)": lambda: (
        ["c-global"], _template("compliance-global-admin.json", "c-global")),
    "per database, archive allowed (database-admin)": lambda: (
        ["c-dba"], _template("database-admin.json", "c-dba", DB)),
    "per database, archive refused (database-user)": lambda: (
        ["c-user"], _template("database-user.json", "c-user", DB)),
    "per asset (database-admin + tag deny)": lambda: (
        ["c-tag"], _template("database-admin.json", "c-tag", DB)
        + _template("deny-tagged-assets.json", "c-tag") + _tag_read_deny("c-tag")),
}
NO_RULES, ADMIN, USER, TAG_DENY = list(CALLERS)


def _live_id(index):
    return f"c{index:05d}"


def _tagged(index):
    return index % 10 == 0


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


class _CountingTable:
    """A table whose Query, GetItem and BatchGetItem calls are counted."""

    def __init__(self, table, calls, prefix):
        self._table = table
        self._calls = calls
        self._prefix = prefix
        counting = self

        class _Client:
            def batch_get_item(self, **kwargs):
                counting._count("BatchGetItem")
                return table.meta.client.batch_get_item(**kwargs)

            def __getattr__(self, name):
                return getattr(table.meta.client, name)

        class _Meta:
            client = _Client()

        self.meta = _Meta()

    def _count(self, name):
        key = f"{self._prefix} {name}"
        self._calls[key] = self._calls.get(key, 0) + 1

    def query(self, **kwargs):
        self._count("Query")
        return self._table.query(**kwargs)

    def get_item(self, **kwargs):
        self._count("GetItem")
        return self._table.get_item(**kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


@pytest.fixture(scope="module")
def world():
    """One seeded database, shared by every caller (the reads and checks are counted per call)."""
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    with mock_aws(), patch.object(authz_module, "log_authorization", MagicMock()):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        for active in patches:
            active.start()
        try:
            doubles["databaseStorage"].put_item(Item={
                "databaseId": DB, "complianceSchemaName": SCHEMA, "complianceAutoEval": False})
            doubles["complianceSchemaStorage"].put_item(
                Item=schema_row(SCHEMA, body=RULES_SCHEMA_BODY, database_id="GLOBAL"))
            assets, states = doubles["assetStorage"], doubles["complianceAssetStateStorage"]
            audit = doubles["complianceAuditStorage"]
            with assets.batch_writer() as asset_batch, states.batch_writer() as state_batch, \
                    audit.batch_writer() as audit_batch:
                for index in range(LIVE_COUNT + ARCHIVED_COUNT):
                    archived = index >= LIVE_COUNT
                    asset_id = f"z{index:05d}" if archived else _live_id(index)
                    name = f"Asset {index}"
                    asset_batch.put_item(Item={
                        "databaseId": f"{DB}#deleted" if archived else DB, "assetId": asset_id,
                        "assetName": name, "assetType": ".glb", "description": "x" * 300,
                        "tags": [TAG] if _tagged(index) else ["ok"],
                        **({"status": "archived"} if archived else {})})
                    state_batch.put_item(Item={
                        "databaseId": DB, "assetId": asset_id, "schemaName": SCHEMA,
                        "schemaSource": "database", "complianceState": "quarantined",
                        "quarantineReason": "rule failed", "assetName": name,
                        "assetNameSort": f"{name.lower()}#{asset_id}",
                        "assetSearchText": f"{name.lower()} {asset_id}",
                        "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
                    audit_batch.put_item(Item=build_audit_item(
                        DB, asset_id, "compliance_check", "SYSTEM_USER", {},
                        timestamp=f"2026-10-01T00:00:00.{index:06d}+00:00", asset_name=name))
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()
            authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


def _measure(world, caller, method, path, params=None, query=None, request_body=None):
    """(response body, cost) of one request by `caller`."""
    roles, constraints = CALLERS[caller]()
    real = _enforcer(roles, constraints)
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = real.enforce
    mirror_engine(stand_in, real)
    checks, check_seconds = [], []

    class CountingEngine:
        """The real engine, its asset checks (the calls `AssetAccess` makes) counted and timed."""

        def enforce(self, obj, action):
            checks.append(obj.get("object__type"))
            started = perf_counter()
            try:
                return real.service_object.enforce(obj, action)
            finally:
                check_seconds.append(perf_counter() - started)
    stand_in.service_object = CountingEngine()
    calls = {}
    asset_table = _CountingTable(world["assetStorage"], calls, "asset")
    state_table = _CountingTable(world["complianceAssetStateStorage"], calls, "record")
    service = {"/compliance/state": evaluate_svc, "/compliance/evaluate": evaluate_svc,
               "/compliance/quarantine": quarantine_svc,
               "/compliance/audit": audit_svc}[[prefix for prefix in (
                   "/compliance/state", "/compliance/evaluate", "/compliance/quarantine",
                   "/compliance/audit") if path.startswith(prefix)][0]]
    patches = [patch.object(module, "CasbinEnforcer", return_value=stand_in)
               for module in HANDLERS]
    patches += [patch.object(module, "request_to_claims", claims_for("cost-user"))
                for module in HANDLERS]
    patches += [patch.object(module, "asset_table", asset_table)
                for module in (evaluate_svc, quarantine_svc, audit_svc)]
    # The counts' BatchGetItem of compliance records (withheld assets) goes through this table.
    patches += [patch.object(evaluate_svc, "asset_state_table", state_table),
                patch.object(store, "run_evaluation", return_value={
                    "evaluationId": "e", "verdict": "compliant", "complianceState": "compliant"}),
                patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()),
                patch("customLogging.auditLogging.log_authorization_denials", MagicMock())]
    for active in patches:
        active.start()
    try:
        started = perf_counter()
        response = service.lambda_handler(rest_event(method, path, params, query, request_body),
                                          MagicMock())
        elapsed = perf_counter() - started
    finally:
        for active in reversed(patches):
            active.stop()
    assert response["statusCode"] in (200, 403), response
    cost = {key: calls.get(key, 0) for key in (
        "asset Query", "asset GetItem", "asset BatchGetItem", "record Query",
        "record BatchGetItem")}
    cost["asset checks"] = checks.count("asset")
    cost["check seconds"] = round(sum(check_seconds), 4)
    cost["seconds (moto)"] = round(elapsed, 3)
    cost["status"] = response["statusCode"]
    return json.loads(response["body"]), cost


SURFACES = {
    "overview first page": ("GET", f"/compliance/state/{DB}", {"databaseId": DB}, None),
    "quarantine first page": ("GET", "/compliance/quarantine", None, {"databaseId": DB}),
    "audit first page": ("GET", "/compliance/audit", None, {"databaseId": DB}),
}


def _rows(surface, body):
    key = {"overview first page": "assets", "quarantine first page": "quarantinedAssets",
           "audit first page": "entries"}[surface]
    return [row["assetId"] for row in body[key]]


@pytest.mark.unit
@pytest.mark.parametrize("surface", list(SURFACES))
@pytest.mark.parametrize("caller", list(CALLERS))
def test_the_first_pages_at_5000_assets(world, caller, surface):
    method, path, params, query = SURFACES[surface]
    body, cost = _measure(world, caller, method, path, params, query)
    print(f"COST {surface} | {caller}: {cost}")
    rows = _rows(surface, body)
    assert len(rows) == 100, (caller, surface, len(rows))
    tagged = [asset_id for asset_id in rows
              if asset_id.startswith("c") and _tagged(int(asset_id[1:]))]
    if caller == TAG_DENY:
        assert tagged == [], (surface, tagged)
    elif surface != "audit first page" or caller != USER:
        assert tagged, (caller, surface)
    # No caller reads an asset row one by one in a listing.
    assert cost["asset GetItem"] == 0, (caller, surface, cost)
    if caller == NO_RULES:
        # No asset rules: no asset check and no live-row read.
        assert cost["asset checks"] == 0 and cost["asset BatchGetItem"] == 0, cost
    elif caller in (ADMIN, USER):
        # On the database id alone: at most one check of the live assets and one of the archive,
        # and no live-row read.
        assert cost["asset checks"] <= 2 and cost["asset BatchGetItem"] == 0, cost
    else:
        # On each asset's row: one check per distinct (databaseId, tags), no more than four here
        # (two tag lists, live and archived), and rows read 100 to a BatchGetItem or with one
        # partition read; the checks themselves take milliseconds.
        assert cost["asset checks"] <= 4, cost
        assert cost["check seconds"] < 0.1, cost
        if surface == "overview first page":
            # The live partition once (5,000 projected rows, a few pages). The counts are of the
            # readable assets' records: their ids come from the queries every caller's counts make
            # (six states and the error overlay), so no record is read by key, and the archived
            # partition is not read (an archived asset has no live row to be readable).
            assert cost["asset BatchGetItem"] == 0 and cost["asset Query"] <= 6, cost
            assert cost["record BatchGetItem"] == 0, cost
            assert cost["record Query"] <= (len(evaluate_svc.OVERVIEW_STATES) + 1
                                            + evaluate_svc.listing.DEFAULT_READ_BUDGET), cost
            summary = body["summary"]
            assert body["totalAssets"] == LIVE_COUNT - LIVE_COUNT // 10
            assert summary["quarantined"] == body["totalAssets"]
        else:
            # A page of 100 rows: about one BatchGetItem per 100 rows decided (a refused row is
            # decided too, so a little over one page).
            assert 1 <= cost["asset BatchGetItem"] <= 3, cost


@pytest.mark.unit
@pytest.mark.parametrize("caller", list(CALLERS))
def test_a_single_record(world, caller):
    costs = {}
    for label, index in (("untagged", 1), ("tagged", 10)):
        asset_id = _live_id(index)
        _body, costs[label] = _measure(world, caller, "GET", f"/compliance/state/{DB}/{asset_id}",
                                       {"databaseId": DB, "assetId": asset_id})
    print(f"COST single record | {caller}: {costs}")
    for label, cost in costs.items():
        # The state read reads the asset's archived key once whoever the caller, to answer an
        # archived asset with the state `archived` (one GetItem before D101 too); the decision
        # shares it.
        assert cost["asset BatchGetItem"] == 0 and cost["asset Query"] == 0, (label, cost)
        if caller == NO_RULES:
            assert cost["asset checks"] == 0 and cost["asset GetItem"] == 1, cost
            assert cost["status"] == 200
        elif caller == ADMIN:
            # The archive's check decides the live asset too: one check, no further read.
            assert cost["asset checks"] == 1 and cost["asset GetItem"] == 1, cost
            assert cost["status"] == 200
        elif caller == USER:
            # Live allowed, archive refused on the compliance object (no asset check for it).
            assert cost["asset checks"] == 1 and cost["asset GetItem"] == 1, cost
            assert cost["status"] == 200
        else:
            # The archived key, then the live row (the one new read): two GetItems, one check.
            assert cost["asset checks"] == 1 and cost["asset GetItem"] == 2, cost
            assert cost["status"] == (403 if label == "tagged" else 200), cost


@pytest.mark.unit
@pytest.mark.parametrize("caller", [ADMIN, TAG_DENY])
def test_evaluate_all_reads_the_live_partition_once(world, caller):
    body, cost = _measure(world, caller, "POST", f"/compliance/evaluate/{DB}",
                          {"databaseId": DB}, request_body={})
    print(f"COST evaluate all | {caller}: {cost} | skippedNotAuthorized "
          f"{body.get('skippedNotAuthorized')}")
    assert cost["asset BatchGetItem"] == 0
    if caller == TAG_DENY:
        assert body["skippedNotAuthorized"] == LIVE_COUNT // 10
        assert cost["asset checks"] <= 4
    else:
        assert body["skippedNotAuthorized"] == 0
        assert cost["asset checks"] <= 2
