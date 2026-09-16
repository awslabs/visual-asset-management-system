# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceEvaluateService: dispatch of its five method+path pairs, both authorization tiers,
input validation, the evaluate / sweep flows over a patched store (a sweep evaluates only the bound
assets whose database the caller may evaluate), and the two paged listings (evaluation history behind
a LastEvaluatedKey token; the database overview behind an offset token over the full set)."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, SCHEMA, USER, body_of, claims_for, decode_token, encode_token, enforcer, rest_event,
    schema_row,
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

ALL_PATHS = [
    ("POST", EVALUATE_PATH, ASSET_PARAMS, {}),
    ("POST", SWEEP_PATH, SCHEMA_PARAMS, {}),
    ("GET", EVALUATIONS_PATH, ASSET_PARAMS, None),
    ("GET", STATE_ASSET_PATH, ASSET_PARAMS, None),
    ("GET", STATE_DB_PATH, DB_PARAMS, None),
]

EVALUATION_RESULT = {"evaluationId": "eval-1", "verdict": "compliant",
                     "complianceState": "compliant", "ruleResults": [], "pipelineRulesPending": 0}

OTHER_DB = "db2"


def _run(event, tokens=(USER,), api=True, obj=True, asset_exists=True, compliance_record=None,
         evaluation_result=None, schema_item=None, state_rows=None, evaluation_rows=None,
         evaluation_last_key=None, casbin=None):
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": list(state_rows or [])}
    evaluation_table = MagicMock(name="evaluation_table")
    page = {"Items": list(evaluation_rows or [])}
    if evaluation_last_key is not None:
        page["LastEvaluatedKey"] = evaluation_last_key
    evaluation_table.query.return_value = page
    asset_table = MagicMock(name="asset_table")
    asset_table.get_item.return_value = {"Item": {"assetName": "Turbine"}}
    casbin = casbin if casbin is not None else enforcer(api=api, obj=obj)
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=casbin), \
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
                      "run_evaluation": run_evaluation, "cascade": cascade, "casbin": casbin}


