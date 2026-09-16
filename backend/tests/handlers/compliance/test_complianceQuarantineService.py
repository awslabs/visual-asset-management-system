# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceQuarantineService: dispatch of its four method+path pairs, both authorization tiers,
input validation, the state transitions out of quarantine (release clears the quarantine; exception
records who granted it and why, scoped to the bound schema's current version; both refuse an asset
that is not quarantined), the revocation of an exception (back to the last verdict's state, or to
pending_evaluation), and the listing, which pages the ComplianceStateIndex GSI behind a
round-tripping token."""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance._harness import (
    ASSET, COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, DB, SCHEMA, USER, body_of, claims_for,
    decode_token, encode_token, enforcer, rest_event, schema_row,
)
from handlers.compliance import complianceQuarantineService as svc

MOD = "handlers.compliance.complianceQuarantineService"
STORE = "handlers.compliance.complianceEvaluationStore"
NOTIFICATIONS = "handlers.compliance.complianceNotifications"

LIST_PATH = "/compliance/quarantine"
RELEASE_PATH = f"/compliance/quarantine/{DB}/{ASSET}/release"
EXCEPTION_PATH = f"/compliance/quarantine/{DB}/{ASSET}/exception"
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}
QUARANTINED = {"databaseId": DB, "assetId": ASSET, "complianceState": "quarantined",
               "schemaName": SCHEMA, "lastEvaluationId": "eval-1"}
EXCEPTED = {"databaseId": DB, "assetId": ASSET, "complianceState": "exception", "schemaName": SCHEMA,
            "lastEvaluationId": "eval-1", "exceptionGranted": True, "exceptionReason": "legacy",
            "exceptionGrantedBy": USER, "exceptionGrantedAt": "2026-01-01T00:00:00+00:00",
            "exceptionSchemaName": SCHEMA, "exceptionSchemaVersion": 3}

ALL_PATHS = [
    ("GET", LIST_PATH, None, None),
    ("POST", RELEASE_PATH, ASSET_PARAMS, {}),
    ("POST", EXCEPTION_PATH, ASSET_PARAMS, {"reason": "r"}),
    ("DELETE", EXCEPTION_PATH, ASSET_PARAMS, None),
]


def _evaluation(verdict, violations=("owner missing",)):
    """An evaluation row as the store writes it, with one failed rule per violation."""
    rule_results = [{"ruleName": f"rule-{i}", "ruleType": "metadata", "passed": False,
                     "enforcement": "quarantine", "message": message}
                    for i, message in enumerate(violations)]
    return {"evaluationId": "eval-1", "databaseId": DB, "assetId": ASSET, "schemaName": SCHEMA,
            "verdict": verdict, "ruleResults": json.dumps(rule_results)}


def _run(event, tokens=(USER,), api=True, obj=True, compliance_record=None, quarantined_rows=None,
         last_evaluated_key=None, casbin=None, state_table=None, schema_item=None, evaluation=None,
         latest_verdict_evaluation=None):
    if state_table is None:
        state_table = MagicMock(name="asset_state_table")
        page = {"Items": list(quarantined_rows or [])}
        if last_evaluated_key is not None:
            page["LastEvaluatedKey"] = last_evaluated_key
        state_table.query.return_value = page
    asset_table = MagicMock(name="asset_table")
    asset_table.get_item.return_value = {"Item": {"assetName": "Turbine"}}
    casbin = casbin if casbin is not None else enforcer(api=api, obj=obj)
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=casbin), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.load_schema_item", return_value=schema_item) as load_schema, \
            patch(f"{STORE}.get_evaluation", return_value=evaluation) as get_evaluation, \
            patch(f"{STORE}.latest_verdict_evaluation",
                  return_value=latest_verdict_evaluation) as latest_verdict, \
            patch(f"{STORE}.update_asset_state") as update_state, \
            patch(f"{STORE}.write_audit") as write_audit, \
            patch(f"{NOTIFICATIONS}.notify_quarantine") as notify:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"state": state_table, "asset": asset_table, "update_state": update_state,
                      "audit": write_audit, "casbin": casbin, "load_schema": load_schema,
                      "get_evaluation": get_evaluation, "latest_verdict": latest_verdict,
                      "notify": notify}


@pytest.mark.unit
class TestRouteDispatch:

    def test_get_lists_quarantined_assets_from_the_compliance_state_index(self):
        rows = [dict(QUARANTINED, assetId="a"), dict(QUARANTINED, databaseId="db2", assetId="b")]
        response, mocks = _run(rest_event("GET", LIST_PATH), quarantined_rows=rows)
        assert response["statusCode"] == 200
        listed = body_of(response)["quarantinedAssets"]
        assert [(r["databaseId"], r["assetId"]) for r in listed] == [(DB, "a"), ("db2", "b")]
        assert all(r["assetName"] == "Turbine" for r in listed)
        assert "NextToken" not in body_of(response)
        query = mocks["state"].query.call_args.kwargs
        assert mocks["state"].query.call_count == 1
        assert query["IndexName"] == "ComplianceStateIndex"
        assert query["KeyConditionExpression"]._values[0].name == "complianceState"
        assert query["KeyConditionExpression"]._values[1] == "quarantined"
        assert 0 < query["Limit"] <= svc.DEFAULT_LIST_PAGE_SIZE
        assert "ExclusiveStartKey" not in query

    def test_post_release_reaches_release(self):
        response, _ = _run(rest_event("POST", RELEASE_PATH, ASSET_PARAMS, body={}),
                           compliance_record=QUARANTINED)
        assert response["statusCode"] == 200, response
        assert "released from quarantine" in body_of(response)["message"]

    def test_post_exception_reaches_grant_exception(self):
        response, _ = _run(rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body={"reason": "ok"}),
                           compliance_record=QUARANTINED)
        assert response["statusCode"] == 200, response
        assert body_of(response)["grantedBy"] == USER

    def test_delete_exception_reaches_revoke_exception(self):
        response, _ = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                           compliance_record=EXCEPTED)
        assert response["statusCode"] == 200, response
        assert body_of(response)["message"] == "Exception revoked"

    @pytest.mark.parametrize("method,path", [
        ("POST", LIST_PATH), ("GET", RELEASE_PATH), ("DELETE", RELEASE_PATH), ("GET", EXCEPTION_PATH),
        ("PUT", EXCEPTION_PATH), ("DELETE", LIST_PATH),
        ("POST", f"/compliance/quarantine/{DB}/{ASSET}"), ("PUT", LIST_PATH),
        ("DELETE", f"/compliance/quarantine/{DB}/{ASSET}"),
        ("POST", f"/compliance/quarantine/{DB}/{ASSET}/pardon"),
    ])
    def test_an_unknown_method_or_path_is_refused(self, method, path):
        response, mocks = _run(rest_event(method, path, ASSET_PARAMS, body={}),
                               compliance_record=dict(EXCEPTED, complianceState="quarantined"))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"
        mocks["update_state"].assert_not_called()


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens", [(), (USER,)], ids=["empty-tokens", "api-denied"])
    def test_tier_one_denies(self, tokens):
        response, mocks = _run(rest_event("GET", LIST_PATH), tokens=tokens, api=False)
        assert response["statusCode"] == 403
        mocks["state"].query.assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", ALL_PATHS)
    def test_tier_one_with_empty_tokens_denies_even_when_the_api_check_would_pass(
            self, method, path, params, body):
        response, mocks = _run(rest_event(method, path, params, body=body), tokens=(), api=True,
                               compliance_record=EXCEPTED, quarantined_rows=[QUARANTINED])
        assert response["statusCode"] == 403
        mocks["casbin"].enforce.assert_not_called()
        mocks["state"].query.assert_not_called()
        mocks["update_state"].assert_not_called()
        mocks["audit"].assert_not_called()

    @pytest.mark.parametrize("method,path,body,record", [
        ("POST", RELEASE_PATH, {}, QUARANTINED),
        ("POST", EXCEPTION_PATH, {"reason": "r"}, QUARANTINED),
        ("DELETE", EXCEPTION_PATH, None, EXCEPTED),
    ], ids=["release", "grant", "revoke"])
    def test_tier_two_denial_changes_nothing(self, method, path, body, record):
        response, mocks = _run(rest_event(method, path, ASSET_PARAMS, body=body), obj=False,
                               compliance_record=record)
        assert response["statusCode"] == 403
        mocks["update_state"].assert_not_called()
        mocks["audit"].assert_not_called()
        mocks["notify"].assert_not_called()

    @pytest.mark.parametrize("method,path,body", [
        ("POST", RELEASE_PATH, {}), ("POST", EXCEPTION_PATH, {"reason": "r"}),
        ("DELETE", EXCEPTION_PATH, None),
    ], ids=["release", "grant", "revoke"])
    def test_the_business_function_denies_an_empty_token_list_before_reading_the_row(
            self, method, path, body):
        # The guard is a statement of its own in each function: reached with no identity, the row is
        # never read and Casbin is never consulted.
        casbin = enforcer()
        with patch(f"{MOD}.claims_and_roles", {"tokens": [], "roles": []}), \
                patch(f"{MOD}.CasbinEnforcer", return_value=casbin), \
                patch(f"{STORE}.get_compliance_record") as read_row, \
                patch(f"{STORE}.update_asset_state") as update_state:
            event = rest_event(method, path, ASSET_PARAMS, body=body)
            if method == "DELETE":
                response = svc.revoke_exception(event, DB, ASSET)
            elif path == RELEASE_PATH:
                response = svc.release_quarantine(event, DB, ASSET, svc.ReleaseQuarantineRequestModel())
            else:
                response = svc.grant_exception(
                    event, DB, ASSET, svc.GrantExceptionRequestModel(reason="r"))
        assert response["statusCode"] == 403
        casbin.enforce.assert_not_called()
        read_row.assert_not_called()
        update_state.assert_not_called()

    def test_the_listing_filters_to_what_the_caller_may_get(self):
        response, mocks = _run(rest_event("GET", LIST_PATH), obj=False,
                               quarantined_rows=[QUARANTINED])
        assert response["statusCode"] == 200
        assert body_of(response)["quarantinedAssets"] == []
        mocks["asset"].get_item.assert_not_called()

    def test_the_listing_filters_by_the_row_database(self):
        casbin = enforcer()
        casbin.enforce.side_effect = lambda obj, action: obj["databaseId"] == DB
        rows = [dict(QUARANTINED, assetId="mine"),
                dict(QUARANTINED, databaseId="hidden-db", assetId="theirs")]
        response, _ = _run(rest_event("GET", LIST_PATH), casbin=casbin, quarantined_rows=rows)
        assert [r["assetId"] for r in body_of(response)["quarantinedAssets"]] == ["mine"]
        assert "hidden-db" not in response["body"]
        assert "theirs" not in response["body"]
        assert casbin.enforce.call_args_list[1].args == (
            {"object__type": "complianceEvaluation", "databaseId": "hidden-db",
             "complianceState": "quarantined"}, "GET")

    def test_a_fully_filtered_page_still_carries_the_token(self):
        """The Casbin filter applies to the page after the read, so a page the caller may see
        nothing of is empty while NextToken says more rows exist."""
        last_key = {"complianceState": "quarantined", "databaseId": "hidden-db", "assetId": "z"}
        response, _ = _run(rest_event("GET", LIST_PATH), obj=False,
                           quarantined_rows=[dict(QUARANTINED, databaseId="hidden-db")],
                           last_evaluated_key=last_key)
        body = body_of(response)
        assert body["quarantinedAssets"] == []
        assert decode_token(body["NextToken"]) == last_key

    def test_release_checks_a_quarantined_evaluation_object_for_the_database(self):
        instance = enforcer()
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{STORE}.get_compliance_record", return_value=QUARANTINED), \
                patch(f"{STORE}.update_asset_state"), patch(f"{STORE}.write_audit"):
            svc.lambda_handler(rest_event("POST", RELEASE_PATH, ASSET_PARAMS, body={}), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceEvaluation", "databaseId": DB,
             "complianceState": "quarantined"}, "POST")

    def test_revoke_checks_an_exception_evaluation_object_for_the_database_with_delete(self):
        _, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS), compliance_record=EXCEPTED,
                        evaluation=_evaluation("compliant"))
        mocks["casbin"].enforce.assert_called_once_with(
            {"object__type": "complianceEvaluation", "databaseId": DB,
             "complianceState": "exception"}, "DELETE")


@pytest.mark.unit
class TestValidation:

    BAD_DB = "bad<database-id>"
    BAD_ASSET = "bad<asset-id>"

    @pytest.mark.parametrize("method,path,params,bad", [
        ("POST", f"/compliance/quarantine/{BAD_DB}/{ASSET}/release",
         {"databaseId": BAD_DB, "assetId": ASSET}, BAD_DB),
        ("POST", f"/compliance/quarantine/{DB}/{BAD_ASSET}/release",
         {"databaseId": DB, "assetId": BAD_ASSET}, BAD_ASSET),
        ("POST", f"/compliance/quarantine/{BAD_DB}/{ASSET}/exception",
         {"databaseId": BAD_DB, "assetId": ASSET}, BAD_DB),
        ("DELETE", f"/compliance/quarantine/{BAD_DB}/{ASSET}/exception",
         {"databaseId": BAD_DB, "assetId": ASSET}, BAD_DB),
        ("DELETE", f"/compliance/quarantine/{DB}/{BAD_ASSET}/exception",
         {"databaseId": DB, "assetId": BAD_ASSET}, BAD_ASSET),
    ])
    def test_a_bad_path_parameter_is_rejected(self, method, path, params, bad):
        response, mocks = _run(rest_event(method, path, params, body={"reason": "r"}),
                               compliance_record=EXCEPTED)
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        mocks["update_state"].assert_not_called()
        mocks["casbin"].enforce.assert_not_called()

    @pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 1025}],
                             ids=["missing", "empty", "blank", "too-long"])
    def test_an_exception_needs_a_reason(self, body):
        response, mocks = _run(rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body=body),
                               compliance_record=QUARANTINED)
        assert response["statusCode"] == 400
        assert "x" * 1025 not in response["body"]
        mocks["update_state"].assert_not_called()

    def test_a_body_that_is_not_json_is_rejected(self):
        response, _ = _run(rest_event("POST", RELEASE_PATH, ASSET_PARAMS, body="{"))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]

    def test_release_with_no_body_uses_the_default_reason(self):
        response, mocks = _run(rest_event("POST", RELEASE_PATH, ASSET_PARAMS),
                               compliance_record=QUARANTINED)
        assert response["statusCode"] == 200
        assert mocks["audit"].call_args.kwargs["details"] == {"reason": "released via API"}

    def test_a_bad_page_size_is_rejected(self):
        response, mocks = _run(rest_event("GET", LIST_PATH, query_params={"maxItems": "ten"}))
        assert response["statusCode"] == 400
        assert "ten" not in response["body"]
        mocks["state"].query.assert_not_called()

    @pytest.mark.parametrize("token", [
        "!!not-base64!!", encode_token([1]), encode_token("key"), encode_token({}),
        encode_token({"complianceState": "quarantined"}),
        encode_token({"databaseId:assetId": "x:y", "evaluatedAt": "t"}),
        encode_token({"complianceState": "quarantined", "databaseId": DB, "assetId": 7}),
    ], ids=["not-base64", "list", "string", "empty-object", "partial-key", "other-listings-key",
            "non-string-attribute"])
    def test_a_malformed_pagination_token_is_rejected_before_any_read(self, token):
        response, mocks = _run(rest_event("GET", LIST_PATH, query_params={"startingToken": token}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        mocks["state"].query.assert_not_called()


@pytest.mark.unit
class TestStateTransitions:

    def test_release_moves_quarantined_to_compliant_and_clears_the_quarantine(self):
        response, mocks = _run(
            rest_event("POST", RELEASE_PATH, ASSET_PARAMS, body={"reason": "  reviewed  "}),
            compliance_record=QUARANTINED)
        assert response["statusCode"] == 200
        database_id, asset_id, updates = mocks["update_state"].call_args.args
        assert (database_id, asset_id) == (DB, ASSET)
        assert updates["complianceState"] == "compliant"
        assert updates["quarantineReason"] is None
        assert updates["exceptionGranted"] is False
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "quarantine_released"
        assert audit["actor"] == USER
        assert audit["details"] == {"reason": "reviewed"}
        assert (audit["previous_state"], audit["new_state"]) == ("quarantined", "compliant")

    def test_exception_moves_quarantined_to_exception_scoped_to_the_bound_schema_version(self):
        response, mocks = _run(
            rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body={"reason": " legacy data "}),
            compliance_record=QUARANTINED, schema_item=schema_row(SCHEMA, version=3))
        assert response["statusCode"] == 200
        body = body_of(response)
        assert body["reason"] == "legacy data"
        assert body["complianceState"] == "exception"
        updates = mocks["update_state"].call_args.args[2]
        assert updates["complianceState"] == "exception"
        assert updates["exceptionGranted"] is True
        assert updates["exceptionReason"] == "legacy data"
        assert updates["exceptionGrantedBy"] == USER
        assert updates["exceptionGrantedAt"] == updates["updatedAt"]
        assert updates["exceptionSchemaName"] == SCHEMA
        assert updates["exceptionSchemaVersion"] == 3
        assert updates["quarantineReason"] is None
        mocks["load_schema"].assert_called_once_with(SCHEMA)
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "exception_granted"
        assert audit["schema_name"] == SCHEMA
        assert (audit["previous_state"], audit["new_state"]) == ("quarantined", "exception")

    def test_exception_on_an_asset_whose_schema_is_gone_records_no_version(self):
        _, mocks = _run(rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body={"reason": "r"}),
                        compliance_record=QUARANTINED, schema_item=None)
        updates = mocks["update_state"].call_args.args[2]
        assert updates["complianceState"] == "exception"
        assert updates["exceptionSchemaName"] == SCHEMA
        assert updates["exceptionSchemaVersion"] is None

    def test_the_schema_tables_decimal_version_is_stored_as_an_int(self):
        from decimal import Decimal
        _, mocks = _run(rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body={"reason": "r"}),
                        compliance_record=QUARANTINED,
                        schema_item=dict(schema_row(SCHEMA), internalVersion=Decimal("4")))
        updates = mocks["update_state"].call_args.args[2]
        assert updates["exceptionSchemaVersion"] == 4
        assert type(updates["exceptionSchemaVersion"]) is int
        json.dumps(mocks["audit"].call_args.kwargs["details"])

    def test_exception_on_an_asset_with_no_bound_schema_reads_no_schema(self):
        _, mocks = _run(rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body={"reason": "r"}),
                        compliance_record={k: v for k, v in QUARANTINED.items() if k != "schemaName"})
        updates = mocks["update_state"].call_args.args[2]
        assert updates["exceptionSchemaName"] is None
        assert updates["exceptionSchemaVersion"] is None
        mocks["load_schema"].assert_not_called()

    @pytest.mark.parametrize("path,body", [(RELEASE_PATH, {}), (EXCEPTION_PATH, {"reason": "r"})])
    @pytest.mark.parametrize("state", ["compliant", "non_compliant", "pending_evaluation", "unknown",
                                       "exception"])
    def test_an_asset_that_is_not_quarantined_is_refused(self, path, body, state):
        response, mocks = _run(rest_event("POST", path, ASSET_PARAMS, body=body),
                               compliance_record=dict(QUARANTINED, complianceState=state))
        assert response["statusCode"] == 400
        assert "not quarantined" in body_of(response)["message"]
        mocks["update_state"].assert_not_called()
        mocks["audit"].assert_not_called()

    @pytest.mark.parametrize("method,path,body", [
        ("POST", RELEASE_PATH, {}), ("POST", EXCEPTION_PATH, {"reason": "r"}),
        ("DELETE", EXCEPTION_PATH, None),
    ], ids=["release", "grant", "revoke"])
    def test_an_untracked_asset_is_refused(self, method, path, body):
        response, mocks = _run(rest_event(method, path, ASSET_PARAMS, body=body))
        assert response["statusCode"] == 400
        assert "not found in compliance tracking" in body_of(response)["message"]
        mocks["update_state"].assert_not_called()


@pytest.mark.unit
class TestRevokeException:
    """DELETE on the exception path: the exception fields are cleared and the asset returns to the
    state of its last evaluation's verdict."""

    CLEARED = {"exceptionReason": None, "exceptionGrantedBy": None, "exceptionGrantedAt": None,
               "exceptionSchemaName": None, "exceptionSchemaVersion": None}

    def test_a_quarantined_last_verdict_re_quarantines_with_the_reason_and_notifies(self):
        response, mocks = _run(
            rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS), compliance_record=EXCEPTED,
            evaluation=_evaluation("quarantined", violations=("owner missing", "no parent")))
        assert response["statusCode"] == 200
        assert body_of(response) == {"message": "Exception revoked", "databaseId": DB,
                                     "assetId": ASSET, "complianceState": "quarantined"}
        mocks["get_evaluation"].assert_called_once_with("eval-1")
        database_id, asset_id, updates = mocks["update_state"].call_args.args
        assert (database_id, asset_id) == (DB, ASSET)
        assert updates["complianceState"] == "quarantined"
        assert updates["exceptionGranted"] is False
        assert {k: updates[k] for k in self.CLEARED} == self.CLEARED
        assert updates["quarantineReason"] == "owner missing; no parent"
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "exception_revoked"
        assert audit["actor"] == USER
        assert audit["evaluation_id"] == "eval-1"
        assert audit["schema_name"] == SCHEMA
        assert (audit["previous_state"], audit["new_state"]) == ("exception", "quarantined")
        mocks["notify"].assert_called_once_with(DB, ASSET, SCHEMA, ["rule-0", "rule-1"])

    @pytest.mark.parametrize("verdict,state", [
        ("compliant", "compliant"), ("non_compliant", "non_compliant"),
    ])
    def test_any_other_last_verdict_maps_to_its_state_without_a_quarantine(self, verdict, state):
        response, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                               compliance_record=EXCEPTED, evaluation=_evaluation(verdict))
        assert body_of(response)["complianceState"] == state
        mocks["latest_verdict"].assert_not_called()
        updates = mocks["update_state"].call_args.args[2]
        assert updates["complianceState"] == state
        assert updates["quarantineReason"] is None
        assert updates["exceptionGranted"] is False
        assert mocks["audit"].call_args.kwargs["new_state"] == state
        mocks["notify"].assert_not_called()

    @pytest.mark.parametrize("verdict", ["pending_pipeline", "error", "not-a-verdict"])
    def test_a_last_evaluation_without_a_verdict_falls_back_to_the_newest_one_that_has_one(self, verdict):
        response, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                               compliance_record=EXCEPTED, evaluation=_evaluation(verdict),
                               latest_verdict_evaluation=_evaluation("quarantined"))
        assert body_of(response)["complianceState"] == "quarantined"
        mocks["latest_verdict"].assert_called_once_with(DB, ASSET)
        assert mocks["update_state"].call_args.args[2]["complianceState"] == "quarantined"
        mocks["notify"].assert_called_once()

    @pytest.mark.parametrize("verdict", ["pending_pipeline", "error", "not-a-verdict"])
    def test_a_last_evaluation_without_a_verdict_and_no_earlier_verdict_returns_to_pending(self, verdict):
        response, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                               compliance_record=EXCEPTED, evaluation=_evaluation(verdict))
        state = "pending_evaluation"
        assert body_of(response)["complianceState"] == state
        updates = mocks["update_state"].call_args.args[2]
        assert updates["complianceState"] == state
        assert updates["quarantineReason"] is None
        assert updates["exceptionGranted"] is False
        assert mocks["audit"].call_args.kwargs["new_state"] == state
        mocks["notify"].assert_not_called()

    @pytest.mark.parametrize("record", [
        {k: v for k, v in EXCEPTED.items() if k != "lastEvaluationId"},
        dict(EXCEPTED, lastEvaluationId=""),
    ], ids=["no-last-evaluation", "empty-last-evaluation"])
    def test_an_asset_never_evaluated_returns_to_pending_evaluation(self, record):
        response, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                               compliance_record=record)
        assert body_of(response)["complianceState"] == "pending_evaluation"
        mocks["get_evaluation"].assert_not_called()
        updates = mocks["update_state"].call_args.args[2]
        assert updates["complianceState"] == "pending_evaluation"
        assert updates["exceptionGranted"] is False
        assert {k: updates[k] for k in self.CLEARED} == self.CLEARED
        mocks["notify"].assert_not_called()

    def test_a_last_evaluation_row_that_no_longer_exists_returns_to_pending_evaluation(self):
        response, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                               compliance_record=EXCEPTED, evaluation=None)
        assert body_of(response)["complianceState"] == "pending_evaluation"
        mocks["get_evaluation"].assert_called_once_with("eval-1")

    @pytest.mark.parametrize("record", [
        QUARANTINED, dict(QUARANTINED, exceptionGranted=False),
        dict(QUARANTINED, complianceState="compliant"),
        dict(EXCEPTED, exceptionGranted=False),
    ], ids=["never-granted", "granted-false", "compliant", "already-revoked"])
    def test_revoking_without_an_active_exception_is_refused(self, record):
        response, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                               compliance_record=record, evaluation=_evaluation("quarantined"))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "No active exception"
        mocks["update_state"].assert_not_called()
        mocks["audit"].assert_not_called()
        mocks["notify"].assert_not_called()

    def test_a_failing_notification_does_not_undo_the_revoke(self):
        state_table = MagicMock(name="asset_state_table")
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{STORE}.get_compliance_record", return_value=EXCEPTED), \
                patch(f"{STORE}.get_evaluation", return_value=_evaluation("quarantined")), \
                patch(f"{STORE}.update_asset_state") as update_state, \
                patch(f"{STORE}.write_audit") as write_audit, \
                patch(f"{NOTIFICATIONS}.notify_quarantine", side_effect=RuntimeError("sns down")):
            response = svc.lambda_handler(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                                          MagicMock())
        assert response["statusCode"] == 200
        assert body_of(response)["complianceState"] == "quarantined"
        update_state.assert_called_once()
        write_audit.assert_called_once()

    def test_the_revoke_needs_no_body(self):
        response, _ = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS, body="{"),
                           compliance_record=EXCEPTED, evaluation=_evaluation("compliant"))
        assert response["statusCode"] == 200

    def test_the_stored_decimal_version_is_audited_as_a_json_encodable_int(self):
        from decimal import Decimal
        _, mocks = _run(rest_event("DELETE", EXCEPTION_PATH, ASSET_PARAMS),
                        compliance_record=dict(EXCEPTED, exceptionSchemaVersion=Decimal("3")),
                        evaluation=_evaluation("compliant"))
        details = mocks["audit"].call_args.kwargs["details"]
        assert details == {"exceptionSchemaVersion": 3}
        assert type(details["exceptionSchemaVersion"]) is int
        json.dumps(details)


