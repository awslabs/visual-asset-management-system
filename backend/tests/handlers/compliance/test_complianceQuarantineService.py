# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceQuarantineService: dispatch of its three method+path pairs, both authorization tiers,
input validation, and the two state transitions out of quarantine (release clears the quarantine;
exception records who granted it and why). Both refuse an asset that is not quarantined."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, USER, body_of, claims_for, enforcer, rest_event,
)
from handlers.compliance import complianceQuarantineService as svc

MOD = "handlers.compliance.complianceQuarantineService"
STORE = "handlers.compliance.complianceEvaluationStore"

LIST_PATH = "/compliance/quarantine"
RELEASE_PATH = f"/compliance/quarantine/{DB}/{ASSET}/release"
EXCEPTION_PATH = f"/compliance/quarantine/{DB}/{ASSET}/exception"
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}
QUARANTINED = {"databaseId": DB, "assetId": ASSET, "complianceState": "quarantined"}


def _run(event, tokens=(USER,), api=True, obj=True, compliance_record=None, schema_names=("s1",),
         quarantined_rows=None):
    schema_table = MagicMock(name="schema_table")
    schema_table.scan.return_value = {"Items": [{"schemaName": n} for n in schema_names]}
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": list(quarantined_rows or [])}
    asset_table = MagicMock(name="asset_table")
    asset_table.get_item.return_value = {"Item": {"assetName": "Turbine"}}
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer(api=api, obj=obj)), \
            patch(f"{MOD}.schema_table", schema_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{STORE}.get_compliance_record", return_value=compliance_record), \
            patch(f"{STORE}.update_asset_state") as update_state, \
            patch(f"{STORE}.write_audit") as write_audit:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"schema": schema_table, "state": state_table, "asset": asset_table,
                      "update_state": update_state, "audit": write_audit}