def _enforcer_denying_database(denied_database_id):
    """Tier-1 passes; Tier-2 grants every object except evaluation objects of one database."""
    instance = enforcer()
    instance.enforce.side_effect = (
        lambda obj, action: not (obj.get("object__type") == "complianceEvaluation"
                                 and obj.get("databaseId") == denied_database_id))
    return instance


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
            state_rows=[{"databaseId": DB, "assetId": "a"}, {"databaseId": OTHER_DB, "assetId": "b"}])
        assert response["statusCode"] == 200, response
        body = body_of(response)
        assert [(t["databaseId"], t["assetId"]) for t in body["assetsTriggered"]] == [
            (DB, "a"), (OTHER_DB, "b")]
        assert body["assetsRemaining"] == 0
        assert body["skipped"] == 0
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
                        {"assetId": "c", "complianceState": "weird"},
                        {"assetId": "d", "complianceState": "exception"}])
        assert response["statusCode"] == 200
        body = body_of(response)
        assert body["totalAssets"] == 4
        assert body["summary"] == {"compliant": 1, "non_compliant": 0, "pending_evaluation": 0,
                                   "quarantined": 1, "exception": 1, "unknown": 1}
        assert all(a["assetName"] == "Turbine" for a in body["assets"])
        assert "NextToken" not in body

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

    @pytest.mark.parametrize("method,path,params,body", ALL_PATHS)
    def test_tier_one_with_empty_tokens_denies_even_when_the_api_check_would_pass(
            self, method, path, params, body):
        response, mocks = _run(rest_event(method, path, params, body=body), tokens=(), api=True,
                               compliance_record={"schemaName": SCHEMA}, schema_item=schema_row(),
                               state_rows=[{"databaseId": DB, "assetId": "a"}])
        assert response["statusCode"] == 403
        mocks["casbin"].enforce.assert_not_called()
        mocks["run_evaluation"].assert_not_called()
        mocks["evaluation"].query.assert_not_called()
        mocks["state"].query.assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", ALL_PATHS)
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

    def test_sweep_checks_the_evaluation_object_of_each_bound_database_once(self):
        _, mocks = _run(
            rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}), schema_item=schema_row(),
            state_rows=[{"databaseId": DB, "assetId": "a"}, {"databaseId": DB, "assetId": "b"},
                        {"databaseId": OTHER_DB, "assetId": "c"}])
        calls = [c.args for c in mocks["casbin"].enforce.call_args_list]
        assert calls == [
            ({"object__type": "complianceSchema", "complianceSchemaName": SCHEMA}, "POST"),
            ({"object__type": "complianceEvaluation", "databaseId": DB, "complianceState": ""}, "POST"),
            ({"object__type": "complianceEvaluation", "databaseId": OTHER_DB, "complianceState": ""},
             "POST"),
        ]

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

    BAD_DB = "bad<database-id>"
    BAD_ASSET = "bad<asset-id>"
    BAD_SCHEMA = "bad<schema-name>"

    @pytest.mark.parametrize("method,path,params,body,bad", [
        ("POST", f"/compliance/evaluate/{BAD_DB}/{ASSET}", {"databaseId": BAD_DB, "assetId": ASSET},
         {}, BAD_DB),
        ("POST", f"/compliance/evaluate/{DB}/{BAD_ASSET}", {"databaseId": DB, "assetId": BAD_ASSET},
         {}, BAD_ASSET),
        ("POST", f"/compliance/sweep/{BAD_SCHEMA}", {"schemaName": BAD_SCHEMA}, {}, BAD_SCHEMA),
        ("GET", f"/compliance/evaluations/{BAD_DB}/{ASSET}", {"databaseId": BAD_DB, "assetId": ASSET},
         None, BAD_DB),
        ("GET", f"/compliance/state/{BAD_DB}/{ASSET}", {"databaseId": BAD_DB, "assetId": ASSET},
         None, BAD_DB),
        ("GET", f"/compliance/state/{BAD_DB}", {"databaseId": BAD_DB}, None, BAD_DB),
    ])
    def test_a_bad_path_parameter_is_rejected(self, method, path, params, body, bad):
        response, mocks = _run(rest_event(method, path, params, body=body))
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        mocks["run_evaluation"].assert_not_called()
        mocks["state"].query.assert_not_called()
        mocks["evaluation"].query.assert_not_called()

    @pytest.mark.parametrize("body,bad", [
        ({"schemaName": "zq"}, "zq"), ({"schemaName": "no spaces!"}, "no spaces!"),
        ({"schemaName": 7}, None),
    ], ids=["short", "invalid-chars", "not-a-string"])
    def test_a_bad_evaluate_body_is_rejected(self, body, bad):
        response, mocks = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body=body))
        assert response["statusCode"] == 400
        if bad is not None:
            assert bad not in response["body"]
        mocks["run_evaluation"].assert_not_called()

    def test_a_body_that_is_not_json_is_rejected(self):
        response, _ = _run(rest_event("POST", EVALUATE_PATH, ASSET_PARAMS, body="nope"))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]

    @pytest.mark.parametrize("path,params", [(EVALUATIONS_PATH, ASSET_PARAMS), (STATE_DB_PATH, DB_PARAMS)])
    def test_a_non_integer_page_size_is_rejected(self, path, params):
        response, mocks = _run(rest_event("GET", path, params, query_params={"maxItems": "ten"}))
        assert response["statusCode"] == 400
        assert "ten" not in response["body"]
        mocks["evaluation"].query.assert_not_called()
        mocks["state"].query.assert_not_called()

    @pytest.mark.parametrize("token", ["!!not-base64!!", encode_token([1]), encode_token("k"),
                                       encode_token({})],
                             ids=["not-base64", "list", "string", "empty-object"])
    def test_a_malformed_evaluations_pagination_token_is_rejected(self, token):
        response, mocks = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                          query_params={"startingToken": token}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        mocks["evaluation"].query.assert_not_called()

    @pytest.mark.parametrize("token", [
        "!!not-base64!!", encode_token([1]), encode_token("offset"), encode_token({}),
        encode_token({"offset": -1}), encode_token({"offset": "3"}), encode_token({"offset": True}),
        encode_token({"offset": 1.5}),
    ], ids=["not-base64", "list", "string", "no-offset", "negative", "string-offset", "bool",
            "float"])
    def test_a_malformed_overview_pagination_token_is_rejected(self, token):
        response, mocks = _run(rest_event("GET", STATE_DB_PATH, DB_PARAMS,
                                          query_params={"startingToken": token}),
                               state_rows=[{"assetId": "a", "complianceState": "compliant"}])
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        mocks["state"].query.assert_not_called()


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
        assert body["skipped"] == 0
        assert mocks["run_evaluation"].call_count == svc.MAX_SWEEP_ASSETS

    def test_assets_in_a_database_the_caller_may_not_evaluate_are_skipped_counted_and_not_listed(self):
        """A schema grant does not reach assets in another database: those are neither evaluated
        nor cascaded, are counted under `skipped`, and their ids are absent from the response."""
        response, mocks = _run(
            rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}), schema_item=schema_row(),
            casbin=_enforcer_denying_database(OTHER_DB),
            state_rows=[{"databaseId": DB, "assetId": "a"},
                        {"databaseId": OTHER_DB, "assetId": "hidden-1"},
                        {"databaseId": DB, "assetId": "b"},
                        {"databaseId": OTHER_DB, "assetId": "hidden-2"}])
        assert response["statusCode"] == 200, response
        body = body_of(response)
        assert [(t["databaseId"], t["assetId"]) for t in body["assetsTriggered"]] == [(DB, "a"), (DB, "b")]
        assert body["skipped"] == 2
        assert body["assetsRemaining"] == 0
        assert OTHER_DB not in response["body"]
        assert "hidden-1" not in response["body"]
        assert "hidden-2" not in response["body"]
        assert [c.args[:2] for c in mocks["run_evaluation"].call_args_list] == [(DB, "a"), (DB, "b")]
        assert [c.args for c in mocks["cascade"].call_args_list] == [(DB, "a"), (DB, "b")]

    def test_a_denied_database_is_not_reported_as_remaining(self):
        response, mocks = _run(
            rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}), schema_item=schema_row(),
            casbin=_enforcer_denying_database(OTHER_DB),
            state_rows=[{"databaseId": OTHER_DB, "assetId": f"h{i}"} for i in range(3)])
        body = body_of(response)
        assert body["assetsTriggered"] == []
        assert body["skipped"] == 3
        assert body["assetsRemaining"] == 0
        mocks["run_evaluation"].assert_not_called()

    def test_skipped_assets_do_not_consume_the_sweep_bound(self):
        rows = [{"databaseId": OTHER_DB, "assetId": "hidden"}]
        rows += [{"databaseId": DB, "assetId": f"a{i}"} for i in range(svc.MAX_SWEEP_ASSETS + 1)]
        response, mocks = _run(rest_event("POST", SWEEP_PATH, SCHEMA_PARAMS, body={}),
                               schema_item=schema_row(), casbin=_enforcer_denying_database(OTHER_DB),
                               state_rows=rows)
        body = body_of(response)
        assert len(body["assetsTriggered"]) == svc.MAX_SWEEP_ASSETS
        assert body["skipped"] == 1
        assert body["assetsRemaining"] == 1


