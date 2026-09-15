# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceEvaluateService: dispatch of its five method+path pairs, both authorization tiers,
input validation, the evaluate / sweep flows over a patched store, and the two listings (evaluation
history pages externally with a round-tripping token; the database overview reads its rows to
exhaustion)."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, SCHEMA, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from handlers.compliance import complianceEvaluateService as svc

MOD = "handlers.compliance.complianceEvaluateService"
STORE = "handlers.compliance.complianceEvaluationStore"

EVALUATE_PATH = f"/compliance/evaluate/{DB}/{ASSET}"
SWEEP_PATH = f"/compliance/sweep/{SCHEMA}"
EVALUATIONS_PATH = f"/compliance/evaluations/{DB}/{ASSET}"
STATE_ASSET_PATH = f"/compliance/state/{DB}/{ASSET}"
STATE_DB_PATH = f"/compliance/state/{DB}"
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}
DB_PARAMS = {"databaseId": DB}
SCHEMA_PARAMS = {"schemaName": SCHEMA}

EVALUATION_RESULT = {"evaluationId": "eval-1", "verdict": "compliant",
                     "complianceState": "compliant", "ruleResults": [], "pipelineRulesPending": 0}


def _run(event, tokens=(USER,), api=True, obj=True, asset_exists=True, compliance_record=None,
         evaluation_result=None, schema_item=None, state_rows=None, evaluation_rows=None,
         evaluation_last_key=None):
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": list(state_rows or [])}
    evaluation_table = MagicMock(name="evaluation_table")
    page = {"Items": list(evaluation_rows or [])}
    if evaluation_last_key is not None:
        page["LastEvaluatedKey"] = evaluation_last_key
    evaluation_table.query.return_value = page
    asset_table = MagicMock(name="asset_table")
    asset_table.get_item.return_value = {"Item": {"assetName": "Turbine"}}
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer(api=api, obj=obj)), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.evaluation_table", evaluation_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{STORE}.get_asset_item",
                  return_value={"assetId": ASSET} if asset_exists else None), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.load_schema_item", return_value=schema_item), \
            patch(f"{STORE}.run_evaluation",
                  return_value=evaluation_result or EVALUATION_RESULT) as run_evaluation, \
            patch(f"{MOD}.check_and_trigger_cascade") as cascade:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"state": state_table, "evaluation": evaluation_table, "asset": asset_table,
                      "run_evaluation": run_evaluation, "cascade": cascade}


