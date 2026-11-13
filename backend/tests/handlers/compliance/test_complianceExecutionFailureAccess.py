# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A failed pipeline rule's execution failure reason reaches exactly the callers allowed to read the
execution.

The reason is what the execution itself recorded, so `common.compliance.executionFailureAccess`
returns it only to a caller that GET /workflows/executions/{executionId}/details would serve: the
route's Tier-1 check on its path and the execution read rule
(`common.workflows.executionAccess.execution_access_decision`). Covered:

- the helper over its readers: allowed, each denial (route, workflow, asset, missing execution), a
  caller without tokens, a stored JSON list returned untouched when nothing is left out, one decision
  per execution, the per-request bound, and records without the fields;
- for every shipped role template (alone, combined with a compliance role, scoped to another
  database, and against a GLOBAL workflow), the helper's decision is the execution details route's
  own decision on the same rows, with the production enforcer, and matches the expected table: the
  compliance templates alone, which read compliance but no workflow or execution, never see it;
- the compliance reads end to end with the production enforcer of a template role: the evaluation
  history, the asset's compliance state and the quarantine listing.
"""

import json
import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# executionService resolves its table names at import (as the workflow suites seed them).
for _key, _value in {
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME": "t-execv2",
}.items():
    os.environ.setdefault(_key, _value)

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.handlers.workflows import executionService as execution_service  # noqa: E402
from backend.tests.handlers.auth.test_permission_templates_compliance_binding import (  # noqa: E402,F401
    DB_A, DB_B, USER_ID, roles_enforcer,
)
from backend.tests.handlers.compliance._harness import (  # noqa: E402
    COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, body_of, claims_for, database_table_with,
    live_only_asset_table, mirror_engine, real_constants, rest_event,
)
from common.compliance import executionFailureAccess as failure_access  # noqa: E402
from handlers.compliance import complianceEvaluateService as evaluate_svc  # noqa: E402
from handlers.compliance import complianceEvaluationStore as store  # noqa: E402
from handlers.compliance import complianceQuarantineService as quarantine_svc  # noqa: E402

EXECUTION_ID = "e1000000000000000000000000000094"
OTHER_EXECUTION_ID = "e2000000000000000000000000000094"
ASSET = "bracket-asset"
WORKFLOW_ID = "conversion-3d-basic"
GLOBAL = "GLOBAL"
REASON = ("ValueError: latin1.obj could not be read as OBJ text: byte 0xE8 at offset 5 is not "
          "UTF-8")
GENERIC = "Pipeline execution FAILED"


def _failed_result(execution_id=EXECUTION_ID, reason=REASON):
    return {"ruleName": "converts", "ruleType": "pipeline", "enforcement": "quarantine",
            "passed": False, "status": "evaluated", "message": GENERIC, "measured": None,
            "expected": None, "executionId": execution_id, "executionFailureReason": reason}


def _key_values(condition):
    """{attribute: value} of a boto3 key condition built from `Key(...).eq(...)` terms."""
    expression = condition.get_expression()
    if expression["operator"] == "AND":
        values = {}
        for term in expression["values"]:
            values.update(_key_values(term))
        return values
    key, value = expression["values"]
    return {key.name: value}


class ExecutionRows:
    """The rows one execution's read decision reads, as the four execution tables and the asset
    table serve them: the main row (absent with `main=False`), the configuration row, the input
    rows (one per input asset), the workflow definition and the asset rows. `calls` counts each
    table's reads."""

    def __init__(self, workflow_database_id=DB_A, input_assets=((DB_A, ASSET),), config=None,
                 assets=None, main=True):
        self.main = ({"workflowExecutionId": EXECUTION_ID, "workflowDatabaseId": workflow_database_id,
                      "workflowId": WORKFLOW_ID, "executionStatus": "FAILED"} if main else None)
        self.workflow_database_id = workflow_database_id
        self.input_assets = list(input_assets)
        self.config = dict(config or {"workflowExecutionId": EXECUTION_ID,
                                      "recordType": "configuration"})
        self.assets = assets if assets is not None else {
            (DB_A, ASSET): {"databaseId": DB_A, "assetId": ASSET, "assetName": "Bracket",
                            "assetType": ".obj", "tags": []}}
        self.tables = {
            "workflow_executions_table": MagicMock(name="workflow_executions_table"),
            "execution_configuration_table": MagicMock(name="execution_configuration_table"),
            "execution_inputs_table": MagicMock(name="execution_inputs_table"),
            "workflow_table": MagicMock(name="workflow_table"),
            "execution_asset_table": MagicMock(name="execution_asset_table"),
        }
        self.tables["workflow_executions_table"].query.side_effect = (
            lambda **_kw: {"Items": [dict(self.main)] if self.main else []})
        self.tables["execution_configuration_table"].get_item.side_effect = (
            lambda **_kw: {"Item": dict(self.config)})
        self.tables["execution_inputs_table"].query.side_effect = lambda **_kw: {"Items": [
            {"workflowExecutionId": EXECUTION_ID, "databaseId": d, "assetId": a}
            for d, a in self.input_assets]}
        self.tables["workflow_table"].get_item.side_effect = lambda **_kw: {"Item": self.workflow()}
        self.tables["execution_asset_table"].query.side_effect = self._asset_query

    def workflow(self):
        return {"databaseId": self.workflow_database_id, "workflowId": WORKFLOW_ID,
                "workflowName": "3D Basic Conversion"}

    def asset(self, database_id, asset_id):
        row = self.assets.get((database_id, asset_id))
        return dict(row) if row else None

    def _asset_query(self, KeyConditionExpression, **_kw):
        keys = _key_values(KeyConditionExpression)
        row = self.asset(keys["databaseId"], keys["assetId"])
        return {"Items": [row] if row else []}

    def patches(self):
        return [patch.object(failure_access, name, table) for name, table in self.tables.items()]

    def calls(self, name):
        table = self.tables[name]
        return table.query.call_count + table.get_item.call_count


