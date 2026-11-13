# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance request that works across databases records a denial for each database the caller
may not act on, as the core listings do.

Core VAMS records every failed authorization check: `CasbinEnforcer.enforce` writes one
authorization record per denial, and the database listing writes one for each database it leaves
out. The compliance requests that name no database and decide, database by database, which ones
they cover log the same way: approving a cascade, or creating one that starts at once (the reach
recorded for its executor), sweeping a schema bound in many databases, listing the audit trail
across databases, and the schema listing's create scopes. A request refused on what it names
(another database's cascade, an audit listing filtered to another database) writes its denial too.
The tests run the handlers on moto tables built from the CDK definitions with the production
enforcer of the shipped Compliance Admin and Compliance Read-Only templates, scoped to one database
of many.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
import common.compliance.assetAccess as asset_access
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    body_of, claims_for, mirror_engine, real_constants, rest_event,
)
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceSchemaService as schema_svc

USER_ID = "denials-user"
OWN = "denials-own-db"
# The other databases of the deployment: the caller may act on none of them.
OTHERS = [f"denials-other-{n:02d}" for n in range(46)]
PARENT = "denials-parent"
CHILD = "denials-child"
SCHEMA = "denials-global-schema"
CASCADE_OWN = "5c0e0000-0000-4000-8000-00000000d198"
CASCADE_OTHER = "5c0e0000-0000-4000-8000-00000000d199"
SERVICES = (store, cascade_svc, evaluate_svc, audit_svc, schema_svc)
HANDLERS = (cascade_svc, evaluate_svc, audit_svc, schema_svc)


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


def _real_enforcer(template, role):
    """The production CasbinEnforcer of a user holding one shipped template scoped to OWN."""
    constraints = shipped_constraints.template_constraints(
        template, role, {"ROLE_NAME": role, "DATABASE_ID": OWN})
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    with patch.object(authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=[{"userId": USER_ID, "roleName": role}]), \
            patch.object(authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
                         return_value=constraints):
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": [role],
                                            "mfaEnabled": True})


