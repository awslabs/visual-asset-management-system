# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A deleted database's compliance data moves under `<databaseId>#deleted` (D115).

The database service's real `delete_database` and `create_database`, the compliance services and
the trigger run against moto tables built from the CDK definitions (`_cdkTables`). Covered: a
re-created database starts with none of the deleted one's compliance data; its cascades awaiting
approval and its waiting pipeline rules are closed by the delete; the history is readable under
`<databaseId>#deleted` before and after the re-create, by an admin and by the shipped global
Compliance Admin template; reads and actions on the live id wait while the move runs, and a listing
across databases shows the moving rows under `<databaseId>#deleted`; a second delete merges into
`<databaseId>#deleted`; a large audit history moves in pages and survives an interruption; the
compliance job Lambda runs and continues the move from the job queue, and stops a move past its run
limit (its message kept in the job dead-letter queue, resumed when it is sent again); a move
message found on the trigger's queue is logged and dropped, and nothing is moved or sent for it.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    USER, body_of, claims_for, real_constants, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceUnknownDatabase import _stand_in
from backend.tests.handlers.compliance.test_complianceTemplateRolesEndToEnd import _real
from backend.tests.handlers.auth.test_permission_templates_compliance_binding import (  # noqa: F401
    USER_ID, roles_enforcer,
)
from common.compliance import databaseRetirement as retirement
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceJobs as jobs
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc
from handlers.compliance import complianceSchemaService as schema_svc
from handlers.compliance import complianceTrigger as trigger
from backend.backend.handlers.databases import createDatabase
from backend.backend.handlers.databases import databaseService

# The conftest reads the retirement record from these tests' own tables.
USES_RETIREMENT_RECORD = True

SERVICES = (store, evaluate_svc, quarantine_svc, cascade_svc, audit_svc, schema_svc, binding_svc,
            trigger, jobs)
READERS = (evaluate_svc, quarantine_svc, cascade_svc, audit_svc, schema_svc, binding_svc)
DB = "retired-db"
DELETED = f"{DB}#deleted"
BUCKET = "0b1c0000-0000-4000-8000-000000000b01"
PENDING_CASCADE = "7a0c0000-0000-4000-8000-0000000000a1"
DONE_CASCADE = "7a0c0000-0000-4000-8000-0000000000a2"
OTHER_DB = "other-db"


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