def _decide(enforcer, rows, execution_id=EXECUTION_ID):
    """The helper's decision for `enforcer` over `rows`."""
    scope = failure_access.new_scope(enforcer)
    patches = rows.patches()
    for p in patches:
        p.start()
    try:
        return failure_access.may_read_execution(scope, execution_id)
    finally:
        for p in patches:
            p.stop()


def _route_decision(enforcer, rows):
    """What GET /workflows/executions/{executionId}/details decides for `enforcer` over the same
    rows: its Tier-1 `enforceAPI` on the route's path, then `authorize_execution_access` (the route's
    Tier-2 rule) with the route's own readers serving `rows`."""
    claims = {"tokens": [USER_ID], "roles": [], "mfaEnabled": True}
    details_event = {"requestContext": {"http": {
        "method": "GET", "path": f"/workflows/executions/{EXECUTION_ID}/details"}}}
    with patch.object(authz_module, "request_to_claims", return_value=claims), \
            patch.object(authz_module, "log_authorization_api", MagicMock()):
        if not enforcer.enforceAPI(details_event):
            return False
    if rows.main is None:
        return False
    execution_service._asset_details_cache.clear()
    execution_service._authz_decision_cache.clear()
    execution_service._disarm_authz_entity_budget()
    with patch.object(execution_service, "claims_and_roles", claims), \
            patch.object(execution_service, "CasbinEnforcer", return_value=enforcer), \
            patch.object(execution_service, "get_execution_input_assets",
                         side_effect=lambda _eid: list(rows.input_assets)), \
            patch.object(execution_service, "get_workflow_execution_configuration_row",
                         side_effect=lambda _eid: dict(rows.config)), \
            patch.object(execution_service, "get_workflow_definition",
                         side_effect=lambda _db, _wf: rows.workflow()), \
            patch.object(execution_service, "get_asset_details", side_effect=rows.asset), \
            patch.object(execution_service, "_batch_get_rows", return_value=[]):
        allowed, _reason = execution_service.authorize_execution_access(
            EXECUTION_ID, dict(rows.main), "GET")
    return allowed


