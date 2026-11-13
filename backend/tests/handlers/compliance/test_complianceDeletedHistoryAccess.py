# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A deleted database's history is decided as the live database's is (D168).

Deleting a database moves its compliance data under `<databaseId>#deleted`; its assets stay in the
asset archive under the same id. The documented rule: the history is decided on that id, so a role
allowed on every database reaches it, and, for a user with asset rules, each archived asset's own
read decides its per-asset records. A role combining Compliance Global Admin with Database User of
another database read every per-asset audit entry of a deleted history (round 29) while for a live
database it read the database-level entries only: the asset rule was skipped for an id that is
already `#deleted`.

Run with the production enforcer of each role (the shipped templates, the real permission field
matrix) on moto tables built from the CDK definitions, for a live database and a deleted one with
the same shape: database-level entries and per-asset entries of two assets, one carrying a tag the
tag-deny reader is refused on, each history long enough to page. Hidden entries are absent (not
redacted), the pages hold no duplicate and every page's token is followed to the end; the single
asset history, the asset state and the overview follow the same decision.
"""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    body_of, claims_for, mirror_engine, real_constants, rest_event,
)
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store

LIVE_DB = "live-db"
GONE_DB = "gone-db"
DELETED = f"{GONE_DB}#deleted"
OTHER_DB = "other-db"
DENIED_TAG = "restricted"
USER_ID = "deleted-history-user"
# Two assets per history: one plain, one carrying the tag the tag-deny reader is refused on.
ASSETS = {LIVE_DB: ("live-plain", "live-tagged"), DELETED: ("gone-plain", "gone-tagged")}
ENTRIES_PER_ASSET = 3
DATABASE_ENTRIES = 2
SERVICES = (store, evaluate_svc, audit_svc)


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


def _tag_deny(role):
    return [{
        "constraintId": "deny-tag-asset", "name": "deny-tag-asset", "objectType": "asset",
        "criteriaAnd": [{"field": "tags", "id": "t", "operator": "is_one_of",
                         "value": DENIED_TAG}],
        "criteriaOr": [],
        "groupPermissions": [{"groupId": role, "id": f"deny-tag-{action}", "permission": action,
                              "permissionType": "deny"} for action in ("GET", "PUT", "POST",
                                                                       "DELETE")],
        "userPermissions": [],
    }]


def _template(name, role, database_id=None):
    variables = {"ROLE_NAME": role}
    if database_id:
        variables["DATABASE_ID"] = database_id
    return shipped_constraints.template_constraints(name, role, variables)


def _role(name):
    """(roles, constraints) of each role the D168 matrix names."""
    if name == "admin":
        return ["admin"], shipped_constraints.seeded_constraints("admin")
    if name == "compliance global admin":
        return ["cga"], _template("compliance-global-admin.json", "cga")
    if name == "compliance global admin + database user of another database":
        return ["cga", "dbu"], (_template("compliance-global-admin.json", "cga")
                                + _template("database-user.json", "dbu", OTHER_DB))
    if name == "database admin of the deleted database":
        return ["dba"], _template("database-admin.json", "dba", GONE_DB)
    if name == "database admin of the live database":
        return ["dba"], _template("database-admin.json", "dba", LIVE_DB)
    if name == "tag-deny reader of the deleted database":
        return ["dba"], _template("database-admin.json", "dba", GONE_DB) + _tag_deny("dba")
    if name == "tag-deny reader of the live database":
        return ["dba"], _template("database-admin.json", "dba", LIVE_DB) + _tag_deny("dba")
    raise KeyError(name)


def _enforcer(roles, constraints):
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    with patch.object(authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=[{"userId": USER_ID, "roleName": r} for r in roles]), \
            patch.object(authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
                         return_value=constraints):
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": list(roles),
                                            "mfaEnabled": True})


def _seed(doubles):
    t = doubles
    t["databaseStorage"].put_item(Item={"databaseId": LIVE_DB})
    t["databaseStorage"].put_item(Item={"databaseId": OTHER_DB})
    # The row a deleted database leaves, and its finished move.
    t["databaseStorage"].put_item(Item={"databaseId": DELETED})
    t["complianceEvaluationStorage"].put_item(Item={
        "evaluationId": f"databaseRetirement#{GONE_DB}", "recordType": "databaseRetirement",
        "databaseId": GONE_DB, "deletedId": DELETED, "state": "done",
        "deletedAt": "2026-11-01T00:00:00+00:00", "generation": 1})
    clock = 0
    for scope, (plain, tagged) in ASSETS.items():
        for asset_id in (plain, tagged):
            # A live asset's row under the live id; a deleted database's in its archive.
            t["assetStorage"].put_item(Item={
                "databaseId": scope, "assetId": asset_id, "assetName": asset_id,
                "tags": [DENIED_TAG] if asset_id == tagged else ["ok"],
                **({"status": "archived", "archivedAt": "2026-10-31T00:00:00+00:00"}
                   if scope == DELETED else {})})
            t["complianceAssetStateStorage"].put_item(Item={
                "databaseId": scope, "assetId": asset_id, "schemaName": "s",
                "schemaSource": "database", "complianceState": "compliant",
                "assetName": asset_id, "assetNameSort": f"{asset_id}#{asset_id}",
                "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
            for n in range(ENTRIES_PER_ASSET):
                clock += 1
                t["complianceAuditStorage"].put_item(Item={
                    "entryId": f"{scope}-{asset_id}-{n}", "databaseId:assetId": f"{scope}:{asset_id}",
                    "databaseId": scope, "assetId": asset_id,
                    "timestamp": f"2026-10-02T00:00:{str(clock).zfill(2)}+00:00",
                    "eventType": "schema_binding_applied_to_asset", "allListPartition": "audit",
                    "actor": "administrator"})
        for n in range(DATABASE_ENTRIES):
            clock += 1
            t["complianceAuditStorage"].put_item(Item={
                "entryId": f"{scope}-db-{n}", "databaseId:assetId": f"{scope}:*",
                "databaseId": scope, "assetId": "*",
                "timestamp": f"2026-10-02T00:00:{str(clock).zfill(2)}+00:00",
                "eventType": "schema_bound_to_database", "allListPartition": "audit",
                "actor": "administrator"})


class World:
    """Moto tables with the two histories, the services' CasbinEnforcer the role's production
    enforcer."""

    def __init__(self, role):
        self.role = role

    def __enter__(self):
        previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
        self._restore = previous
        self._log = patch.object(authz_module, "log_authorization", MagicMock())
        self._log.start()
        enforcer = _enforcer(*_role(self.role))
        stand_in = MagicMock(name="CasbinEnforcer")
        stand_in.enforceAPI.return_value = True
        stand_in.enforce.side_effect = enforcer.enforce
        mirror_engine(stand_in, enforcer)
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        self.patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in)
                         for module in (evaluate_svc, audit_svc)]
        self.patches += [patch.object(module, "request_to_claims", claims_for(USER_ID))
                         for module in (evaluate_svc, audit_svc)]
        for active in self.patches:
            active.start()
        _seed(doubles)
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()
        self._log.stop()
        authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = self._restore

    def call(self, service, path, params=None, query=None):
        response = service.lambda_handler(rest_event("GET", path, params, query), MagicMock())
        assert response["statusCode"] < 500, (path, response)
        return response["statusCode"], body_of(response)

    def listing(self, query=None, page_size=2):
        """Every entry of the audit listing, following each page's token; asserts no duplicate."""
        entries, token, pages = [], None, 0
        while True:
            q = dict(query or {}, maxItems=str(page_size))
            if token:
                q["startingToken"] = token
            status, body = self.call(audit_svc, "/compliance/audit", query=q)
            assert status == 200, body
            entries.extend(body["entries"])
            pages += 1
            token = body.get("NextToken")
            if not token:
                break
            assert pages < 100
        ids = [entry["entryId"] for entry in entries]
        assert len(ids) == len(set(ids)), "an entry was returned twice"
        return entries, pages

    def asset_history(self, scope, asset_id):
        status, body = self.call(
            audit_svc, f"/compliance/audit/{scope}/{asset_id}",
            {"databaseId": scope.replace("#", "%23"), "assetId": asset_id},
            {"maxItems": "2"})
        if status != 200:
            return status, None
        entries, token = list(body["entries"]), body.get("NextToken")
        while token:
            status, body = self.call(
                audit_svc, f"/compliance/audit/{scope}/{asset_id}",
                {"databaseId": scope.replace("#", "%23"), "assetId": asset_id},
                {"maxItems": "2", "startingToken": token})
            entries.extend(body["entries"])
            token = body.get("NextToken")
        return 200, entries