@pytest.mark.unit
class TestRouteDispatch:

    def test_post_evaluate_runs_an_evaluation_and_the_cascade_check(self):
        response, mocks = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={}),
                               compliance_record={"schemaName": SCHEMA})
        assert response["statusCode"] == 200, response
        body = body_of(response)
        assert body["verdict"] == "compliant"
        assert body["schemaName"] == SCHEMA
        assert body["evaluationId"] == "eval-1"
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, USER)
        mocks["cascade"].assert_called_once_with(DB, ASSET)

    def test_post_evaluate_with_no_body_uses_the_bound_schema(self):
        response, mocks = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS),
                               compliance_record={"schemaName": "bound-schema"})
        assert response["statusCode"] == 200
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, "bound-schema", USER)

    def test_post_evaluate_prefers_the_requested_schema(self):
        response, mocks = _run(
            rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={"schemaName": "requested"}),
            compliance_record={"schemaName": "bound-schema"})
        assert response["statusCode"] == 200
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, "requested", USER)

    def test_post_sweep_evaluates_every_bound_asset(self):
        response, mocks = _run(
            rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}), schema_item=schema_row(),
            state_rows=[{"databaseId": DB, "assetId": "a"}, {"databaseId": "db2", "assetId": "b"}])
        assert response["statusCode"] == 200, response
        body = body_of(response)
        assert [(t["databaseId"], t["assetId"]) for t in body["assetsTriggered"]] == [
            (DB, "a"), ("db2", "b")]
        assert body["assetsRemaining"] == 0
        assert mocks["run_evaluation"].call_count == 2
        assert mocks["cascade"].call_count == 2

    def test_get_evaluations_lists_history_newest_first(self):
        response, mocks = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS),
                               evaluation_rows=[{"evaluationId": "e2"}, {"evaluationId": "e1"}])
        assert response["statusCode"] == 200
        assert [e["evaluationId"] for e in body_of(response)["evaluations"]] == ["e2", "e1"]
        query = mocks["evaluation"].query.call_args.kwargs
        assert query["IndexName"] == "AssetIndex"
        assert query["ScanIndexForward"] is False
        assert 1 <= query["Limit"] <= svc.DEFAULT_EVALUATIONS_PAGE_SIZE
        assert "NextToken" not in body_of(response)

    def test_get_state_for_an_asset(self):
        response, _ = _run(rest_event("GET", STATE_ASSET_PATH, ASSET_PARAMS),
                           compliance_record={"complianceState": "quarantined", "schemaName": SCHEMA})
        assert response["statusCode"] == 200
        assert body_of(response)["complianceState"] == "quarantined"

    def test_get_state_for_an_untracked_asset_is_unknown(self):
        response, _ = _run(rest_event("GET", STATE_ASSET_PATH, ASSET_PARAMS))
        assert response["statusCode"] == 200
        assert body_of(response) == {"databaseId": DB, "assetId": ASSET, "complianceState": "unknown",
                                     "schemaName": None, "schemaSource": None}

    def test_get_state_for_a_database(self):
        response, _ = _run(
            rest_event("GET", STATE_DB_PATH, DB_PARAMS),
            state_rows=[{"assetId": "a", "complianceState": "compliant"},
                        {"assetId": "b", "complianceState": "quarantined"},
                        {"assetId": "c", "complianceState": "weird"}])
        assert response["statusCode"] == 200
        body = body_of(response)
        assert body["totalAssets"] == 3
        assert body["summary"] == {"compliant": 1, "non_compliant": 0, "pending_evaluation": 0,
                                   "quarantined": 1, "unknown": 1}
        assert all(a["assetName"] == "Turbine" for a in body["assets"])

    @pytest.mark.parametrize("method,path", [
        ("GET", EVALUATE_PATH), ("GET", SWEEP_PATH), ("POST", EVALUATIONS_PATH),
        ("POST", STATE_DB_PATH), ("PUT", STATE_ASSET_PATH), ("DELETE", EVALUATE_PATH),
        ("GET", "/compliance/state"), ("POST", f"/compliance/evaluate/{DB}"),
    ])
    def test_an_unknown_method_or_path_is_refused(self, method, path):
        response, mocks = _run(rest_event(method, path, body={}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"
        mocks["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens", [(), (USER,)], ids=["empty-tokens", "api-denied"])
    def test_tier_one_denies(self, tokens):
        response, mocks = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={}),
                               tokens=tokens, api=False, compliance_record={"schemaName": SCHEMA})
        assert response["statusCode"] == 403
        mocks["run_evaluation"].assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", [
        ("POST", EVALUATE_PATH, ASSET_PARAMS, {}),
        ("POST", SWEEP_PATH, SCHEMA_PARAMS, {}),
        ("GET", EVALUATIONS_PATH, ASSET_PARAMS, None),
        ("GET", STATE_ASSET_PATH, ASSET_PARAMS, None),
        ("GET", STATE_DB_PATH, DB_PARAMS, None),
    ])
    def test_tier_two_denial_reads_and_writes_nothing(self, method, path, params, body):
        response, mocks = _run(rest_event(method, path, params, body=body), obj=False,
                               compliance_record={"schemaName": SCHEMA}, schema_item=schema_row(),
                               state_rows=[{"databaseId": DB, "assetId": "a"}])
        assert response["statusCode"] == 403
        mocks["run_evaluation"].assert_not_called()
        mocks["evaluation"].query.assert_not_called()
        mocks["state"].query.assert_not_called()

    def test_evaluate_checks_a_compliance_evaluation_object_for_the_database(self):
        instance = enforcer()
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{STORE}.get_asset_item", return_value={"assetId": ASSET}), \
                patch(f"{STORE}.get_compliance_record", return_value={"schemaName": SCHEMA}), \
                patch(f"{STORE}.run_evaluation", return_value=EVALUATION_RESULT), \
                patch(f"{MOD}.check_and_trigger_cascade"):
            svc.lambda_handler(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={}), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceEvaluation", "databaseId": DB, "complianceState": ""}, "POST")

    def test_sweep_checks_the_schema_object(self):
        instance = enforcer()
        state_table = MagicMock()
        state_table.query.return_value = {"Items": []}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{STORE}.load_schema_item", return_value=schema_row()):
            svc.lambda_handler(rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceSchema", "complianceSchemaName": SCHEMA}, "POST")

    def test_asset_state_check_carries_the_current_state(self):
        instance = enforcer()
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{STORE}.get_compliance_record",
                      return_value={"complianceState": "quarantined"}):
            svc.lambda_handler(rest_event("GET", STATE_ASSET_PATH, ASSET_PARAMS), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceEvaluation", "databaseId": DB,
             "complianceState": "quarantined"}, "GET")


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("method,path,params,body", [
        ("POST", f"/compliance/evaluate/x/{ASSET}", {"databaseId": "x", "assetId": ASSET}, {}),
        ("POST", f"/compliance/evaluate/{DB}/bad<id>", {"databaseId": DB, "assetId": "bad<id>"}, {}),
        ("POST", "/compliance/sweep/x", {"schemaName": "x"}, {}),
        ("GET", f"/compliance/evaluations/x/{ASSET}", {"databaseId": "x", "assetId": ASSET}, None),
        ("GET", f"/compliance/state/x/{ASSET}", {"databaseId": "x", "assetId": ASSET}, None),
        ("GET", "/compliance/state/x", {"databaseId": "x"}, None),
    ])
    def test_a_bad_path_parameter_is_rejected(self, method, path, params, body):
        response, mocks = _run(rest_event(method, path, params, body=body))
        assert response["statusCode"] == 400
        mocks["run_evaluation"].assert_not_called()
        mocks["state"].query.assert_not_called()
        mocks["evaluation"].query.assert_not_called()

    @pytest.mark.parametrize("body", [{"schemaName": "x"}, {"schemaName": "no spaces!"},
                                      {"schemaName": 7}])
    def test_a_bad_evaluate_body_is_rejected(self, body):
        response, mocks = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body=body))
        assert response["statusCode"] == 400
        mocks["run_evaluation"].assert_not_called()

    def test_a_body_that_is_not_json_is_rejected(self):
        response, _ = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body="nope"))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]

    def test_a_non_integer_page_size_is_rejected(self):
        response, mocks = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                          query_params={"maxItems": "ten"}))
        assert response["statusCode"] == 400
        mocks["evaluation"].query.assert_not_called()

    def test_a_malformed_pagination_token_is_rejected(self):
        response, mocks = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                          query_params={"startingToken": "!!not-base64!!"}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        mocks["evaluation"].query.assert_not_called()