def _stand_in(allow=lambda obj, action: True):
    """A CasbinEnforcer stand-in answering `allow(obj, action)`."""
    enforcer = MagicMock(name="CasbinEnforcer")
    enforcer.enforce.side_effect = allow
    return enforcer


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    """The real permission field matrix for the enforcer and the template import (see
    `_harness.real_constants`)."""
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestTheHelper:

    def test_a_caller_allowed_on_the_execution_keeps_the_reason(self):
        enforcer = _stand_in()
        rows = ExecutionRows()
        scope = failure_access.new_scope(enforcer)
        patches = rows.patches()
        for p in patches:
            p.start()
        try:
            results = failure_access.rule_results_for_caller(scope, [_failed_result()])
        finally:
            for p in patches:
                p.stop()
        assert results == [_failed_result()]
        checked = [(c.args[0].get("object__type"), c.args[1]) for c in enforcer.enforce.call_args_list]
        assert checked == [("api", "GET"), ("workflow", "GET"), ("asset", "GET")]
        assert enforcer.enforce.call_args_list[0].args[0] == {
            "object__type": "api", "route__path": f"/workflows/executions/{EXECUTION_ID}/details"}

    @pytest.mark.parametrize("denied", ["api", "workflow", "asset"])
    def test_a_denial_on_the_route_the_workflow_or_an_asset_withholds_the_reason(self, denied):
        enforcer = _stand_in(lambda obj, action: obj.get("object__type") != denied)
        assert _decide(enforcer, ExecutionRows()) is False

    def test_a_denied_metadata_source_database_or_output_asset_withholds_the_reason(self):
        out_asset = {"databaseId": DB_B, "assetId": "out", "assetName": "Out", "tags": []}
        rows = ExecutionRows(
            config={"inputMetadataDatabaseId": DB_B, "outputDatabaseId": DB_B,
                    "outputAssetId": "out"},
            assets={(DB_A, ASSET): {"databaseId": DB_A, "assetId": ASSET, "tags": []},
                    (DB_B, "out"): out_asset})
        assert _decide(_stand_in(lambda obj, action: obj.get("databaseId") != DB_B), rows) is False
        assert _decide(_stand_in(), rows) is True

    def test_an_execution_without_a_main_row_is_readable_by_nobody(self):
        assert _decide(_stand_in(), ExecutionRows(main=False)) is False

    def test_a_deleted_asset_defers_to_its_database(self):
        rows = ExecutionRows(assets={})
        allowed_on_database = _stand_in(lambda obj, action: obj.get("object__type") != "asset")
        assert _decide(allowed_on_database, rows) is True
        assert _decide(_stand_in(lambda obj, action: obj.get("object__type") != "database"),
                       rows) is False

    def test_a_caller_without_tokens_reads_no_reason(self):
        scope = failure_access.new_scope(None)
        assert failure_access.rule_results_for_caller(scope, [_failed_result()]) == [
            {k: v for k, v in _failed_result().items() if k != "executionFailureReason"}]

    def test_a_withheld_reason_leaves_the_message_and_the_execution_link(self):
        rows = ExecutionRows()
        scope = failure_access.new_scope(_stand_in(lambda obj, action: obj["object__type"] != "api"))
        stored = json.dumps([_failed_result()])
        with patch.object(failure_access, "workflow_executions_table",
                          rows.tables["workflow_executions_table"]):
            text = failure_access.rule_results_for_caller(scope, stored)
        [result] = json.loads(text)
        assert result["message"] == GENERIC
        assert result["executionId"] == EXECUTION_ID
        assert "executionFailureReason" not in result

    def test_stored_results_without_a_reason_are_returned_untouched_and_read_nothing(self):
        enforcer = _stand_in()
        rows = ExecutionRows()
        stored = json.dumps([{"ruleName": "r", "ruleType": "metadata", "enforcement": "warn",
                              "passed": False, "message": "missing owner"}])
        scope = failure_access.new_scope(enforcer)
        patches = rows.patches()
        for p in patches:
            p.start()
        try:
            assert failure_access.rule_results_for_caller(scope, stored) is stored
            assert failure_access.evaluation_for_caller(scope, {"evaluationId": "e"}) == {
                "evaluationId": "e"}
            record = {"databaseId": DB_A, "assetId": ASSET, "complianceState": "compliant"}
            assert failure_access.state_record_for_caller(scope, record) == record
        finally:
            for p in patches:
                p.stop()
        enforcer.enforce.assert_not_called()
        assert all(rows.calls(name) == 0 for name in rows.tables)

    def test_each_execution_is_decided_once_per_request(self):
        enforcer = _stand_in()
        rows = ExecutionRows()
        scope = failure_access.new_scope(enforcer)
        patches = rows.patches()
        for p in patches:
            p.start()
        try:
            results = failure_access.rule_results_for_caller(scope, [_failed_result()] * 3)
            record = failure_access.state_record_for_caller(scope, {
                "quarantineExecutionFailures": [
                    {"ruleName": "converts", "executionId": EXECUTION_ID,
                     "executionFailureReason": REASON}]})
        finally:
            for p in patches:
                p.stop()
        assert all(r["executionFailureReason"] == REASON for r in results)
        assert record["quarantineExecutionFailures"][0]["executionFailureReason"] == REASON
        assert rows.calls("workflow_executions_table") == 1
        assert rows.calls("execution_configuration_table") == 1

    def test_the_request_decides_a_bounded_number_of_executions(self):
        enforcer = _stand_in()
        rows = ExecutionRows()
        scope = failure_access.new_scope(enforcer)
        bound = failure_access.MAX_EXECUTIONS_DECIDED_PER_REQUEST
        results = [_failed_result(execution_id=f"e{index:031d}") for index in range(bound + 2)]
        patches = rows.patches()
        for p in patches:
            p.start()
        try:
            filtered = failure_access.rule_results_for_caller(scope, results)
        finally:
            for p in patches:
                p.stop()
        kept = [r for r in filtered if "executionFailureReason" in r]
        assert len(kept) == bound
        assert all("executionFailureReason" not in r for r in filtered[bound:])
        assert rows.calls("workflow_executions_table") == bound

    def test_a_quarantine_entry_keeps_its_rule_and_execution_when_the_reason_is_withheld(self):
        scope = failure_access.new_scope(_stand_in(lambda obj, action: False))
        record = failure_access.state_record_for_caller(scope, {
            "complianceState": "quarantined", "quarantineReason": GENERIC,
            "quarantineExecutionFailures": [
                {"ruleName": "converts", "executionId": EXECUTION_ID,
                 "executionFailureReason": REASON}]})
        assert record["quarantineReason"] == GENERIC
        assert record["quarantineExecutionFailures"] == [
            {"ruleName": "converts", "executionId": EXECUTION_ID}]

    @pytest.mark.parametrize("record", [
        {"complianceState": "compliant"},
        {"complianceState": "compliant", "quarantineExecutionFailures": None},
        {"complianceState": "quarantined", "quarantineReason": GENERIC},
    ])
    def test_records_written_without_the_field_still_read(self, record):
        scope = failure_access.new_scope(_stand_in())
        assert failure_access.state_record_for_caller(scope, dict(record)) == record


