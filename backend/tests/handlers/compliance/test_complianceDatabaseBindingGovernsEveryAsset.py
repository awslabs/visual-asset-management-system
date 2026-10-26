# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A database binding governs every asset of the database, whatever auto-evaluation says (D24).

An asset created after the binding in a database whose `complianceAutoEval` is off is registered
under the binding by the trigger (from the asset's INSERT) and not evaluated; `evaluate` without a
schema falls back to the database binding; and a sweep registers the bound databases' assets that
have no record yet, so it reaches them."""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance._harness import (
    ASSET, COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, DB, SCHEMA, USER, claims_for, enforcer,
    rest_event)
from backend.tests.handlers.compliance.test_complianceTrigger import (
    _run, _sns_event, _stream_message)
from handlers.compliance import complianceEvaluateService as svc
from handlers.compliance import complianceEvaluationStore as store

STORE = "handlers.compliance.complianceEvaluationStore"
SVC = "handlers.compliance.complianceEvaluateService"
OFF_DB = {"databaseId": DB, "complianceAutoEval": False, "complianceSchemaName": SCHEMA}
EVALUATION = {"evaluationId": "eval-1", "verdict": "compliant", "complianceState": "compliant",
              "ruleResults": [], "pipelineRulesPending": 0}


# --- the trigger registers a new asset of an auto-evaluation-off bound database -----------------

@pytest.mark.unit
class TestTheTriggerRegistersANewAsset:

    def _run(self, message, database_item, compliance_record=None):
        with patch(f"{STORE}.register_under_database_binding") as register:
            mocks = _run(_sns_event(message), database_item=database_item,
                         compliance_record=compliance_record)
        mocks["register"] = register
        return mocks

    def test_an_insert_in_a_bound_database_with_auto_eval_off_is_registered_not_evaluated(self):
        mocks = self._run(_stream_message("INSERT"), OFF_DB)
        mocks["register"].assert_called_once_with(DB, ASSET, SCHEMA)
        mocks["run_evaluation"].assert_not_called()
        assert all(c.kwargs == {"consistent_read": True}
                   for c in mocks["get_compliance_record"].call_args_list)

    def test_an_asset_already_recorded_is_left_as_it_is(self):
        mocks = self._run(_stream_message("INSERT"), OFF_DB,
                          compliance_record={"schemaName": "override", "schemaSource": "asset"})
        mocks["register"].assert_not_called()

    @pytest.mark.parametrize("database_item", [None, {"databaseId": DB},
                                               {"databaseId": DB, "complianceAutoEval": False}])
    def test_a_database_without_a_binding_registers_nothing(self, database_item):
        mocks = self._run(_stream_message("INSERT"), database_item)
        mocks["register"].assert_not_called()
        mocks["get_compliance_record"].assert_not_called()

    def test_a_modify_does_not_register(self):
        mocks = self._run(_stream_message("MODIFY", change_source="assetEdit"), OFF_DB)
        mocks["register"].assert_not_called()
        mocks["run_evaluation"].assert_not_called()

    def test_with_auto_eval_on_the_evaluation_registers_and_evaluates_once(self):
        mocks = self._run(_stream_message("INSERT"),
                          {"databaseId": DB, "complianceAutoEval": True,
                           "complianceSchemaName": SCHEMA})
        mocks["register"].assert_not_called()
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")


# --- evaluate falls back to the database binding ------------------------------------------------

def _evaluate(database_item, compliance_record=None):
    event = rest_event("POST", f"/compliance/evaluate/{DB}/{ASSET}",
                       {"databaseId": DB, "assetId": ASSET}, body={})
    with patch(f"{SVC}.request_to_claims", claims_for(USER)), \
            patch(f"{SVC}.CasbinEnforcer", return_value=enforcer()), \
            patch(f"{STORE}.get_asset_item", return_value={"assetId": ASSET}), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.get_database_item", return_value=database_item), \
            patch(f"{STORE}.register_under_database_binding") as register, \
            patch(f"{STORE}.run_evaluation", return_value=EVALUATION) as run_evaluation, \
            patch(f"{SVC}.check_and_trigger_cascade"):
        response = svc.lambda_handler(event, MagicMock())
    return response, register, run_evaluation


@pytest.mark.unit
class TestEvaluateFallsBackToTheDatabaseBinding:

    def test_an_unrecorded_asset_of_a_bound_database_is_registered_and_evaluated(self):
        response, register, run_evaluation = _evaluate(OFF_DB)
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["schemaName"] == SCHEMA
        register.assert_called_once_with(DB, ASSET, SCHEMA)
        assert run_evaluation.call_args.args[:3] == (DB, ASSET, SCHEMA)

    def test_a_recorded_binding_is_used_without_reading_the_database(self):
        response, register, _ = _evaluate(None, compliance_record={"schemaName": "override"})
        assert json.loads(response["body"])["schemaName"] == "override"
        register.assert_not_called()

    def test_an_unbound_database_is_still_refused(self):
        response, register, run_evaluation = _evaluate({"databaseId": DB})
        assert response["statusCode"] == 400
        assert "no bound schema" in json.loads(response["body"])["message"]
        register.assert_not_called()
        run_evaluation.assert_not_called()


# --- the sweep registers the bound databases' unrecorded assets ---------------------------------

def _sweep(bound_databases, casbin=None, state_rows=()):
    event = rest_event("POST", f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA}, body={})
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": list(state_rows)}
    with patch(f"{SVC}.request_to_claims", claims_for(USER)), \
            patch(f"{SVC}.CasbinEnforcer", return_value=casbin or enforcer()), \
            patch(f"{SVC}.asset_state_table", state_table), \
            patch(f"{STORE}.load_schema_item", return_value={"schemaName": SCHEMA}), \
            patch(f"{STORE}.databases_bound_to_schema", return_value=list(bound_databases)), \
            patch(f"{STORE}.register_unrecorded_assets", return_value=2) as register, \
            patch(f"{STORE}.run_evaluation", return_value=EVALUATION), \
            patch(f"{SVC}.check_and_trigger_cascade"):
        response = svc.lambda_handler(event, MagicMock())
    return response, register


@pytest.mark.unit
class TestASweepRegistersTheUnrecordedAssetsOfItsBoundDatabases:

    def test_each_bound_database_is_registered_before_the_bound_rows_are_read(self):
        response, register = _sweep(["db-a", "db-b"])
        assert response["statusCode"] == 200
        assert [c.args for c in register.call_args_list] == [("db-a", SCHEMA), ("db-b", SCHEMA)]

    def test_a_database_the_caller_may_not_evaluate_is_not_registered(self):
        casbin = enforcer()
        casbin.enforce.side_effect = lambda obj, action: not (
            obj.get("object__type") == "complianceEvaluation" and obj.get("databaseId") == "db-b")
        _, register = _sweep(["db-a", "db-b"], casbin=casbin)
        assert [c.args for c in register.call_args_list] == [("db-a", SCHEMA)]


# --- the store helpers against real conditional writes (moto) ---------------------------------

ASSET_TABLE = {
    "TableName": "assetStorageTable-d24",
    "KeySchema": [{"AttributeName": "databaseId", "KeyType": "HASH"},
                  {"AttributeName": "assetId", "KeyType": "RANGE"}],
    "AttributeDefinitions": [{"AttributeName": "databaseId", "AttributeType": "S"},
                             {"AttributeName": "assetId", "AttributeType": "S"}],
    "BillingMode": "PAY_PER_REQUEST",
}
DATABASE_TABLE = {
    "TableName": "databaseStorageTable-d24",
    "KeySchema": [{"AttributeName": "databaseId", "KeyType": "HASH"}],
    "AttributeDefinitions": [{"AttributeName": "databaseId", "AttributeType": "S"}],
    "BillingMode": "PAY_PER_REQUEST",
}


@pytest.fixture
def moto_tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        tables = {"asset": resource.create_table(**ASSET_TABLE),
                  "state": resource.create_table(**COMPLIANCE_ASSET_STATE_TABLE_DEFINITION),
                  "database": resource.create_table(**DATABASE_TABLE)}
        with patch(f"{STORE}.asset_table", tables["asset"]), \
                patch(f"{STORE}.asset_state_table", tables["state"]), \
                patch(f"{STORE}.database_table", tables["database"]):
            yield tables


def _state_row(tables, asset_id=ASSET):
    return tables["state"].get_item(Key={"databaseId": DB, "assetId": asset_id}).get("Item")


@pytest.mark.unit
class TestStoreRegistration:

    def test_an_unrecorded_asset_is_registered_pending(self, moto_tables):
        store.register_under_database_binding(DB, ASSET, SCHEMA)
        row = _state_row(moto_tables)
        assert (row["schemaName"], row["schemaSource"], row["complianceState"]) == (
            SCHEMA, "database", "pending_evaluation")

    def test_an_override_is_never_replaced(self, moto_tables):
        moto_tables["state"].put_item(Item={"databaseId": DB, "assetId": ASSET,
                                            "schemaName": "override", "schemaSource": "asset",
                                            "complianceState": "quarantined"})
        store.register_under_database_binding(DB, ASSET, SCHEMA)
        assert _state_row(moto_tables)["schemaName"] == "override"

    def test_a_row_without_a_schema_keeps_its_state(self, moto_tables):
        moto_tables["state"].put_item(Item={"databaseId": DB, "assetId": ASSET,
                                            "complianceState": "non_compliant"})
        store.register_under_database_binding(DB, ASSET, SCHEMA)
        row = _state_row(moto_tables)
        assert (row["schemaName"], row["complianceState"]) == (SCHEMA, "non_compliant")

    def test_the_databases_bound_to_a_schema_are_found(self, moto_tables):
        for item in ({"databaseId": "db-a", "complianceSchemaName": SCHEMA},
                     {"databaseId": "db-b", "complianceSchemaName": "other"},
                     {"databaseId": "db-c"}):
            moto_tables["database"].put_item(Item=item)
        assert store.databases_bound_to_schema(SCHEMA) == ["db-a"]

    def test_only_the_unrecorded_live_assets_are_registered(self, moto_tables):
        for asset_id in ("a-new", "a-recorded"):
            moto_tables["asset"].put_item(Item={"databaseId": DB, "assetId": asset_id})
        moto_tables["asset"].put_item(Item={"databaseId": f"{DB}#deleted", "assetId": "a-archived"})
        moto_tables["state"].put_item(Item={"databaseId": DB, "assetId": "a-recorded",
                                            "schemaName": SCHEMA, "complianceState": "compliant"})
        assert store.register_unrecorded_assets(DB, SCHEMA) == 1
        assert _state_row(moto_tables, "a-new")["complianceState"] == "pending_evaluation"
        assert _state_row(moto_tables, "a-recorded")["complianceState"] == "compliant"
        assert _state_row(moto_tables, "a-archived") is None