def _seed(doubles):
    now = datetime.now(timezone.utc)
    for database_id in [OWN] + OTHERS:
        doubles["databaseStorage"].put_item(Item={
            "databaseId": database_id, "complianceSchemaName": SCHEMA, "complianceAutoEval": False})
    for asset_id in (PARENT, CHILD):
        doubles["assetStorage"].put_item(Item={"databaseId": OWN, "assetId": asset_id,
                                               "assetName": asset_id, "tags": []})
    # The bound assets of the sweep: one in the caller's database, one in each of five others.
    bound = [(OWN, PARENT)] + [(other, f"asset-{other}") for other in OTHERS[:5]]
    for database_id, asset_id in bound:
        doubles["complianceAssetStateStorage"].put_item(Item={
            "databaseId": database_id, "assetId": asset_id, "schemaName": SCHEMA,
            "schemaSource": "database", "complianceState": "compliant",
            "assetName": asset_id, "assetNameSort": f"{asset_id}#{asset_id}",
            "assetSearchText": asset_id, "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
    doubles["complianceSchemaStorage"].put_item(Item={
        "schemaName": SCHEMA, "internalVersion": 1, "databaseId": "GLOBAL", "description": "",
        "schemaBody": json.dumps({"schemaFormat": "vams-rules-v1", "rules": {}}),
        "registeredAt": "2026-01-01T00:00:00+00:00", "registeredBy": "admin", "isSystem": False})
    for cascade_id, database_id in ((CASCADE_OWN, OWN), (CASCADE_OTHER, OTHERS[0])):
        doubles["complianceCascadeStorage"].put_item(Item={
            "cascadeId": cascade_id, "state": "pending_approval",
            "triggeredByDatabaseId": database_id, "triggeredByAssetId": PARENT,
            "triggeredByAssetName": PARENT, "createdAt": now.isoformat(), "requireApproval": True,
            "approvalTimeoutAt": (now + timedelta(hours=1)).isoformat(),
            "nodes": json.dumps({}), "executionOrder": json.dumps([])})
    for n, database_id in enumerate([OWN, OWN] + OTHERS[:3]):
        item = build_audit_item(database_id, PARENT, "compliance_check", "admin", {"n": n},
                                timestamp=f"2026-10-01T00:00:{n:02d}+00:00")
        item["entryId"] = f"denials-entry-{n}"
        doubles["complianceAuditStorage"].put_item(Item=item)


class _World:
    """The services on moto tables, for one caller, with every authorization denial record counted:
    those `CasbinEnforcer.enforce` writes (`log_authorization` with a refusal) and those the asset
    checks keep for `flush_denials`."""

    def __init__(self, template, role):
        self._template, self._role = template, role
        self.denials = []
        self.lambda_client = MagicMock(name="lambda_client")

    def __enter__(self):
        self._previous = (authz_module.casbin_user_policy_map,
                          authz_module.casbin_user_enforcer_map)
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, self.doubles = _cdkTables.table_doubles(resource, SERVICES)
        real = _real_enforcer(self._template, self._role)
        stand_in = MagicMock(name="CasbinEnforcer")
        stand_in.enforceAPI.return_value = True
        stand_in.enforce.side_effect = real.enforce
        mirror_engine(stand_in, real)

        def log_authorization(_claims, authorized, data=None):
            if not authorized:
                self.denials.append(((data or {}).get("obj", {}), (data or {}).get("action")))

        def flush_denials():
            self.denials.extend((obj, action)
                                for _claims, obj, action in asset_access._PENDING_DENIALS)
            asset_access._PENDING_DENIALS.clear()

        for module in HANDLERS:
            patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in),
                        patch.object(module, "request_to_claims", claims_for(USER_ID))]
            if hasattr(module, "flush_denials"):
                patches.append(patch.object(module, "flush_denials", flush_denials))
        patches += [
            patch.object(authz_module, "log_authorization", side_effect=log_authorization),
            patch.object(cascade_svc, "lambda_client", self.lambda_client),
            patch.object(cascade_svc, "cascade_executor_function_name", "executor"),
            patch.object(evaluate_svc, "automatic_evaluation_target", return_value="live"),
            patch.object(evaluate_svc, "run_evaluation_for_asset",
                         side_effect=lambda database_id, asset_id, *_: {
                             "evaluationId": f"e-{asset_id}", "verdict": "compliant"}),
        ]
        self._patches = patches
        for active in patches:
            active.start()
        _seed(self.doubles)
        asset_access._PENDING_DENIALS.clear()
        return self

    def __exit__(self, *exc):
        for active in reversed(self._patches):
            active.stop()
        self._mock.stop()
        asset_access._PENDING_DENIALS.clear()
        authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = self._previous

    def call(self, module, method, path, params=None, query=None, body=None):
        self.denials.clear()
        response = module.lambda_handler(rest_event(method, path, params, query, body), MagicMock())
        assert response["statusCode"] < 500, response
        return response

    def denied_databases(self):
        """The database ids the request's denial records name."""
        return {obj.get("databaseId") for obj, _action in self.denials}


COMPLIANCE_ADMIN = ("compliance-admin.json", "denials-cadmin")
COMPLIANCE_READONLY = ("compliance-readonly.json", "denials-cro")


def _denied_ids(denials, object_type, action):
    """The database ids of the denial records on `object_type` / `action`, one entry per record."""
    return [obj.get("databaseId") for obj, denied_action in denials
            if obj.get("object__type") == object_type and denied_action == action]