# ---------------------------------------------------------------------------------------------
# role name -> (template, databaseId); the expected decision for an execution of a workflow of
# DB_A over an asset of DB_A, and of a GLOBAL workflow over the same asset.
ROLE_SETS = {
    "compliance-readonly": ({"c": ("compliance-readonly.json", DB_A)}, False, False),
    "compliance-admin": ({"c": ("compliance-admin.json", DB_A)}, False, False),
    "compliance-global-admin": ({"c": ("compliance-global-admin.json", DB_A)}, False, False),
    "database-readonly": ({"d": ("database-readonly.json", DB_A)}, True, False),
    "database-user": ({"d": ("database-user.json", DB_A)}, True, True),
    "database-admin": ({"d": ("database-admin.json", DB_A)}, True, True),
    "global-readonly": ({"g": ("global-readonly.json", DB_A)}, True, True),
    "database-tag-admin": ({"t": ("database-tag-admin.json", DB_A)}, False, False),
    "deny-tagged-assets": ({"x": ("deny-tagged-assets.json", DB_A)}, False, False),
    "compliance-readonly + database-readonly": (
        {"c": ("compliance-readonly.json", DB_A), "d": ("database-readonly.json", DB_A)},
        True, False),
    "compliance-admin + database-user": (
        {"c": ("compliance-admin.json", DB_A), "d": ("database-user.json", DB_A)}, True, True),
    "database-user of another database": ({"d": ("database-user.json", DB_B)}, False, False),
    # The compliance role reads the asset of DB_A, the other database's user role the execution
    # routes and GLOBAL workflows: together they open an execution of a GLOBAL workflow over the
    # asset, and see its reason, but not one of DB_A's own workflow.
    "compliance-readonly + database-user of another database": (
        {"c": ("compliance-readonly.json", DB_A), "d": ("database-user.json", DB_B)},
        False, True),
}