@pytest.mark.unit
class TestEvaluateFlow:

    def test_a_missing_asset_is_refused(self):
        response, mocks = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={}),
                               asset_exists=False)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Asset not found"
        mocks["run_evaluation"].assert_not_called()

    def test_no_schema_anywhere_is_refused(self):
        response, mocks = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={}))
        assert response["statusCode"] == 400
        assert "no bound schema" in body_of(response)["message"]
        mocks["run_evaluation"].assert_not_called()

    def test_an_evaluation_error_is_reported_and_skips_the_cascade(self):
        response, mocks = _run(
            rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={}),
            compliance_record={"schemaName": SCHEMA},
            evaluation_result={"evaluationId": "e", "verdict": "error", "complianceState": "unknown",
                               "ruleResults": [], "pipelineRulesPending": 0,
                               "error": "Schema not found"})
        assert response["statusCode"] == 400
        assert "Schema not found" in body_of(response)["message"]
        mocks["cascade"].assert_not_called()

    def test_a_pending_pipeline_result_is_returned_as_such(self):
        pending = dict(EVALUATION_RESULT, verdict="pending_pipeline",
                       complianceState="pending_evaluation", pipelineRulesPending=1)
        response, _ = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body={}),
                           compliance_record={"schemaName": SCHEMA}, evaluation_result=pending)
        body = body_of(response)
        assert body["verdict"] == "pending_pipeline"
        assert body["pipelineRulesPending"] == 1