class World:
    """Moto tables, the services patched onto them, and a delete / re-create / read driver."""

    def __init__(self, enforcer):
        self.enforcer = enforcer
        self.queued = []

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(resource, SERVICES)
        for name in ("asset", "workflow", "pipeline", "buckets"):
            key = "bucketId" if name == "buckets" else "databaseId"
            schema = [{"AttributeName": key, "KeyType": "HASH"}]
            definitions = [{"AttributeName": key, "AttributeType": "S"}]
            if name == "asset":
                schema.append({"AttributeName": "assetId", "KeyType": "RANGE"})
                definitions.append({"AttributeName": "assetId", "AttributeType": "S"})
            resource.create_table(TableName=f"retire-{name}", KeySchema=schema,
                                  AttributeDefinitions=definitions, BillingMode="PAY_PER_REQUEST")
        resource.Table("retire-buckets").put_item(Item={"bucketId": BUCKET, "bucketName": "b"})
        self.tables = retirement.Tables(
            schema=self.t["complianceSchemaStorage"], asset_state=self.t["complianceAssetStateStorage"],
            evaluation=self.t["complianceEvaluationStorage"], cascade=self.t["complianceCascadeStorage"],
            audit=self.t["complianceAuditStorage"])
        factory = self.enforcer if callable(self.enforcer) and not isinstance(
            self.enforcer, MagicMock) else (lambda _claims: self.enforcer)
        for module in READERS:
            self.patches += [patch.object(module, "CasbinEnforcer", side_effect=factory),
                             patch.object(module, "request_to_claims", claims_for(USER))]
        database_name = self.t["databaseStorage"].name
        self.patches += [
            patch.object(createDatabase, "dynamodb", resource),
            patch.object(createDatabase, "db_database", database_name),
            patch.object(createDatabase, "s3_asset_buckets_table", "retire-buckets"),
            patch.object(databaseService, "dynamodb", resource),
            patch.object(databaseService, "db_database", database_name),
            patch.object(databaseService, "_compliance_tables", self.tables, create=True),
            patch.object(databaseService, "CasbinEnforcer", return_value=_stand_in({"*"})),
            patch.object(databaseService, "check_workflows", return_value=False),
            patch.object(databaseService, "check_pipelines", return_value=False),
            patch.object(databaseService, "check_assets", return_value=False),
            patch.object(databaseService, "hand_off_compliance_move", create=True,
                         side_effect=lambda database_id: self.queued.append(database_id) or True),
            patch.object(trigger, "_RETIREMENT_TABLES", self.tables, create=True),
            # The approval catches the conditional failure of its own resource's client.
            patch.object(cascade_svc, "dynamodb", resource),
        ]
        started = []
        try:
            for active in self.patches:
                active.start()
                started.append(active)
        except Exception:
            for active in reversed(started):
                active.stop()
            self._mock.stop()
            raise
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    # --- data ---

    def seed(self, database_id=DB, asset_prefix="", generation_note="first"):
        t = self.t
        t["databaseStorage"].put_item(Item={
            "databaseId": database_id, "defaultBucketId": BUCKET, "description": generation_note,
            "complianceSchemaName": "retired-schema", "complianceAutoEval": True})
        t["complianceSchemaStorage"].put_item(Item=schema_row(
            schema_name=f"{asset_prefix}retired-schema", database_id=database_id))
        for asset_id, state in ((f"{asset_prefix}quarantined", "quarantined"),
                                (f"{asset_prefix}archived", "archived")):
            t["complianceAssetStateStorage"].put_item(Item={
                "databaseId": database_id, "assetId": asset_id, "complianceState": state,
                "schemaName": "retired-schema", "schemaSource": "database",
                "assetNameSort": asset_id, "assetName": asset_id, "note": generation_note,
                "lastEvaluationId": f"ev-{asset_id}", "lastEvaluatedAt": "2026-10-01T00:00:00+00:00",
                "lastEvaluationStatus": "completed"})
            t["complianceEvaluationStorage"].put_item(Item={
                "evaluationId": f"ev-{asset_id}", "databaseId:assetId": f"{database_id}:{asset_id}",
                "databaseId": database_id, "assetId": asset_id, "schemaName": "retired-schema",
                "evaluatedAt": "2026-10-01T00:00:00+00:00", "status": "completed",
                "verdict": state if state != "archived" else "compliant"})
            t["complianceAuditStorage"].put_item(Item=build_audit_item(
                database_id, asset_id, "compliance_check", "SYSTEM_USER", {},
                timestamp="2026-10-01T00:00:00+00:00", asset_name=asset_id))
        # An evaluation whose pipeline rule waits for a running execution of its workflow.
        waiting_asset = f"{asset_prefix}quarantined"
        t["complianceEvaluationStorage"].put_item(Item={
            "evaluationId": f"ev-wait-{asset_prefix}", "databaseId:assetId": f"{database_id}:{waiting_asset}",
            "databaseId": database_id, "assetId": waiting_asset, "schemaName": "retired-schema",
            "evaluatedAt": "2026-10-02T00:00:00+00:00", "status": "pending_pipeline",
            "verdict": "pending_pipeline", "hasRuleErrors": False,
            "ruleResults": json.dumps([{"ruleName": "named", "ruleType": "assetAttributes",
                                        "enforcement": "warn", "passed": True,
                                        "status": "evaluated"}]),
            "pipelineExecutions": [{"ruleName": "convert", "status": "waiting"}]})
        t["complianceEvaluationStorage"].put_item(Item={
            "evaluationId": f"ev-wait-{asset_prefix}#convert", "recordType": "pipelineExecution",
            "parentEvaluationId": f"ev-wait-{asset_prefix}", "pipelineRuleName": "convert",
            "status": "waiting", "waitingWorkflowKey": "wfdb:wf1",
            "waitingSince": "2026-10-02T00:00:00+00:00",
            "waitingAssetKey": f"{database_id}:{waiting_asset}", "launchRequest": "{}"})
        if not asset_prefix:
            for cascade_id, state in ((PENDING_CASCADE, "pending_approval"),
                                      (DONE_CASCADE, "completed")):
                t["complianceCascadeStorage"].put_item(Item={
                    "cascadeId": cascade_id, "state": state,
                    "createdAt": "2026-10-01T00:00:00+00:00", "triggeredByDatabaseId": database_id,
                    "triggeredByAssetId": "quarantined", "triggeredByAssetName": "quarantined",
                    "approvalTimeoutAt": "2099-01-01T00:00:00+00:00", "requireApproval": True,
                    "nodes": "{}", "executionOrder": "[]"})
            t["complianceCascadeStorage"].put_item(Item={
                "cascadeId": f"pendingCascadeMarker:{database_id}:quarantined",
                "markerFor": PENDING_CASCADE, "triggeredByDatabaseId": database_id,
                "triggeredByAssetId": "quarantined"})
        # Another database's data never moves.
        t["complianceAuditStorage"].put_item(Item=build_audit_item(
            OTHER_DB, "kept", "compliance_check", "SYSTEM_USER", {},
            timestamp="2026-10-01T00:00:00+00:00"))

    # --- actions ---

    def delete(self, database_id=DB, run=True):
        result = databaseService.delete_database(database_id, {"tokens": [USER], "roles": []})
        assert result.statusCode == 200, result
        if run:
            assert self.move(database_id)
        return result

    def move(self, database_id=DB, deadline=float("inf"), **kwargs):
        return retirement.run(self.tables, database_id, deadline, **kwargs)

    def recreate(self, database_id=DB):
        createDatabase.create_database(createDatabase.CreateDatabaseRequestModel(
            databaseId=database_id, description="re-created", defaultBucketId=BUCKET))

    def get(self, service, path, path_params=None, query=None):
        response = service.lambda_handler(rest_event("GET", path, path_params, query), MagicMock())
        return response["statusCode"], body_of(response)

    def reads(self, database_id):
        """What the history reads of one database id answer: `{read: (status, ids listed)}`."""
        quoted = database_id.replace("#", "%23")
        out = {}
        status, body = self.get(evaluate_svc, f"/compliance/state/{quoted}", {"databaseId": quoted})
        out["overview"] = (status, sorted(a["assetId"] for a in body.get("assets", [])),
                           body.get("totalAssets"))
        for state in ("pending_approval", "completed", "aborted"):
            status, body = self.get(cascade_svc, "/compliance/cascades", None,
                                    {"databaseId": database_id, "state": state})
            out[f"cascades:{state}"] = (status, sorted(c["cascadeId"] for c in body.get("cascades", [])))
        status, body = self.get(audit_svc, "/compliance/audit", None, {"databaseId": database_id})
        out["audit"] = (status, sorted(e["eventType"] + ":" + e["assetId"]
                                       for e in body.get("entries", [])))
        status, body = self.get(audit_svc, f"/compliance/audit/{quoted}/quarantined",
                                {"databaseId": quoted, "assetId": "quarantined"})
        out["asset audit"] = (status, len(body.get("entries", [])))
        status, body = self.get(evaluate_svc, f"/compliance/evaluations/{quoted}/quarantined",
                                {"databaseId": quoted, "assetId": "quarantined"})
        out["evaluations"] = (status, sorted(e["evaluationId"] for e in body.get("evaluations", [])))
        status, body = self.get(evaluate_svc, f"/compliance/state/{quoted}/quarantined",
                                {"databaseId": quoted, "assetId": "quarantined"})
        out["asset state"] = (status, body.get("complianceState"))
        status, body = self.get(schema_svc, "/compliance/schemas", None, {"databaseId": database_id})
        out["schemas"] = (status, sorted(s["schemaName"] for s in body.get("schemas", [])))
        status, body = self.get(quarantine_svc, "/compliance/quarantine", None)
        out["quarantine (all databases)"] = (status, sorted(
            (q["databaseId"], q["assetId"]) for q in body.get("quarantinedAssets", [])))
        return out

    def rows_under(self, database_id):
        t = self.t
        return {
            "assetState": [r["assetId"] for r in t["complianceAssetStateStorage"].scan()["Items"]
                           if r["databaseId"] == database_id],
            "evaluations": sorted(r["evaluationId"] for r in t["complianceEvaluationStorage"].scan()["Items"]
                                  if r.get("databaseId") == database_id
                                  and r.get("recordType") is None),
            "audit": sorted(r["eventType"] for r in t["complianceAuditStorage"].scan()["Items"]
                            if r["databaseId"] == database_id),
            "cascades": sorted(r["cascadeId"] for r in t["complianceCascadeStorage"].scan()["Items"]
                               if r.get("triggeredByDatabaseId") == database_id),
            "schemas": sorted(r["schemaName"] for r in t["complianceSchemaStorage"].scan()["Items"]
                              if r["databaseId"] == database_id),
        }