@pytest.mark.unit
class TestEveryShippedTemplate:
    """The helper decides each role exactly as the execution details route does, and the reason is
    visible to the roles that may read executions and to no other."""

    @pytest.mark.parametrize("set_name", list(ROLE_SETS))
    @pytest.mark.parametrize("workflow_database_id", [DB_A, GLOBAL])
    def test_the_reason_is_visible_exactly_where_the_execution_is(
            self, roles_enforcer, set_name, workflow_database_id):
        roles, expected_db_workflow, expected_global_workflow = ROLE_SETS[set_name]
        enforcer = roles_enforcer(roles)
        rows = ExecutionRows(workflow_database_id=workflow_database_id)
        route = _route_decision(enforcer, rows)
        helper = _decide(enforcer, rows)
        assert helper == route
        assert helper is (expected_db_workflow if workflow_database_id == DB_A
                          else expected_global_workflow)

    @pytest.mark.parametrize("set_name", ["database-user", "global-readonly"])
    def test_the_deny_editing_overlay_leaves_a_tagged_assets_reason_visible(
            self, roles_enforcer, set_name):
        """The overlay denies PUT, POST and DELETE on the tagged asset, not GET, so the execution
        over it stays readable and so does its reason."""
        roles, _, _ = ROLE_SETS[set_name]
        enforcer = roles_enforcer({**roles, "x": ("deny-tagged-assets.json", DB_A)})
        rows = ExecutionRows(assets={(DB_A, ASSET): {
            "databaseId": DB_A, "assetId": ASSET, "assetName": "Bracket", "assetType": ".obj",
            "tags": ["locked"]}})
        assert _decide(enforcer, rows) is _route_decision(enforcer, rows) is True


# ---------------------------------------------------------------------------------------------
def _real(enforcer):
    """A CasbinEnforcer factory for the handler modules: Tier 2 is the template role's real
    enforcer; Tier 1 of the compliance route passes (the compliance routes' constraints are the
    template tests' subject)."""
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = enforcer.enforce
    mirror_engine(stand_in, enforcer)
    return lambda _claims: stand_in


READER_ONLY = {"c": ("compliance-readonly.json", DB_A)}
READER_WITH_EXECUTIONS = {"c": ("compliance-readonly.json", DB_A),
                          "d": ("database-readonly.json", DB_A)}
ADMIN_ONLY = {"c": ("compliance-admin.json", DB_A)}
ADMIN_WITH_EXECUTIONS = {"c": ("compliance-admin.json", DB_A), "d": ("database-readonly.json", DB_A)}

QUARANTINED_RECORD = {
    "databaseId": DB_A, "assetId": ASSET, "assetName": "Bracket", "complianceState": "quarantined",
    "schemaName": "3d-model-quality", "quarantineReason": GENERIC,
    "quarantineExecutionFailures": [{"ruleName": "converts", "executionId": EXECUTION_ID,
                                     "executionFailureReason": REASON}],
}