@pytest.mark.unit
class TestSweep:

    def test_a_missing_schema_is_refused(self):
        response, mocks = _run(rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Schema not found"
        mocks["state"].query.assert_not_called()

    def test_bound_assets_are_read_from_the_schema_name_index_to_exhaustion(self):
        state_table = MagicMock()
        state_table.query.side_effect = [
            {"Items": [{"databaseId": DB, "assetId": "a"}], "LastEvaluatedKey": {"k": 1}},
            {"Items": [{"databaseId": DB, "assetId": "b"}]},
        ]
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{STORE}.load_schema_item", return_value=schema_row()), \
                patch(f"{STORE}.run_evaluation", return_value=EVALUATION_RESULT) as run, \
                patch(f"{MOD}.check_and_trigger_cascade"):
            response = svc.lambda_handler(
                rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}), MagicMock())
        assert len(body_of(response)["assetsTriggered"]) == 2
        assert state_table.query.call_args_list[0].kwargs["IndexName"] == "SchemaNameIndex"
        assert state_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}
        assert run.call_count == 2

    def test_a_sweep_is_bounded_and_reports_the_remainder(self):
        rows = [{"databaseId": DB, "assetId": f"a{i}"} for i in range(svc.MAX_SWEEP_ASSETS + 3)]
        response, mocks = _run(rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}),
                               schema_item=schema_row(), state_rows=rows)
        body = body_of(response)
        assert len(body["assetsTriggered"]) == svc.MAX_SWEEP_ASSETS
        assert body["assetsRemaining"] == 3
        assert mocks["run_evaluation"].call_count == svc.MAX_SWEEP_ASSETS


@pytest.mark.unit
class TestListings:

    def test_the_evaluation_history_token_round_trips(self):
        last_key = {"databaseId:assetId": f"{DB}:{ASSET}", "evaluatedAt": "2026-01-01T00:00:00+00:00"}
        response, _ = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                      query_params={"maxItems": "1"}),
                           evaluation_rows=[{"evaluationId": "e2"}], evaluation_last_key=last_key)
        token = body_of(response)["NextToken"]
        assert json.loads(base64.b64decode(token)) == last_key

        response, mocks = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                          query_params={"maxItems": "1", "startingToken": token}),
                               evaluation_rows=[{"evaluationId": "e1"}])
        assert body_of(response)["evaluations"] == [{"evaluationId": "e1"}]
        query = mocks["evaluation"].query.call_args.kwargs
        assert query["ExclusiveStartKey"] == last_key
        assert query["Limit"] == 1

    def test_the_page_size_is_clamped(self):
        _, mocks = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                   query_params={"maxItems": "100000"}))
        assert 1 <= mocks["evaluation"].query.call_args.kwargs["Limit"] <= svc.MAX_EVALUATIONS_PAGE_SIZE
        _, mocks = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                   query_params={"maxItems": "0"}))
        assert mocks["evaluation"].query.call_args.kwargs["Limit"] == 1

    def test_the_database_overview_reads_its_rows_to_exhaustion(self):
        state_table = MagicMock()
        state_table.query.side_effect = [
            {"Items": [{"assetId": "a", "complianceState": "compliant"}], "LastEvaluatedKey": {"k": 1}},
            {"Items": [{"assetId": "b", "complianceState": "quarantined"}]},
        ]
        asset_table = MagicMock()
        asset_table.get_item.return_value = {}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{MOD}.asset_table", asset_table):
            response = svc.lambda_handler(rest_event("GET", STATE_DB_PATH, DB_PARAMS), MagicMock())
        body = body_of(response)
        assert body["totalAssets"] == 2
        assert body["summary"]["quarantined"] == 1
        assert [a["assetName"] for a in body["assets"]] == ["", ""]
        assert state_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}