@pytest.mark.unit
class TestACascadeStartRecordsADenialPerDatabase:

    def test_approving_a_cascade_records_a_denial_for_each_database_the_approver_may_not_evaluate(
            self):
        with _World(*COMPLIANCE_ADMIN) as world:
            response = world.call(cascade_svc, "POST", f"/compliance/cascades/{CASCADE_OWN}/approve",
                                  {"cascadeId": CASCADE_OWN}, body={})
            row = world.doubles["complianceCascadeStorage"].get_item(
                Key={"cascadeId": CASCADE_OWN})["Item"]
            denials = list(world.denials)
        assert response["statusCode"] == 202
        assert json.loads(row["authorizedDatabaseIds"]) == [OWN]
        denied = _denied_ids(denials, "complianceEvaluation", "POST")
        assert sorted(database_id for database_id in denied if database_id in OTHERS) == OTHERS
        assert len(denied) == len(set(denied)), "one denial record per database"
        assert OWN not in denied

    def test_a_cascade_started_at_once_records_a_denial_for_each_database_out_of_reach(self):
        with _World(*COMPLIANCE_ADMIN) as world:
            response = world.call(cascade_svc, "POST", "/compliance/cascades", body={
                "databaseId": OWN, "assetId": PARENT, "requireApproval": False})
            cascade_id = body_of(response)["cascadeId"]
            row = world.doubles["complianceCascadeStorage"].get_item(
                Key={"cascadeId": cascade_id})["Item"]
            denials = list(world.denials)
        assert response["statusCode"] == 202
        assert json.loads(row["authorizedDatabaseIds"]) == [OWN]
        denied = _denied_ids(denials, "complianceEvaluation", "POST")
        assert sorted(database_id for database_id in denied if database_id in OTHERS) == OTHERS

    def test_approving_another_databases_cascade_is_refused_and_recorded(self):
        with _World(*COMPLIANCE_ADMIN) as world:
            response = world.call(cascade_svc, "POST",
                                  f"/compliance/cascades/{CASCADE_OTHER}/approve",
                                  {"cascadeId": CASCADE_OTHER}, body={})
            named = world.denied_databases()
            row = world.doubles["complianceCascadeStorage"].get_item(
                Key={"cascadeId": CASCADE_OTHER})["Item"]
        assert response["statusCode"] == 403
        assert row["state"] == "pending_approval"
        assert named == {OTHERS[0]}


@pytest.mark.unit
class TestASweepRecordsADenialPerDatabase:

    def test_a_global_schema_sweep_records_a_denial_for_each_bound_database_it_skips(self):
        with _World(*COMPLIANCE_ADMIN) as world:
            response = world.call(evaluate_svc, "POST", f"/compliance/sweep/{SCHEMA}",
                                  {"schemaName": SCHEMA}, body={})
            denials = list(world.denials)
        body = body_of(response)
        assert response["statusCode"] == 200
        assert sorted((a["databaseId"], a["assetId"]) for a in body["assetsTriggered"]) == [
            (OWN, CHILD), (OWN, PARENT)]
        assert body["skippedNotAuthorized"] == 5
        denied = _denied_ids(denials, "complianceEvaluation", "POST")
        assert sorted(database_id for database_id in denied if database_id in OTHERS) == OTHERS
        assert OWN not in denied


@pytest.mark.unit
class TestTheListingsAcrossDatabasesRecordADenialPerDatabase:

    def test_the_audit_trail_across_databases_records_a_denial_for_each_other_database(self):
        with _World(*COMPLIANCE_READONLY) as world:
            response = world.call(audit_svc, "GET", "/compliance/audit")
            denials = list(world.denials)
        assert response["statusCode"] == 200
        assert [entry["databaseId"] for entry in body_of(response)["entries"]] == [OWN, OWN]
        denied = set(_denied_ids(denials, "complianceEvaluation", "GET"))
        assert set(OTHERS) <= denied
        assert OWN not in denied

    def test_the_audit_trail_of_another_database_is_refused_and_recorded(self):
        with _World(*COMPLIANCE_READONLY) as world:
            response = world.call(audit_svc, "GET", "/compliance/audit",
                                  query={"databaseId": OTHERS[1]})
            named = world.denied_databases()
        assert response["statusCode"] == 200
        assert body_of(response)["entries"] == []
        assert named == {OTHERS[1]}

    def test_the_schema_listings_create_scopes_record_a_denial_for_each_other_database(self):
        with _World(*COMPLIANCE_ADMIN) as world:
            response = world.call(schema_svc, "GET", "/compliance/schemas")
            denials = list(world.denials)
        assert response["statusCode"] == 200
        assert body_of(response)["createScopes"] == [OWN]
        denied = _denied_ids(denials, "complianceSchema", "POST")
        assert sorted(database_id for database_id in denied if database_id in OTHERS) == OTHERS