@pytest.mark.unit
class TestTheComplianceReads:

    @pytest.fixture(autouse=True)
    def _execution_rows(self):
        rows = ExecutionRows()
        patches = rows.patches() + [
            patch.object(store, "asset_table", live_only_asset_table()),
            patch.object(evaluate_svc, "asset_table", live_only_asset_table()),
            patch.object(quarantine_svc, "asset_table", live_only_asset_table()),
        ]
        for p in patches:
            p.start()
        yield rows
        for p in patches:
            p.stop()

    def _history(self, enforcer):
        evaluation_table = MagicMock(name="evaluation_table")
        evaluation_table.query.return_value = {"Items": [{
            "evaluationId": "eval-94", "databaseId": DB_A, "assetId": ASSET,
            "verdict": "quarantined", "violations": [GENERIC], "executionId": EXECUTION_ID,
            "ruleResults": json.dumps([_failed_result()])}]}
        svc = evaluate_svc
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(enforcer)), \
                patch(f"{svc.__name__}.evaluation_table", evaluation_table):
            response = svc.lambda_handler(rest_event(
                "GET", f"/compliance/evaluations/{DB_A}/{ASSET}",
                {"databaseId": DB_A, "assetId": ASSET}), MagicMock())
        assert response["statusCode"] == 200
        [evaluation] = body_of(response)["evaluations"]
        assert evaluation["violations"] == [GENERIC]
        return json.loads(evaluation["ruleResults"])[0]

    def _state(self, enforcer):
        svc = evaluate_svc
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(enforcer)), \
                patch.object(store, "get_compliance_record", return_value=dict(QUARANTINED_RECORD)):
            response = svc.lambda_handler(rest_event(
                "GET", f"/compliance/state/{DB_A}/{ASSET}",
                {"databaseId": DB_A, "assetId": ASSET}), MagicMock())
        assert response["statusCode"] == 200
        body = body_of(response)
        assert body["quarantineReason"] == GENERIC
        return body["quarantineExecutionFailures"][0]

    def _quarantine_listing(self, enforcer):
        svc = quarantine_svc
        state_table = MagicMock(name="asset_state_table")
        state_table.query.return_value = {"Items": [dict(QUARANTINED_RECORD)]}
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(enforcer)), \
                patch(f"{svc.__name__}.asset_state_table", state_table):
            response = svc.lambda_handler(rest_event("GET", "/compliance/quarantine"), MagicMock())
        assert response["statusCode"] == 200
        [row] = body_of(response)["quarantinedAssets"]
        assert row["quarantineReason"] == GENERIC
        return row["quarantineExecutionFailures"][0]

    def _overview(self, enforcer):
        svc = evaluate_svc
        with mock_aws():
            table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
                **COMPLIANCE_ASSET_STATE_TABLE_DEFINITION)
            table.put_item(Item=dict(QUARANTINED_RECORD))
            with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                    patch(f"{svc.__name__}.CasbinEnforcer", _real(enforcer)), \
                    patch(f"{svc.__name__}.asset_state_table", table), \
                    patch.object(store, "database_table", database_table_with(DB_A)):
                response = svc.lambda_handler(rest_event(
                    "GET", f"/compliance/state/{DB_A}", {"databaseId": DB_A}), MagicMock())
        assert response["statusCode"] == 200, response
        [row] = body_of(response)["assets"]
        assert row["quarantineReason"] == GENERIC
        return row["quarantineExecutionFailures"][0]

    def _evaluate(self, enforcer):
        """POST evaluate of an evaluation whose pipeline rule reported during its launches."""
        svc = evaluate_svc
        evaluation = {"evaluationId": "eval-94", "verdict": "quarantined",
                      "complianceState": "quarantined", "ruleResults": [_failed_result()],
                      "pipelineRulesPending": 0}
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(enforcer)), \
                patch.object(store, "get_asset_item", return_value={"assetId": ASSET}), \
                patch.object(store, "get_compliance_record",
                             return_value={"schemaName": "3d-model-quality"}), \
                patch.object(store, "run_evaluation", return_value=evaluation), \
                patch(f"{svc.__name__}.check_and_trigger_cascade"):
            response = svc.lambda_handler(rest_event(
                "POST", f"/compliance/evaluate/{DB_A}/{ASSET}",
                {"databaseId": DB_A, "assetId": ASSET}, body={}), MagicMock())
        assert response["statusCode"] == 200, response
        [result] = body_of(response)["ruleResults"]
        assert result["message"] == GENERIC
        return result

    @pytest.mark.parametrize("read", ["_history", "_state", "_quarantine_listing", "_overview"])
    def test_a_compliance_reader_without_execution_access_gets_the_link_only(
            self, roles_enforcer, read):
        entry = getattr(self, read)(roles_enforcer(READER_ONLY))
        assert entry["executionId"] == EXECUTION_ID
        assert "executionFailureReason" not in entry

    @pytest.mark.parametrize("read", ["_history", "_state", "_quarantine_listing", "_overview"])
    def test_a_compliance_reader_who_may_read_the_execution_gets_the_reason(
            self, roles_enforcer, read):
        entry = getattr(self, read)(roles_enforcer(READER_WITH_EXECUTIONS))
        assert entry["executionId"] == EXECUTION_ID
        assert entry["executionFailureReason"] == REASON

    def test_an_evaluate_response_carries_the_reason_only_for_an_execution_reader(
            self, roles_enforcer):
        withheld = self._evaluate(roles_enforcer(ADMIN_ONLY))
        assert withheld["executionId"] == EXECUTION_ID
        assert "executionFailureReason" not in withheld
        shown = self._evaluate(roles_enforcer(ADMIN_WITH_EXECUTIONS))
        assert shown["executionFailureReason"] == REASON