@pytest.mark.unit
class TestListings:

    def test_the_evaluation_history_token_round_trips(self):
        last_key = {"databaseId:assetId": f"{DB}:{ASSET}", "evaluatedAt": "2026-01-01T00:00:00+00:00"}
        response, _ = _run(rest_event("GET", EVALUATIONS_PATH, ASSET_PARAMS,
                                      query_params={"maxItems": "1"}),
                           evaluation_rows=[{"evaluationId": "e2"}], evaluation_last_key=last_key)
        token = body_of(response)["NextToken"]
        assert decode_token(token) == last_key

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


@pytest.mark.unit
class TestOverviewPaging:
    """The database overview pages its rows with maxItems / startingToken / NextToken while the
    summary and total keep covering the full set; only the page's rows are enriched."""

    ROWS = [{"assetId": f"asset-{i}", "complianceState": state}
            for i, state in zip((3, 0, 4, 1, 2), ("compliant", "quarantined", "compliant",
                                                  "non_compliant", "pending_evaluation"))]
    SUMMARY = {"compliant": 2, "non_compliant": 1, "pending_evaluation": 1, "quarantined": 1,
               "exception": 0, "unknown": 0}

    def _page(self, query_params):
        response, mocks = _run(rest_event("GET", STATE_DB_PATH, DB_PARAMS, query_params=query_params),
                               state_rows=[dict(r) for r in self.ROWS])
        assert response["statusCode"] == 200, response
        return body_of(response), mocks

    def test_the_token_round_trips_and_the_pages_partition_the_full_set(self):
        page_one, mocks = self._page({"maxItems": "2"})
        assert [a["assetId"] for a in page_one["assets"]] == ["asset-0", "asset-1"]
        assert page_one["totalAssets"] == 5
        assert page_one["summary"] == self.SUMMARY
        assert decode_token(page_one["NextToken"]) == {"offset": 2}
        assert mocks["asset"].get_item.call_count == 2

        page_two, _ = self._page({"maxItems": "2", "startingToken": page_one["NextToken"]})
        assert [a["assetId"] for a in page_two["assets"]] == ["asset-2", "asset-3"]
        assert page_two["totalAssets"] == 5
        assert page_two["summary"] == self.SUMMARY

        page_three, _ = self._page({"maxItems": "2", "startingToken": page_two["NextToken"]})
        assert [a["assetId"] for a in page_three["assets"]] == ["asset-4"]
        assert "NextToken" not in page_three

        union = page_one["assets"] + page_two["assets"] + page_three["assets"]
        assert sorted(a["assetId"] for a in union) == sorted(r["assetId"] for r in self.ROWS)
        assert all(a["assetName"] == "Turbine" for a in union)

    def test_a_page_that_ends_exactly_on_the_set_has_no_token(self):
        page, _ = self._page({"maxItems": "5"})
        assert len(page["assets"]) == 5
        assert "NextToken" not in page

    def test_an_offset_past_the_end_yields_an_empty_page_with_the_full_summary(self):
        page, mocks = self._page({"startingToken": encode_token({"offset": 50})})
        assert page["assets"] == []
        assert page["totalAssets"] == 5
        assert page["summary"] == self.SUMMARY
        assert "NextToken" not in page
        mocks["asset"].get_item.assert_not_called()

    def test_the_page_size_is_clamped_to_the_named_bounds(self):
        page, _ = self._page({"maxItems": "0"})
        assert len(page["assets"]) == 1
        assert decode_token(page["NextToken"]) == {"offset": 1}
        assert svc.DEFAULT_OVERVIEW_PAGE_SIZE == 100
        assert svc.MAX_OVERVIEW_PAGE_SIZE == 500
        page, _ = self._page({"maxItems": "100000"})
        assert len(page["assets"]) == 5