def _shown(entries, scope):
    """{asset id or "*": number of entries} of `scope` among `entries`."""
    counts = {}
    for entry in entries:
        if entry["databaseId"] == scope:
            counts[entry["assetId"]] = counts.get(entry["assetId"], 0) + 1
    return counts


ALL = None  # every asset of the history
NONE = ()   # no asset: the database-level entries only

# role -> {history: the assets whose entries it reads (ALL, NONE, or the ids), or False when it
# reads nothing of that history}.
EXPECTED = {
    "admin": {LIVE_DB: ALL, DELETED: ALL},
    "compliance global admin": {LIVE_DB: ALL, DELETED: ALL},
    "compliance global admin + database user of another database": {LIVE_DB: NONE, DELETED: NONE},
    "database admin of the deleted database": {LIVE_DB: False, DELETED: ALL},
    "database admin of the live database": {LIVE_DB: ALL, DELETED: False},
    "tag-deny reader of the deleted database": {LIVE_DB: False, DELETED: ("gone-plain",)},
    "tag-deny reader of the live database": {LIVE_DB: ("live-plain",), DELETED: False},
}


def _expected_counts(scope, readable):
    if readable is False:
        return {}
    assets = ASSETS[scope] if readable is ALL else readable
    counts = {"*": DATABASE_ENTRIES}
    counts.update({asset_id: ENTRIES_PER_ASSET for asset_id in assets})
    return counts