EMPTY_LIVE = {
    "overview": (200, [], 0),
    "cascades:pending_approval": (200, []), "cascades:completed": (200, []),
    "cascades:aborted": (200, []),
    "audit": (200, []), "asset audit": (200, 0), "evaluations": (200, []),
    "asset state": (200, "unknown"), "schemas": (200, []),
    "quarantine (all databases)": (200, []),
}


def _history(reads):
    return {name: reads[name] for name in ("cascades:aborted", "cascades:completed", "audit",
                                           "asset audit", "evaluations", "asset state", "schemas")}


EXPECTED_HISTORY = {
    "cascades:aborted": (200, [PENDING_CASCADE]),
    "cascades:completed": (200, [DONE_CASCADE]),
    "audit": (200, ["cascade_database_deleted:quarantined", "compliance_check:archived",
                    "compliance_check:quarantined", "database_compliance_moved:*",
                    "database_deleted:*"]),
    "asset audit": (200, 2),
    "evaluations": (200, ["ev-quarantined", "ev-wait-"]),
    "asset state": (200, "quarantined"),
    "schemas": (200, ["retired-schema"]),
}


@pytest.mark.unit
class TestARecreatedDatabaseStartsEmpty:

    def test_the_new_database_shows_none_of_the_deleted_ones_compliance_data(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete()
            world.recreate()
            assert world.reads(DB) == EMPTY_LIVE
            assert world.rows_under(DB) == {"assetState": [], "evaluations": [], "audit": [],
                                            "cascades": [], "schemas": []}
            # Another database's entries did not move.
            assert world.rows_under(OTHER_DB)["audit"] == ["compliance_check"]

    def test_everything_moved_under_the_deleted_id(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete()
            moved = world.rows_under(DELETED)
            assert sorted(moved["assetState"]) == ["archived", "quarantined"]
            assert moved["evaluations"] == ["ev-archived", "ev-quarantined", "ev-wait-"]
            assert moved["cascades"] == sorted([DONE_CASCADE, PENDING_CASCADE])
            assert moved["schemas"] == ["retired-schema"]
            record = retirement.get_record(world.t["complianceEvaluationStorage"], DB)
            assert record["state"] == "done" and record["generation"] == 1
            assert record["moved"] == {"evaluations": 3, "cascades": 2, "assetState": 2,
                                       "schemas": 1, "audit": 3}
            # The pending-cascade marker was a claim, not history: it is gone.
            assert not world.t["complianceCascadeStorage"].get_item(
                Key={"cascadeId": f"pendingCascadeMarker:{DB}:quarantined"}).get("Item")


@pytest.mark.unit
class TestTheDeleteClosesWhatCouldStillAct:

    def test_a_pending_cascade_is_aborted_with_its_reason_and_cannot_be_approved(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete()
            cascade = world.t["complianceCascadeStorage"].get_item(
                Key={"cascadeId": PENDING_CASCADE})["Item"]
            assert cascade["state"] == "aborted"
            assert cascade["abortReason"] == "The database was deleted"
            audit = [r for r in world.t["complianceAuditStorage"].scan()["Items"]
                     if r["eventType"] == "cascade_database_deleted"]
            assert [(r["cascadeId"], r["databaseId"]) for r in audit] == [(PENDING_CASCADE, DELETED)]
            response = cascade_svc.lambda_handler(rest_event(
                "POST", f"/compliance/cascades/{PENDING_CASCADE}/approve",
                {"cascadeId": PENDING_CASCADE}, body={}), MagicMock())
            assert response["statusCode"] == 400
            assert world.t["complianceCascadeStorage"].get_item(
                Key={"cascadeId": PENDING_CASCADE})["Item"]["state"] == "aborted"

    def test_a_waiting_pipeline_rule_is_dropped(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete()
            row = world.t["complianceEvaluationStorage"].get_item(
                Key={"evaluationId": "ev-wait-#convert"})["Item"]
            assert row["status"] == "completed"
            assert "waitingWorkflowKey" not in row and "waitingSince" not in row
            assert json.loads(row["ruleResults"])[0]["message"] == "Not started: the database was deleted"
            parent = world.t["complianceEvaluationStorage"].get_item(
                Key={"evaluationId": "ev-wait-"})["Item"]
            assert parent["status"] == "error"
            assert parent["errorMessage"] == "Not completed: the database was deleted"
            # The end state a superseded close writes: no pending verdict, every entry completed,
            # the rule's reason among the results (D132).
            assert (parent["verdict"], parent["closedReason"]) == ("error", "databaseDeleted")
            assert parent["pipelineExecutions"] == [{"ruleName": "convert", "status": "completed"}]
            assert parent["violations"] == []
            results = {r["ruleName"]: r for r in json.loads(parent["ruleResults"])}
            assert results["convert"]["message"] == "Not started: the database was deleted"
            assert results["convert"]["status"] == "error"
            assert results["named"]["passed"] is True
            assert world.t["complianceEvaluationStorage"].scan(
                IndexName="PipelineRuleWaitIndex")["Items"] == []
            deleted_entry = next(r for r in world.t["complianceAuditStorage"].scan()["Items"]
                                 if r["eventType"] == "database_deleted")
            assert json.loads(deleted_entry["details"])["pendingCascadesClosed"] == 1
            assert json.loads(deleted_entry["details"])["waitingPipelineRulesClosed"] == 1

    def test_a_failed_start_fails_the_delete_and_leaves_the_database_live(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            with patch.object(retirement, "close_waiting_rules", side_effect=RuntimeError("boom")):
                with pytest.raises(Exception):
                    databaseService.delete_database(DB, {"tokens": [USER], "roles": []})
            assert world.t["databaseStorage"].get_item(Key={"databaseId": DB}).get("Item")
            assert retirement.get_record(world.t["complianceEvaluationStorage"], DB) is None
            assert world.queued == []


@pytest.mark.unit
class TestTheHistoryStaysReadable:

    @pytest.mark.parametrize("recreated", [False, True], ids=["deleted", "re-created"])
    def test_an_admin_reads_it_under_the_deleted_id(self, recreated):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete()
            if recreated:
                world.recreate()
                # #861: the re-create removed the deleted database's row.
                assert not world.t["databaseStorage"].get_item(
                    Key={"databaseId": DELETED}).get("Item")
            assert _history(world.reads(DELETED)) == EXPECTED_HISTORY

    @pytest.mark.parametrize("recreated", [False, True], ids=["deleted", "re-created"])
    def test_the_global_compliance_admin_template_reads_it(self, roles_enforcer, recreated):
        enforcer = roles_enforcer({"cga": ("compliance-global-admin.json", "")})
        with World(_real(enforcer)) as world:
            world.seed()
            world.delete()
            if recreated:
                world.recreate()
            assert _history(world.reads(DELETED)) == EXPECTED_HISTORY

    def test_a_database_scoped_compliance_admin_of_the_live_id_does_not(self, roles_enforcer):
        enforcer = roles_enforcer({"ca": ("compliance-admin.json", DB)})
        with World(_real(enforcer)) as world:
            world.seed()
            world.delete()
            world.recreate()
            reads = world.reads(DELETED)
            assert reads["audit"] == (200, [])
            assert reads["evaluations"][0] == 403
            assert reads["cascades:completed"] == (200, [])

    def test_an_id_with_no_history_is_not_found(self):
        with World(_stand_in({"*"})) as world:
            status, _ = world.get(audit_svc, "/compliance/audit", None,
                                  {"databaseId": "never-existed#deleted"})
            assert status == 400

    def test_writes_on_the_deleted_id_are_refused(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete()
            response = schema_svc.lambda_handler(rest_event(
                "PUT", "/compliance/schemas/retired-schema", {"schemaName": "retired-schema"},
                body={"description": "edited"}), MagicMock())
            assert response["statusCode"] == 400
            assert "deleted database" in body_of(response)["message"]
            response = schema_svc.lambda_handler(rest_event(
                "DELETE", "/compliance/schemas/retired-schema", {"schemaName": "retired-schema"}),
                MagicMock())
            assert response["statusCode"] == 400
            status, body = world.get(schema_svc, "/compliance/schemas", None, {"databaseId": DELETED})
            assert body["schemas"][0]["permissions"] == {"edit": False, "delete": False, "bind": False}


NEVER_EXISTED = "never-existed-asset"


def _archive_rows(world, archived_at="2026-10-01T00:00:00.000001"):
    """The deleted database's archived asset rows, as the asset service leaves them: a database
    with live assets cannot be deleted, so its assets were archived first (`archivedAt` naive UTC,
    as `assetService` writes it). `None` writes a row from an earlier release, without the time."""
    for asset_id in ("quarantined", "archived"):
        item = {"databaseId": DELETED, "assetId": asset_id, "assetName": asset_id,
                "status": "archived"}
        if archived_at is not None:
            item["archivedAt"] = archived_at
        world.t["assetStorage"].put_item(Item=item)


def _asset_reads(world, database_id, asset_id):
    """What the per-asset compliance reads answer for one asset id: `{read: (status, body)}`, the
    asset id in the bodies written as `<asset>` so two ids compare."""
    quoted = database_id.replace("#", "%23")
    params = {"databaseId": quoted, "assetId": asset_id}
    out = {}
    for name, service, path in (
            ("state", evaluate_svc, f"/compliance/state/{quoted}/{asset_id}"),
            ("evaluations", evaluate_svc, f"/compliance/evaluations/{quoted}/{asset_id}"),
            ("asset audit", audit_svc, f"/compliance/audit/{quoted}/{asset_id}")):
        out[name] = world.get(service, path, params)
    out["audit listing"] = world.get(audit_svc, "/compliance/audit", None,
                                     {"databaseId": database_id, "assetId": asset_id})
    out["quarantine"] = world.get(quarantine_svc, "/compliance/quarantine", None,
                                  {"databaseId": database_id, "text": asset_id})
    return {name: (status, _without_asset_id(body, asset_id))
            for name, (status, body) in out.items()}


def _without_asset_id(value, asset_id):
    """`value` with each `assetId` equal to `asset_id` written as `<asset>`."""
    if isinstance(value, dict):
        return {key: ("<asset>" if key == "assetId" and item == asset_id
                      else _without_asset_id(item, asset_id)) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_asset_id(item, asset_id) for item in value]
    return value


@pytest.mark.unit
class TestAnAssetIdOfTheDeletedDatabase:
    """The live id of a re-created database answered `GET /compliance/state/<id>/<old asset id>`
    with 200 `archived` (no evaluations, no audit), confirming the id existed in the deleted
    database: its archived row stays in `<databaseId>#deleted` (D136). Every per-asset read of the
    live id now answers for such an id exactly as for an id the database never had, for admin and
    the global and database-scoped Compliance Admin templates; the history stays readable under
    `<databaseId>#deleted`, and an asset archived in the re-created database still reads archived."""

    ROLES = {
        "admin": None,
        "global compliance admin": {"cga": ("compliance-global-admin.json", "")},
        "database compliance admin": {"ca": ("compliance-admin.json", DB)},
    }

    def _world(self, role, roles_enforcer):
        roles = self.ROLES[role]
        return World(_stand_in({"*"}) if roles is None else _real(roles_enforcer(roles)))

    @pytest.mark.parametrize("archived_at", ["2026-10-01T00:00:00.000001", None],
                             ids=["archived-at", "earlier-release-row"])
    @pytest.mark.parametrize("role", list(ROLES))
    @pytest.mark.parametrize("asset_id", ["quarantined", "archived"])
    def test_the_live_id_answers_as_for_an_id_it_never_had(self, roles_enforcer, role, asset_id,
                                                           archived_at):
        with self._world(role, roles_enforcer) as world:
            world.seed()
            _archive_rows(world, archived_at)
            world.delete()
            world.recreate()
            old = _asset_reads(world, DB, asset_id)
            assert old == _asset_reads(world, DB, NEVER_EXISTED)
            if role != "database compliance admin":
                assert old["state"] == (200, {
                    "databaseId": DB, "assetId": "<asset>", "complianceState": "unknown",
                    "schemaName": None, "schemaSource": None})
                assert old["evaluations"] == (200, {"evaluations": []})

    @pytest.mark.parametrize("role", ["admin", "global compliance admin"])
    def test_the_history_stays_readable_under_the_deleted_id(self, roles_enforcer, role):
        with self._world(role, roles_enforcer) as world:
            world.seed()
            _archive_rows(world)
            world.delete()
            world.recreate()
            reads = _asset_reads(world, DELETED, "quarantined")
            assert reads["state"][0] == 200
            assert reads["state"][1]["complianceState"] == "quarantined"
            assert sorted(e["evaluationId"] for e in reads["evaluations"][1]["evaluations"]) == [
                "ev-quarantined", "ev-wait-"]
            assert len(reads["asset audit"][1]["entries"]) == 2

    def test_an_asset_archived_in_the_recreated_database_still_reads_archived(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            _archive_rows(world)
            world.delete()
            world.recreate()
            # Archived after the delete: the re-created database's asset.
            world.t["assetStorage"].put_item(Item={
                "databaseId": DELETED, "assetId": "archived-later", "assetName": "archived-later",
                "status": "archived",
                "archivedAt": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()})
            world.t["complianceAssetStateStorage"].put_item(Item={
                "databaseId": DB, "assetId": "archived-later", "complianceState": "compliant",
                "schemaName": "retired-schema", "schemaSource": "database"})
            status, body = world.get(evaluate_svc, f"/compliance/state/{DB}/archived-later",
                                     {"databaseId": DB, "assetId": "archived-later"})
            assert status == 200
            assert (body["complianceState"], body["recordedComplianceState"]) == (
                "archived", "compliant")

    def test_a_live_asset_that_reuses_an_old_id_is_not_read_as_archived(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            _archive_rows(world)
            world.delete()
            world.recreate()
            # The re-created database has a live asset with the id of a deleted database's asset.
            world.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": "quarantined",
                                                   "assetName": "quarantined"})
            world.t["complianceAssetStateStorage"].put_item(Item={
                "databaseId": DB, "assetId": "quarantined", "complianceState": "compliant",
                "schemaName": "retired-schema", "schemaSource": "database",
                "assetName": "quarantined", "assetNameSort": "quarantined"})
            status, body = world.get(evaluate_svc, f"/compliance/state/{DB}/quarantined",
                                     {"databaseId": DB, "assetId": "quarantined"})
            assert (status, body["complianceState"]) == (200, "compliant")
            assert "recordedComplianceState" not in body
            status, body = world.get(evaluate_svc, f"/compliance/state/{DB}", {"databaseId": DB})
            assert status == 200
            assert [a["assetId"] for a in body["assets"]] == ["quarantined"]


@pytest.mark.unit
class TestWhileTheMoveRuns:

    def _half_moved(self, world):
        world.seed()
        world.delete(run=False)
        assert world.queued == [DB]
        # One page of evaluations, then the deadline.
        clock = iter([0, 10]).__next__
        assert world.move(deadline=5, clock=clock, page_size=1) is False

    def test_the_live_id_shows_nothing_and_its_actions_wait(self):
        with World(_stand_in({"*"})) as world:
            self._half_moved(world)
            world.recreate()
            assert world.rows_under(DB)["audit"]  # still under the live id
            reads = world.reads(DB)
            assert reads == EMPTY_LIVE
            bind = binding_svc.lambda_handler(rest_event(
                "PUT", f"/compliance/bind/{DB}", {"databaseId": DB},
                body={"schemaName": "retired-schema"}), MagicMock())
            assert bind["statusCode"] == 409
            assert body_of(bind)["message"] == retirement.MOVING_MESSAGE
            result = store.run_evaluation(DB, "quarantined", "retired-schema", USER)
            assert result["databaseMoving"] is True and result["superseded"] is True

    def test_a_listing_across_databases_shows_the_moving_rows_under_the_deleted_id(self):
        with World(_stand_in({"*"})) as world:
            self._half_moved(world)
            status, body = world.get(audit_svc, "/compliance/audit")
            assert status == 200
            databases = {(e["databaseId"], e["eventType"]) for e in body["entries"]}
            assert (DB, "compliance_check") not in databases
            assert (DELETED, "compliance_check") in databases
            assert (OTHER_DB, "compliance_check") in databases

    def test_the_move_finishes_and_the_fence_lifts(self):
        with World(_stand_in({"*"})) as world:
            self._half_moved(world)
            world.recreate()
            assert world.move()
            assert retirement.move_in_progress(world.t["complianceEvaluationStorage"], DB) is False
            assert world.reads(DB) == EMPTY_LIVE
            assert _history(world.reads(DELETED)) == EXPECTED_HISTORY


@pytest.mark.unit
class TestASecondDelete:

    def test_it_merges_into_the_deleted_id(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete()
            world.recreate()
            world.seed(asset_prefix="g2-", generation_note="second")
            # The same asset id in both databases: the later database's record is kept.
            world.t["complianceAssetStateStorage"].put_item(Item={
                "databaseId": DB, "assetId": "quarantined", "complianceState": "compliant",
                "note": "second"})
            world.delete()
            record = retirement.get_record(world.t["complianceEvaluationStorage"], DB)
            assert record["generation"] == 2 and record["state"] == "done"
            moved = world.rows_under(DELETED)
            assert {"ev-quarantined", "ev-g2-quarantined", "ev-wait-", "ev-wait-g2-"} <= set(
                moved["evaluations"])
            assert sorted(moved["schemas"]) == ["g2-retired-schema", "retired-schema"]
            state = world.t["complianceAssetStateStorage"].get_item(
                Key={"databaseId": DELETED, "assetId": "quarantined"})["Item"]
            assert state["note"] == "second"
            assert moved["audit"].count("database_deleted") == 2
            assert world.rows_under(DB) == {"assetState": [], "evaluations": [], "audit": [],
                                            "cascades": [], "schemas": []}


@pytest.mark.unit
class TestALargeAuditHistory:

    ENTRIES = 2500

    def _seed_audit(self, world):
        with world.t["complianceAuditStorage"].batch_writer() as batch:
            for index in range(self.ENTRIES):
                batch.put_item(Item=build_audit_item(
                    DB, f"asset-{index % 50}", "compliance_check", "SYSTEM_USER", {"i": index},
                    timestamp=f"2026-09-01T00:00:{index % 60:02d}.{index:06d}+00:00"))

    def test_it_moves_in_pages_and_survives_an_interruption(self):
        with World(_stand_in({"*"})) as world:
            world.t["databaseStorage"].put_item(Item={"databaseId": DB, "defaultBucketId": BUCKET})
            self._seed_audit(world)
            world.delete(run=False)
            # A run that stops at its deadline after a few pages ...
            ticks = iter(range(0, 1000)).__next__
            assert world.move(deadline=10, clock=ticks, page_size=100) is False
            record = retirement.get_record(world.t["complianceEvaluationStorage"], DB)
            assert record["state"] == "moving" and record["phase"] == "audit"
            partly = record["moved"]["audit"]
            assert 0 < partly < self.ENTRIES
            # ... one that dies in the middle of a page ...
            real_move = retirement._move_audit
            calls = {"n": 0}

            def dying(tables, database_id, item):
                calls["n"] += 1
                if calls["n"] == 150:
                    raise RuntimeError("Lambda timed out")
                return real_move(tables, database_id, item)

            with patch.object(retirement, "_move_audit", side_effect=dying):
                with pytest.raises(RuntimeError):
                    world.move(page_size=100)
            # ... and the next run finishes from the last saved page, moving no entry twice.
            assert world.move(page_size=100) is True
            assert world.rows_under(DB)["audit"] == []
            moved = [r for r in world.t["complianceAuditStorage"].scan()["Items"]
                     if r["databaseId"] == DELETED and r["eventType"] == "compliance_check"]
            assert len(moved) == self.ENTRIES
            assert all(r["databaseId:assetId"].startswith(f"{DELETED}:") for r in moved)
            record = retirement.get_record(world.t["complianceEvaluationStorage"], DB)
            assert record["state"] == "done"
            # Rows the dying run moved before its page was saved are not moved again.
            assert record["moved"]["audit"] <= self.ENTRIES
            status, body = world.get(audit_svc, "/compliance/audit", None,
                                     {"databaseId": DELETED, "maxItems": "200"})
            assert status == 200 and len(body["entries"]) == 200


JOB_QUEUE = "https://sqs.example/compliance-jobs"
JOB_DLQ = "https://sqs.example/compliance-jobs-dlq"


def _context(seconds=300):
    return MagicMock(get_remaining_time_in_millis=MagicMock(return_value=seconds * 1000))


@pytest.mark.unit
class TestTheJobLambdaRunsTheMove:

    def _event(self, database_id, sns=False):
        body = retirement.move_message(database_id)
        if sns:
            body = {"Type": "Notification", "Message": json.dumps(body)}
        return {"Records": [{"messageId": "m1", "body": json.dumps(body)}]}

    def test_a_queued_message_runs_the_move(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete(run=False)
            with patch.object(jobs, "sqs_client") as sqs:
                result = jobs.lambda_handler(self._event(DB), _context())
            assert result == {"batchItemFailures": []}
            sqs.send_message.assert_not_called()
            record = retirement.get_record(world.t["complianceEvaluationStorage"], DB)
            assert (record["state"], record["runs"]) == ("done", 1)
            assert record["runLimit"] >= retirement.RUN_LIMIT_ALLOWANCE

    def test_an_unfinished_run_queues_the_next_message_on_the_job_queue(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete(run=False)
            real_run = retirement.run

            def one_page(tables, database_id, deadline):
                return real_run(tables, database_id, 1, clock=iter(range(100)).__next__,
                                page_size=1)

            with patch.object(jobs, "sqs_client") as sqs, \
                    patch.object(jobs, "job_queue_url", JOB_QUEUE), \
                    patch.object(retirement, "run", side_effect=one_page):
                result = jobs.lambda_handler(self._event(DB), _context(1))
            assert result == {"batchItemFailures": []}
            sqs.send_message.assert_called_once()
            assert sqs.send_message.call_args.kwargs["QueueUrl"] == JOB_QUEUE
            assert json.loads(sqs.send_message.call_args.kwargs["MessageBody"]) == \
                retirement.move_message(DB)

    def test_a_failed_run_is_redelivered_not_requeued(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete(run=False)
            with patch.object(jobs, "sqs_client") as sqs, \
                    patch.object(retirement, "run", side_effect=RuntimeError("throttled")):
                result = jobs.lambda_handler(self._event(DB), _context())
            assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
            sqs.send_message.assert_not_called()

    def test_a_message_never_starts_a_move_and_an_sns_envelope_is_not_one(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            assert jobs.lambda_handler(self._event(DB), _context()) == {"batchItemFailures": []}
            assert jobs.lambda_handler(self._event(DB, sns=True), _context()) == {
                "batchItemFailures": []}
            assert world.rows_under(DB)["audit"] == ["compliance_check", "compliance_check"]
            assert retirement.get_record(world.t["complianceEvaluationStorage"], DB) is None


@pytest.mark.unit
class TestTheMoveRunLimit:
    """The job Lambda runs with Lambda's recursive-loop detection off, so the move counts its own
    runs against a limit derived from the size of the tables it reads."""

    _event = TestTheJobLambdaRunsTheMove._event

    def test_the_limit_is_derived_from_the_tables_item_counts(self):
        counts = {"evaluation": 250, "cascade": 0, "asset_state": 101, "schema": 3, "audit": 1000}
        tables = MagicMock()
        for name, count in counts.items():
            table = getattr(tables, name)
            table.name = name
            table.meta.client.describe_table.return_value = {"Table": {"ItemCount": count}}
        # Pages of 100: 3 + 1 + 2 + 1 + 10 = 17, twice over, plus the allowance.
        assert retirement.run_limit(tables) == 2 * 17 + retirement.RUN_LIMIT_ALLOWANCE
        # An unreadable count is one page.
        tables.audit.meta.client.describe_table.side_effect = RuntimeError("denied")
        assert retirement.run_limit(tables) == 2 * 8 + retirement.RUN_LIMIT_ALLOWANCE

    def test_a_run_past_the_limit_moves_nothing_and_its_message_is_kept_in_the_dlq(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete(run=False)
            evaluation = world.t["complianceEvaluationStorage"]
            evaluation.update_item(Key=retirement.record_key(DB),
                                   UpdateExpression="SET runs = :r, runLimit = :l",
                                   ExpressionAttributeValues={":r": 5, ":l": 5})
            before = world.rows_under(DB)
            with patch.object(jobs, "sqs_client") as sqs, \
                    patch.object(jobs, "job_queue_url", JOB_QUEUE), \
                    patch.object(jobs, "job_dlq_url", JOB_DLQ):
                result = jobs.lambda_handler(self._event(DB), _context())
            # The message is consumed here and kept in the dead-letter queue; nothing moved.
            assert result == {"batchItemFailures": []}
            assert [(call.kwargs["QueueUrl"], json.loads(call.kwargs["MessageBody"]))
                    for call in sqs.send_message.call_args_list] == [
                (JOB_DLQ, retirement.move_message(DB))]
            assert world.rows_under(DB) == before
            record = retirement.get_record(evaluation, DB)
            assert (record["state"], record["stopReason"], record["runs"]) == (
                "moving", retirement.STOPPED_RUN_LIMIT, 0)
            # The live id still shows none of the deleted database's data (the existing state of a
            # move that keeps failing) ...
            assert retirement.move_in_progress(evaluation, DB)
            # ... and the message sent again (a redrive) resumes it from its last saved page.
            with patch.object(jobs, "sqs_client"):
                assert jobs.lambda_handler(self._event(DB), _context()) == {
                    "batchItemFailures": []}
            assert retirement.get_record(evaluation, DB)["state"] == "done"

    def test_a_stopped_move_whose_message_cannot_be_kept_is_redelivered_and_keeps_its_count(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete(run=False)
            evaluation = world.t["complianceEvaluationStorage"]
            evaluation.update_item(Key=retirement.record_key(DB),
                                   UpdateExpression="SET runs = :r, runLimit = :l",
                                   ExpressionAttributeValues={":r": 5, ":l": 5})
            with patch.object(jobs, "sqs_client") as sqs, \
                    patch.object(jobs, "job_dlq_url", JOB_DLQ):
                sqs.send_message.side_effect = RuntimeError("down")
                result = jobs.lambda_handler(self._event(DB), _context())
            assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
            record = retirement.get_record(evaluation, DB)
            # Not restarted: its redeliveries stop again until the queue keeps it in its DLQ.
            assert (record["state"], record["stopReason"], record["runs"]) == (
                "moving", retirement.STOPPED_RUN_LIMIT, 6)

    def test_a_new_delete_starts_a_new_count(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete(run=False)
            evaluation = world.t["complianceEvaluationStorage"]
            evaluation.update_item(Key=retirement.record_key(DB),
                                   UpdateExpression="SET runs = :r, runLimit = :l",
                                   ExpressionAttributeValues={":r": 5, ":l": 5})
            record = retirement.begin(world.tables, DB, USER)
            assert "runs" not in record and "runLimit" not in record
            assert retirement.begin_run(world.tables, DB)[0] == retirement.RUN_GO


@pytest.mark.unit
class TestTheTriggerDropsALeftoverMoveMessage:

    def test_it_is_logged_and_dropped_and_nothing_is_moved_or_sent(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            world.delete(run=False)
            before = world.rows_under(DB)
            record = retirement.get_record(world.t["complianceEvaluationStorage"], DB)
            event = {"Records": [{"messageId": "m1",
                                  "body": json.dumps(retirement.move_message(DB))}]}
            with patch.object(trigger, "sqs_client") as sqs, \
                    patch.object(retirement, "run") as run, \
                    patch.object(trigger.logger, "warning") as warning:
                assert trigger.lambda_handler(event, _context()) == {"batchItemFailures": []}
            run.assert_not_called()
            assert sqs.method_calls == []
            assert world.rows_under(DB) == before
            assert retirement.get_record(world.t["complianceEvaluationStorage"], DB) == record
            assert [call.args[0] for call in warning.call_args_list] == [
                f"Compliance job message moveDeletedDatabaseCompliance for database {DB} on the "
                "trigger queue dropped: compliance jobs run from the compliance job queue"]

    def test_an_sns_envelope_carrying_the_body_is_not_a_job_message(self):
        with World(_stand_in({"*"})):
            body = {"Type": "Notification", "Message": json.dumps(retirement.move_message(DB))}
            assert trigger.drop_job_message({"body": json.dumps(body)}) is False


def _index_error(message):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, "Scan")


BACKFILLING = "Cannot read from backfilling global secondary index: PipelineRuleWaitIndex"


class _WaitIndexBackfilling:
    """The evaluation table while DynamoDB still builds PipelineRuleWaitIndex after an upgrade:
    every read of that index is refused; the table itself answers."""

    def __init__(self, table, message=BACKFILLING):
        self._table = table
        self._message = message

    def scan(self, **kwargs):
        if kwargs.get("IndexName") == retirement.PIPELINE_WAIT_INDEX:
            raise _index_error(self._message)
        return self._table.scan(**kwargs)

    def query(self, **kwargs):
        if kwargs.get("IndexName") == retirement.PIPELINE_WAIT_INDEX:
            raise _index_error(self._message)
        return self._table.query(**kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


@pytest.mark.unit
class TestTheWaitIndexStillBuilding:
    """On an upgrade AWS CloudFormation completes the table update while DynamoDB still backfills
    the new PipelineRuleWaitIndex (D129). A database delete then answers a retryable 503 with a
    fixed message and changes nothing (no cascade aborted, no record, the database stays live), and
    the workflow callback settles no waiting rule (logged) instead of failing every completion."""

    def test_the_delete_answers_503_and_changes_nothing(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            building = _WaitIndexBackfilling(world.t["complianceEvaluationStorage"])
            with patch.object(retirement.Tables, "evaluation", property(lambda _self: building)):
                result = databaseService.delete_database(DB, {"tokens": [USER], "roles": []})
            assert (result.statusCode, result.message) == (
                503, retirement.WAIT_INDEX_NOT_READY_MESSAGE)
            assert world.t["databaseStorage"].get_item(Key={"databaseId": DB}).get("Item")
            assert retirement.get_record(world.t["complianceEvaluationStorage"], DB) is None
            assert world.t["complianceCascadeStorage"].get_item(
                Key={"cascadeId": PENDING_CASCADE})["Item"]["state"] == "pending_approval"
            assert world.t["complianceEvaluationStorage"].get_item(
                Key={"evaluationId": "ev-wait-#convert"})["Item"]["status"] == "waiting"
            assert world.queued == []
            assert [r for r in world.t["complianceAuditStorage"].scan()["Items"]
                    if r["eventType"] in ("database_deleted", "cascade_database_deleted")] == []
            # Once the index is active the same delete goes through.
            world.delete()

    def test_another_index_error_still_fails_the_delete(self):
        with World(_stand_in({"*"})) as world:
            world.seed()
            broken = _WaitIndexBackfilling(world.t["complianceEvaluationStorage"],
                                           message="Some other validation problem")
            with patch.object(retirement.Tables, "evaluation", property(lambda _self: broken)):
                with pytest.raises(Exception):
                    databaseService.delete_database(DB, {"tokens": [USER], "roles": []})
            assert world.t["databaseStorage"].get_item(Key={"databaseId": DB}).get("Item")

    def test_the_callback_settles_nothing_and_does_not_fail(self):
        table = MagicMock(name="evaluation_table")
        table.query.side_effect = _index_error(BACKFILLING)
        released = MagicMock(return_value={"db:asset"})
        with patch.object(store, "evaluation_table", table):
            counts = store.process_waiting_pipeline_rules("GLOBAL", "wf-1", released)
        assert set(counts.values()) == {0}
        released.assert_not_called()

    def test_another_query_error_still_fails_the_callback(self):
        table = MagicMock(name="evaluation_table")
        table.query.side_effect = _index_error("Some other validation problem")
        with patch.object(store, "evaluation_table", table):
            with pytest.raises(Exception):
                store.process_waiting_pipeline_rules("GLOBAL", "wf-1", lambda: set())


class _CountingEvaluationTable:
    """The evaluation table, counting the retirement-record reads: each GetItem of a
    `databaseRetirement#` key and each BatchGetItem through the table's client."""

    def __init__(self, table):
        self._table = table
        self.record_gets = []
        self.batches = []
        counter = self

        class _Client:
            def batch_get_item(self, **kwargs):
                counter.batches.append(kwargs)
                return table.meta.client.batch_get_item(**kwargs)

            def __getattr__(self, name):
                return getattr(table.meta.client, name)

        class _Meta:
            client = _Client()

        self.meta = _Meta()

    def get_item(self, **kwargs):
        if str(kwargs.get("Key", {}).get("evaluationId", "")).startswith(retirement.RECORD_PREFIX):
            self.record_gets.append(kwargs)
        return self._table.get_item(**kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


SPREAD = [f"spread-db-{n}" for n in range(6)]


@pytest.mark.unit
class TestTheRetirementRecordsOfAPageAreReadTogether:
    """A cross-database listing read the retirement record of each distinct database id with its
    own consistent GetItem, one at a time (D133: 54-80 ms on the first Quarantine and Audit Log
    page). A page's records are now read with one consistent BatchGetItem (keys
    `databaseRetirement#*` only), and a moving database is still recognized."""

    def _seed_spread(self, world):
        for database_id in SPREAD:
            world.t["complianceAuditStorage"].put_item(Item=build_audit_item(
                database_id, "a1", "compliance_check", "SYSTEM_USER", {},
                timestamp="2026-10-03T00:00:00+00:00", asset_name="a1"))
            world.t["complianceAssetStateStorage"].put_item(Item={
                "databaseId": database_id, "assetId": "a1", "complianceState": "quarantined",
                "schemaName": "s", "schemaSource": "database", "assetName": "a1",
                "assetNameSort": "a1"})
            world.t["assetStorage"].put_item(Item={"databaseId": database_id, "assetId": "a1",
                                                   "assetName": "a1"})
            world.t["databaseStorage"].put_item(Item={"databaseId": database_id})
        # One of them is mid-move: its rows belong to `<id>#deleted`.
        world.t["complianceEvaluationStorage"].put_item(Item={
            **retirement.record_key(SPREAD[0]), "recordType": retirement.RECORD_TYPE,
            "databaseId": SPREAD[0], "state": retirement.STATE_MOVING})

    @pytest.mark.parametrize("service, path, rows_key, module_table", [
        (audit_svc, "/compliance/audit", "entries", "evaluation_table"),
        (quarantine_svc, "/compliance/quarantine", "quarantinedAssets", None),
    ], ids=["audit", "quarantine"])
    def test_one_batch_read_per_page(self, service, path, rows_key, module_table):
        with World(_stand_in({"*"})) as world:
            world.seed()
            self._seed_spread(world)
            counting = _CountingEvaluationTable(world.t["complianceEvaluationStorage"])
            target = service if module_table else store
            with patch.object(target, module_table or "evaluation_table", counting):
                status, body = world.get(service, path)
            assert status == 200
            assert counting.record_gets == []
            assert len(counting.batches) == 1
            (request,) = counting.batches[0]["RequestItems"].values()
            assert request["ConsistentRead"] is True
            keys = {key["evaluationId"] for key in request["Keys"]}
            assert all(key.startswith(retirement.RECORD_PREFIX) for key in keys)
            assert {f"{retirement.RECORD_PREFIX}{database_id}" for database_id in SPREAD} <= keys
            listed = {row["databaseId"] for row in body[rows_key]}
            # The moving database's rows are shown under its deleted id (audit) or left out.
            assert SPREAD[0] not in listed
            if service is audit_svc:
                assert f"{SPREAD[0]}#deleted" in listed
            assert set(SPREAD[1:]) <= listed

    def test_prime_batches_by_one_hundred_and_skips_ids_without_a_record(self):
        with World(_stand_in({"*"})) as world:
            counting = _CountingEvaluationTable(world.t["complianceEvaluationStorage"])
            world.t["complianceEvaluationStorage"].put_item(Item={
                **retirement.record_key("db-7"), "recordType": retirement.RECORD_TYPE,
                "databaseId": "db-7", "state": retirement.STATE_MOVING})
            presenter = retirement.Presenter(counting)
            ids = [f"db-{n}" for n in range(150)] + ["db-1", "GLOBAL", "*", "db-3#deleted", None]
            presenter.prime(ids)
            assert [len(next(iter(b["RequestItems"].values()))["Keys"])
                    for b in counting.batches] == [100, 50]
            assert presenter.moving("db-7") is True
            assert presenter.moving("db-8") is False
            assert presenter.moving("GLOBAL") is False
            assert presenter.moving("db-3#deleted") is False
            assert counting.record_gets == []
            # An id not primed is still read on its own.
            assert presenter.moving("late-db") is False
            assert len(counting.record_gets) == 1

    def test_unprocessed_keys_are_asked_again(self):
        table = MagicMock(name="evaluation_table")
        table.name = "evaluations"
        key = {"evaluationId": f"{retirement.RECORD_PREFIX}db-1"}
        table.meta.client.batch_get_item.side_effect = [
            {"Responses": {"evaluations": []},
             "UnprocessedKeys": {"evaluations": {"Keys": [key]}}},
            {"Responses": {"evaluations": [{**key, "state": "moving"}]},
             "UnprocessedKeys": {}},
        ]
        presenter = retirement.Presenter(table)
        presenter.prime(["db-1"])
        assert table.meta.client.batch_get_item.call_count == 2
        assert presenter.moving("db-1") is True
        table.get_item.assert_not_called()
