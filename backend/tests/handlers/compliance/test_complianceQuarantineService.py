# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceQuarantineService: dispatch of its three method+path pairs, both authorization tiers,
input validation, the two state transitions out of quarantine (release clears the quarantine;
exception records who granted it and why; both refuse an asset that is not quarantined), and the
listing, which pages the ComplianceStateIndex GSI behind a round-tripping token."""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance._harness import (
    ASSET, COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, DB, USER, body_of, claims_for, decode_token,
    encode_token, enforcer, rest_event,
)
from handlers.compliance import complianceQuarantineService as svc

MOD = "handlers.compliance.complianceQuarantineService"
STORE = "handlers.compliance.complianceEvaluationStore"

LIST_PATH = "/compliance/quarantine"
RELEASE_PATH = f"/compliance/quarantine/{DB}/{ASSET}/release"
EXCEPTION_PATH = f"/compliance/quarantine/{DB}/{ASSET}/exception"
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}
QUARANTINED = {"databaseId": DB, "assetId": ASSET, "complianceState": "quarantined"}

ALL_PATHS = [
    ("GET", LIST_PATH, None, None),
    ("POST", RELEASE_PATH, ASSET_PARAMS, {}),
    ("POST", EXCEPTION_PATH, ASSET_PARAMS, {"reason": "r"}),
]


def _run(event, tokens=(USER,), api=True, obj=True, compliance_record=None, quarantined_rows=None,
         last_evaluated_key=None, casbin=None, state_table=None):
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
            patch(f"{STORE}.update_asset_state") as update_state, \
            patch(f"{STORE}.write_audit") as write_audit:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"state": state_table, "asset": asset_table, "update_state": update_state,
                      "audit": write_audit, "casbin": casbin}


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
        mocks["state"].query.assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", ALL_PATHS)
    def test_tier_one_with_empty_tokens_denies_even_when_the_api_check_would_pass(
            self, method, path, params, body):
        response, mocks = _run(rest_event(method, path, params, body=body), tokens=(), api=True,
                               compliance_record=QUARANTINED, quarantined_rows=[QUARANTINED])
        assert response["statusCode"] == 403
        mocks["casbin"].enforce.assert_not_called()
        mocks["state"].query.assert_not_called()
        mocks["update_state"].assert_not_called()
        mocks["audit"].assert_not_called()

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


@pytest.mark.unit
class TestValidation:

    BAD_DB = "bad<database-id>"
    BAD_ASSET = "bad<asset-id>"

    @pytest.mark.parametrize("path,params,bad", [
        (f"/compliance/quarantine/{BAD_DB}/{ASSET}/release", {"databaseId": BAD_DB, "assetId": ASSET},
         BAD_DB),
        (f"/compliance/quarantine/{DB}/{BAD_ASSET}/release", {"databaseId": DB, "assetId": BAD_ASSET},
         BAD_ASSET),
        (f"/compliance/quarantine/{BAD_DB}/{ASSET}/exception", {"databaseId": BAD_DB, "assetId": ASSET},
         BAD_DB),
    ])
    def test_a_bad_path_parameter_is_rejected(self, path, params, bad):
        response, mocks = _run(rest_event("POST", path, params, body={"reason": "r"}),
                               compliance_record=QUARANTINED)
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        mocks["update_state"].assert_not_called()

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