@pytest.mark.unit
class TestRouteDispatch:

    def test_get_lists_quarantined_assets_across_every_schema(self):
        response, mocks = _run(rest_event("GET", LIST_PATH), schema_names=("s1", "s2"),
                               quarantined_rows=[QUARANTINED])
        assert response["statusCode"] == 200
        rows = body_of(response)["quarantinedAssets"]
        assert len(rows) == 2
        assert all(r["assetName"] == "Turbine" for r in rows)
        reads = mocks["state"].query.call_args_list
        assert len(reads) == 2
        assert {c.kwargs["IndexName"] for c in reads} == {"SchemaNameIndex"}

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

    @pytest.mark.parametrize("method,path", [
        ("POST", LIST_PATH), ("GET", RELEASE_PATH), ("DELETE", EXCEPTION_PATH),
        ("POST", f"/compliance/quarantine/{DB}/{ASSET}"), ("PUT", LIST_PATH),
        ("POST", f"/compliance/quarantine/{DB}/{ASSET}/pardon"),
    ])
    def test_an_unknown_method_or_path_is_refused(self, method, path):
        response, mocks = _run(rest_event(method, path, ASSET_PARAMS, body={}),
                               compliance_record=QUARANTINED)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"
        mocks["update_state"].assert_not_called()


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens", [(), (USER,)], ids=["empty-tokens", "api-denied"])
    def test_tier_one_denies(self, tokens):
        response, mocks = _run(rest_event("GET", LIST_PATH), tokens=tokens, api=False)
        assert response["statusCode"] == 403
        mocks["schema"].scan.assert_not_called()

    @pytest.mark.parametrize("path,body", [(RELEASE_PATH, {}), (EXCEPTION_PATH, {"reason": "r"})])
    def test_tier_two_denial_changes_nothing(self, path, body):
        response, mocks = _run(rest_event("POST", path, ASSET_PARAMS, body=body), obj=False,
                               compliance_record=QUARANTINED)
        assert response["statusCode"] == 403
        mocks["update_state"].assert_not_called()
        mocks["audit"].assert_not_called()

    def test_the_listing_filters_to_what_the_caller_may_get(self):
        response, mocks = _run(rest_event("GET", LIST_PATH), obj=False,
                               quarantined_rows=[QUARANTINED])
        assert response["statusCode"] == 200
        assert body_of(response)["quarantinedAssets"] == []
        mocks["asset"].get_item.assert_not_called()

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


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("path,params", [
        (f"/compliance/quarantine/x/{ASSET}/release", {"databaseId": "x", "assetId": ASSET}),
        (f"/compliance/quarantine/{DB}/bad<id>/release", {"databaseId": DB, "assetId": "bad<id>"}),
        (f"/compliance/quarantine/x/{ASSET}/exception", {"databaseId": "x", "assetId": ASSET}),
    ])
    def test_a_bad_path_parameter_is_rejected(self, path, params):
        response, mocks = _run(rest_event("POST", path, params, body={"reason": "r"}),
                               compliance_record=QUARANTINED)
        assert response["statusCode"] == 400
        mocks["update_state"].assert_not_called()

    @pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 1025}],
                             ids=["missing", "empty", "blank", "too-long"])
    def test_an_exception_needs_a_reason(self, body):
        response, mocks = _run(rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body=body),
                               compliance_record=QUARANTINED)
        assert response["statusCode"] == 400
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

    def test_exception_moves_quarantined_to_compliant_and_records_the_grant(self):
        response, mocks = _run(
            rest_event("POST", EXCEPTION_PATH, ASSET_PARAMS, body={"reason": " legacy data "}),
            compliance_record=QUARANTINED)
        assert response["statusCode"] == 200
        assert body_of(response)["reason"] == "legacy data"
        updates = mocks["update_state"].call_args.args[2]
        assert updates["complianceState"] == "compliant"
        assert updates["exceptionGranted"] is True
        assert updates["exceptionReason"] == "legacy data"
        assert updates["exceptionGrantedBy"] == USER
        assert updates["exceptionGrantedAt"] == updates["updatedAt"]
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "exception_granted"
        assert (audit["previous_state"], audit["new_state"]) == ("quarantined", "compliant")

    @pytest.mark.parametrize("path,body", [(RELEASE_PATH, {}), (EXCEPTION_PATH, {"reason": "r"})])
    @pytest.mark.parametrize("state", ["compliant", "non_compliant", "pending_evaluation", "unknown"])
    def test_an_asset_that_is_not_quarantined_is_refused(self, path, body, state):
        response, mocks = _run(rest_event("POST", path, ASSET_PARAMS, body=body),
                               compliance_record=dict(QUARANTINED, complianceState=state))
        assert response["statusCode"] == 400
        assert "not quarantined" in body_of(response)["message"]
        mocks["update_state"].assert_not_called()
        mocks["audit"].assert_not_called()

    @pytest.mark.parametrize("path,body", [(RELEASE_PATH, {}), (EXCEPTION_PATH, {"reason": "r"})])
    def test_an_untracked_asset_is_refused(self, path, body):
        response, mocks = _run(rest_event("POST", path, ASSET_PARAMS, body=body))
        assert response["statusCode"] == 400
        assert "not found in compliance tracking" in body_of(response)["message"]
        mocks["update_state"].assert_not_called()


@pytest.mark.unit
class TestListing:

    def test_schema_names_are_scanned_to_exhaustion_and_deduplicated(self):
        schema_table = MagicMock()
        schema_table.scan.side_effect = [
            {"Items": [{"schemaName": "s1"}, {"schemaName": "s1"}], "LastEvaluatedKey": {"k": 1}},
            {"Items": [{"schemaName": "s2"}, {}]},
        ]
        state_table = MagicMock()
        state_table.query.return_value = {"Items": []}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.schema_table", schema_table), \
                patch(f"{MOD}.asset_state_table", state_table):
            response = svc.lambda_handler(rest_event("GET", LIST_PATH), MagicMock())
        assert response["statusCode"] == 200
        assert schema_table.scan.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}
        assert state_table.query.call_count == 2

    def test_quarantined_rows_are_paged_to_exhaustion_per_schema(self):
        state_table = MagicMock()
        state_table.query.side_effect = [
            {"Items": [dict(QUARANTINED, assetId="a")], "LastEvaluatedKey": {"k": 1}},
            {"Items": [dict(QUARANTINED, assetId="b")]},
        ]
        schema_table = MagicMock()
        schema_table.scan.return_value = {"Items": [{"schemaName": "s1"}]}
        asset_table = MagicMock()
        asset_table.get_item.return_value = {}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.schema_table", schema_table), \
                patch(f"{MOD}.asset_state_table", state_table), \
                patch(f"{MOD}.asset_table", asset_table):
            response = svc.lambda_handler(rest_event("GET", LIST_PATH), MagicMock())
        rows = body_of(response)["quarantinedAssets"]
        assert [r["assetId"] for r in rows] == ["a", "b"]
        assert all(r["assetName"] == "" for r in rows)
        assert state_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}