@pytest.mark.unit
@pytest.mark.parametrize("role", sorted(EXPECTED))
class TestTheDeletedHistoryFollowsTheLiveDatabasesRule:

    def test_the_audit_listing_of_each_history(self, role):
        with World(role) as world:
            for scope, readable in EXPECTED[role].items():
                entries, pages = world.listing({"databaseId": scope.replace("#", "%23")})
                assert _shown(entries, scope) == _expected_counts(scope, readable), (role, scope)
                assert {entry["databaseId"] for entry in entries} <= {scope}
                if readable is not False:
                    # Long enough to page at two entries a page.
                    assert pages >= 2

    def test_the_listing_across_databases(self, role):
        with World(role) as world:
            entries, _ = world.listing()
            for scope, readable in EXPECTED[role].items():
                assert _shown(entries, scope) == _expected_counts(scope, readable), (role, scope)

    def test_each_asset_s_own_history_and_state(self, role):
        with World(role) as world:
            for scope, readable in EXPECTED[role].items():
                for asset_id in ASSETS[scope]:
                    visible = readable is ALL or (readable not in (False,)
                                                  and asset_id in (readable or ()))
                    status, entries = world.asset_history(scope, asset_id)
                    assert (status == 200) == visible, (role, scope, asset_id, status)
                    if visible:
                        assert len(entries) == ENTRIES_PER_ASSET
                    status, _ = world.call(
                        evaluate_svc, f"/compliance/state/{scope}/{asset_id}",
                        {"databaseId": scope.replace("#", "%23"), "assetId": asset_id})
                    assert (status == 200) == visible, (role, scope, asset_id, "state", status)

    def test_the_overview_of_each_history_counts_what_it_shows(self, role):
        with World(role) as world:
            for scope, readable in EXPECTED[role].items():
                status, body = world.call(evaluate_svc, f"/compliance/state/{scope}",
                                          {"databaseId": scope.replace("#", "%23")},
                                          {"maxItems": "200"})
                if readable is False:
                    assert status == 403, (role, scope, status)
                    continue
                assert status == 200, body
                shown = sorted(row["assetId"] for row in body["assets"])
                expected = sorted(ASSETS[scope] if readable is ALL else readable)
                assert shown == expected, (role, scope)
                assert body["totalAssets"] == len(expected)
                assert body["summary"]["compliant"] == len(expected)