@pytest.mark.unit
class TestListingPaging:
    """One ComplianceStateIndex page per request; the token wraps the index's LastEvaluatedKey
    and is threaded back as ExclusiveStartKey."""

    def test_the_token_round_trips(self):
        last_key = {"complianceState": "quarantined", "databaseId": DB, "assetId": "a"}
        response, mocks = _run(rest_event("GET", LIST_PATH, query_params={"maxItems": "1"}),
                               quarantined_rows=[dict(QUARANTINED, assetId="a")],
                               last_evaluated_key=last_key)
        token = body_of(response)["NextToken"]
        assert decode_token(token) == last_key
        assert mocks["state"].query.call_args.kwargs["Limit"] == 1

        response, mocks = _run(
            rest_event("GET", LIST_PATH, query_params={"maxItems": "1", "startingToken": token}),
            quarantined_rows=[dict(QUARANTINED, assetId="b")])
        assert [r["assetId"] for r in body_of(response)["quarantinedAssets"]] == ["b"]
        assert "NextToken" not in body_of(response)
        query = mocks["state"].query.call_args.kwargs
        assert query["ExclusiveStartKey"] == last_key
        assert query["IndexName"] == "ComplianceStateIndex"

    def test_the_page_size_is_clamped_to_the_named_bounds(self):
        _, mocks = _run(rest_event("GET", LIST_PATH, query_params={"maxItems": "100000"}))
        clamped = mocks["state"].query.call_args.kwargs["Limit"]
        assert 0 < clamped <= svc.MAX_LIST_PAGE_SIZE
        _, mocks = _run(rest_event("GET", LIST_PATH, query_params={"maxItems": "0"}))
        assert mocks["state"].query.call_args.kwargs["Limit"] == 1
        assert svc.DEFAULT_LIST_PAGE_SIZE == 100
        assert svc.MAX_LIST_PAGE_SIZE == 500

    def test_only_the_page_rows_are_enriched(self):
        rows = [dict(QUARANTINED, assetId=f"a{i}") for i in range(3)]
        _, mocks = _run(rest_event("GET", LIST_PATH), quarantined_rows=rows,
                        last_evaluated_key={"complianceState": "quarantined", "databaseId": DB,
                                            "assetId": "a2"})
        assert mocks["asset"].get_item.call_count == 3

    def test_the_pages_partition_the_quarantined_rows_against_the_indexed_table(self):
        """Against a DynamoDB double built from the harness table definition: the GSI the handler
        names exists with the keys it queries on, rows in other states stay out, page two begins
        where page one stopped, and the union is the full quarantined set."""
        with mock_aws():
            resource = boto3.resource("dynamodb", region_name="us-east-1")
            table = resource.create_table(**COMPLIANCE_ASSET_STATE_TABLE_DEFINITION)
            for database_id, asset_id, state in (
                    (DB, "q1", "quarantined"), (DB, "ok", "compliant"), ("db2", "q2", "quarantined"),
                    ("db3", "q3", "quarantined"), ("db3", "pending", "pending_evaluation"),
                    (DB, "q0", "quarantined")):
                table.put_item(Item={"databaseId": database_id, "assetId": asset_id,
                                     "schemaName": "s1", "complianceState": state})

            pages, token, reads = [], None, 0
            while True:
                query_params = {"maxItems": "2"}
                if token:
                    query_params["startingToken"] = token
                response, _ = _run(rest_event("GET", LIST_PATH, query_params=query_params),
                                   state_table=table)
                assert response["statusCode"] == 200, response
                body = body_of(response)
                pages.append([(r["databaseId"], r["assetId"]) for r in body["quarantinedAssets"]])
                reads += 1
                token = body.get("NextToken")
                if not token:
                    break
                assert reads < 10, "the token never advanced"

        assert all(len(page) <= 2 for page in pages)
        assert len(pages) >= 2
        flattened = [row for page in pages for row in page]
        assert len(flattened) == len(set(flattened))
        assert sorted(flattened) == [(DB, "q0"), (DB, "q1"), ("db2", "q2"), ("db3", "q3")]
