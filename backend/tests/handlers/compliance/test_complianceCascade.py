# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceCascadeService: dispatch of the five method+path pairs, both authorization tiers
(the cascade object AND the trigger asset's database, each fail-closed on an empty token list),
input validation with no echo of the offending input, the cascade state transitions (create ->
pending_approval or executing; approve -> executing; reject -> aborted; both conditioned on
pending_approval) and the asynchronous hand-off to the executor Lambda (202 + an `Event` invoke;
an invoke that raises leaves the row aborted and the caller told).

The executor itself is covered in test_complianceCascadeExecutor.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, CASCADE_ID, DB, USER, body_of, claims_for, enforcer, put_items, rest_event,
    update_values,
)
from handlers.compliance import complianceCascadeService as svc

MOD = "handlers.compliance.complianceCascadeService"
STORE = "handlers.compliance.complianceEvaluationStore"

EXECUTOR_FUNCTION = "vams-complianceCascadeExecutor"

LIST_PATH = "/compliance/cascades"
BY_ID_PATH = f"/compliance/cascades/{CASCADE_ID}"
APPROVE_PATH = f"/compliance/cascades/{CASCADE_ID}/approve"
REJECT_PATH = f"/compliance/cascades/{CASCADE_ID}/reject"
ID_PARAMS = {"cascadeId": CASCADE_ID}
CREATE_BODY = {"databaseId": DB, "assetId": ASSET}
CREATE_NOW_BODY = dict(CREATE_BODY, requireApproval=False)
PENDING = {"cascadeId": CASCADE_ID, "state": "pending_approval",
           "triggeredByDatabaseId": DB, "triggeredByAssetId": ASSET}

CASCADE_OBJECT = {"object__type": "complianceCascade", "cascadeId": CASCADE_ID}
EVALUATION_OBJECT = {"object__type": "complianceEvaluation", "databaseId": DB,
                     "complianceState": ""}


def _conditional_failure():
    return svc.dynamodb.meta.client.exceptions.ConditionalCheckFailedException(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")


def _enforcer_by_object(cascade=True, evaluation=True):
    """An enforcer instance whose Tier-2 answer depends on the object type it is handed."""
    instance = enforcer()
    instance.enforce.side_effect = lambda obj, action: (
        evaluation if obj["object__type"] == "complianceEvaluation" else cascade)
    return instance


def _run(event, tokens=(USER,), api=True, obj=True, cascade_item=PENDING, pending_rows=None,
         asset_exists=True, transition_succeeds=True, invoke_raises=False, instance=None):
    cascade_table = MagicMock(name="cascade_table")
    cascade_table.get_item.return_value = {"Item": dict(cascade_item)} if cascade_item else {}
    cascade_table.query.return_value = {"Items": list(pending_rows or [])}
    if not transition_succeeds:
        cascade_table.update_item.side_effect = _conditional_failure()
    lambda_client = MagicMock(name="lambda_client")
    if invoke_raises:
        lambda_client.invoke.side_effect = RuntimeError("invoke failed")
    instance = instance or enforcer(api=api, obj=obj)
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
            patch(f"{MOD}.cascade_table", cascade_table), \
            patch(f"{MOD}.lambda_client", lambda_client), \
            patch(f"{MOD}.cascade_executor_function_name", EXECUTOR_FUNCTION), \
            patch(f"{STORE}.get_asset_item",
                  return_value={"assetId": ASSET} if asset_exists else None), \
            patch(f"{STORE}.write_audit") as write_audit:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"table": cascade_table, "audit": write_audit, "invoke": lambda_client.invoke,
                      "enforcer": instance}


def _invoked_cascade_id(invoke):
    invoke.assert_called_once()
    kwargs = invoke.call_args.kwargs
    assert kwargs["FunctionName"] == EXECUTOR_FUNCTION
    assert kwargs["InvocationType"] == "Event"
    return json.loads(kwargs["Payload"])["cascadeId"]


def _aborted_writes(table):
    return [u for u in update_values(table) if u.get("state") == "aborted"]


@pytest.mark.unit
class TestRouteDispatch:

    def test_get_collection_lists_pending_cascades_newest_first(self):
        response, mocks = _run(rest_event("GET", LIST_PATH), pending_rows=[PENDING])
        assert response["statusCode"] == 200
        assert body_of(response)["cascades"][0]["cascadeId"] == CASCADE_ID
        query = mocks["table"].query.call_args.kwargs
        assert query["IndexName"] == "StateIndex"
        assert query["ScanIndexForward"] is False

    def test_post_collection_creates_a_cascade(self):
        response, _ = _run(rest_event("POST", LIST_PATH, body=CREATE_BODY))
        assert response["statusCode"] == 200, response
        assert body_of(response)["state"] == "pending_approval"

    def test_get_by_id_returns_the_cascade(self):
        response, _ = _run(rest_event("GET", BY_ID_PATH, ID_PARAMS))
        assert response["statusCode"] == 200
        assert body_of(response)["cascadeId"] == CASCADE_ID

    def test_post_approve_reaches_approve(self):
        response, _ = _run(rest_event("POST", APPROVE_PATH, ID_PARAMS, body={}))
        assert response["statusCode"] == 202, response
        assert body_of(response)["message"] == "Cascade approved"

    def test_post_reject_reaches_reject(self):
        response, _ = _run(rest_event("POST", REJECT_PATH, ID_PARAMS, body={}))
        assert response["statusCode"] == 200, response
        assert body_of(response)["message"] == "Cascade rejected"

    @pytest.mark.parametrize("method,path", [
        ("PUT", LIST_PATH), ("DELETE", BY_ID_PATH), ("POST", BY_ID_PATH),
        ("GET", APPROVE_PATH), ("GET", REJECT_PATH), ("POST", f"{BY_ID_PATH}/cancel"),
    ])
    def test_an_unknown_method_or_path_is_refused(self, method, path):
        response, mocks = _run(rest_event(method, path, ID_PARAMS, body={}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"
        mocks["table"].put_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()
        mocks["invoke"].assert_not_called()


# Every single-resource path: (method, path, pathParameters, body).
SINGLE_RESOURCE_REQUESTS = [
    ("POST", LIST_PATH, None, CREATE_NOW_BODY),
    ("GET", BY_ID_PATH, ID_PARAMS, None),
    ("POST", APPROVE_PATH, ID_PARAMS, {}),
    ("POST", REJECT_PATH, ID_PARAMS, {}),
]
SINGLE_RESOURCE_IDS = ["create", "get", "approve", "reject"]


class _EnforcerSpy:
    """A CasbinEnforcer stand-in that records every construction; its verdict is always allow, so a
    denial observed alongside zero constructions can only have come from the token guard."""

    def __init__(self):
        self.constructions = []

    @property
    def factory(self):
        spy = self

        class _Enforcer:
            def __init__(self, claims_and_roles):
                spy.constructions.append(claims_and_roles)

            def enforce(self, obj, action):
                return True

            def enforceAPI(self, event):
                return True

        return _Enforcer


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens,api", [((), False), ((USER,), False), ((), True)],
                             ids=["empty-tokens", "api-denied", "empty-tokens-api-allowed"])
    def test_tier_one_denies(self, tokens, api):
        response, mocks = _run(rest_event("GET", LIST_PATH), tokens=tokens, api=api,
                               pending_rows=[PENDING])
        assert response["statusCode"] == 403
        mocks["table"].query.assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", SINGLE_RESOURCE_REQUESTS,
                             ids=SINGLE_RESOURCE_IDS)
    def test_an_empty_token_list_denies_before_any_read_write_or_invoke(
            self, method, path, params, body):
        # Unconfounded: the API tier would allow, so the 403 is the empty-token deny alone.
        response, mocks = _run(rest_event(method, path, params, body=body), tokens=(), api=True)
        assert response["statusCode"] == 403
        mocks["table"].get_item.assert_not_called()
        mocks["table"].put_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()
        mocks["invoke"].assert_not_called()

    @pytest.mark.parametrize("function,args", [
        ("create_cascade", lambda: (svc.CreateCascadeRequestModel(**CREATE_NOW_BODY),)),
        ("get_cascade", lambda: (CASCADE_ID,)),
        ("approve_cascade", lambda: (CASCADE_ID, svc.ApproveCascadeRequestModel())),
        ("reject_cascade", lambda: (CASCADE_ID, svc.RejectCascadeRequestModel())),
    ], ids=SINGLE_RESOURCE_IDS)
    def test_the_business_function_itself_denies_an_empty_token_list_without_consulting_casbin(
            self, function, args):
        # The Tier-2 guard is a statement of its own in each business function, so it holds even
        # when the function is reached with no identity: the enforcer is never constructed.
        spy = _EnforcerSpy()
        cascade_table = MagicMock(name="cascade_table")
        lambda_client = MagicMock(name="lambda_client")
        with patch(f"{MOD}.claims_and_roles", {"tokens": [], "roles": []}), \
                patch(f"{MOD}.CasbinEnforcer", spy.factory), \
                patch(f"{MOD}.cascade_table", cascade_table), \
                patch(f"{MOD}.lambda_client", lambda_client), \
                patch(f"{STORE}.get_asset_item", return_value={"assetId": ASSET}), \
                patch(f"{STORE}.write_audit"):
            response = getattr(svc, function)(rest_event("POST", LIST_PATH), *args())
        assert response["statusCode"] == 403
        assert spy.constructions == []
        assert cascade_table.method_calls == []
        lambda_client.invoke.assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", SINGLE_RESOURCE_REQUESTS,
                             ids=SINGLE_RESOURCE_IDS)
    def test_a_cascade_object_denial_changes_nothing(self, method, path, params, body):
        response, mocks = _run(rest_event(method, path, params, body=body), obj=False)
        assert response["statusCode"] == 403
        mocks["table"].put_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()
        mocks["table"].get_item.assert_not_called()
        mocks["invoke"].assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", SINGLE_RESOURCE_REQUESTS,
                             ids=SINGLE_RESOURCE_IDS)
    def test_a_trigger_database_denial_changes_nothing_even_when_the_cascade_object_is_allowed(
            self, method, path, params, body):
        # Tier 2 on the cascade object alone cannot scope a cascade to a database (a fresh uuid
        # carries no database attribute), so the trigger asset's database is enforced as well.
        response, mocks = _run(rest_event(method, path, params, body=body),
                               instance=_enforcer_by_object(cascade=True, evaluation=False))
        assert response["statusCode"] == 403
        mocks["table"].put_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()
        mocks["invoke"].assert_not_called()
        mocks["audit"].assert_not_called()

    def test_create_enforces_the_cascade_object_and_the_request_database(self):
        _, mocks = _run(rest_event("POST", LIST_PATH, body=CREATE_NOW_BODY))
        calls = [c.args for c in mocks["enforcer"].enforce.call_args_list]
        assert len(calls) == 2
        cascade_object, cascade_action = calls[0]
        assert cascade_object["object__type"] == "complianceCascade"
        assert cascade_object["cascadeId"] == _invoked_cascade_id(mocks["invoke"])
        assert cascade_action == "POST"
        assert calls[1] == (EVALUATION_OBJECT, "POST")

    @pytest.mark.parametrize("method,path,body,action", [
        ("POST", APPROVE_PATH, {}, "POST"), ("POST", REJECT_PATH, {}, "POST"),
        ("GET", BY_ID_PATH, None, "GET"),
    ], ids=["approve", "reject", "get"])
    def test_the_row_is_read_and_its_trigger_database_enforced(self, method, path, body, action):
        row = dict(PENDING, triggeredByDatabaseId="other-db")
        _, mocks = _run(rest_event(method, path, ID_PARAMS, body=body), cascade_item=row)
        assert [c.args for c in mocks["enforcer"].enforce.call_args_list] == [
            (CASCADE_OBJECT, action),
            (dict(EVALUATION_OBJECT, databaseId="other-db"), action),
        ]
        mocks["table"].get_item.assert_called_once_with(Key={"cascadeId": CASCADE_ID})

    def test_the_listing_filters_to_what_the_caller_may_get(self):
        response, _ = _run(rest_event("GET", LIST_PATH), obj=False, pending_rows=[PENDING])
        assert response["statusCode"] == 200
        assert body_of(response)["cascades"] == []

    def test_the_listing_enforces_the_cascade_object_and_the_trigger_database(self):
        _, mocks = _run(rest_event("GET", LIST_PATH), pending_rows=[PENDING])
        assert [c.args for c in mocks["enforcer"].enforce.call_args_list] == [
            (CASCADE_OBJECT, "GET"),
            (EVALUATION_OBJECT, "GET"),
        ]

    @pytest.mark.parametrize("cascade,evaluation", [(True, False), (False, True)],
                             ids=["trigger-database-denied", "cascade-object-denied"])
    def test_a_row_denied_on_either_object_is_left_out(self, cascade, evaluation):
        response, _ = _run(rest_event("GET", LIST_PATH), pending_rows=[PENDING],
                           instance=_enforcer_by_object(cascade=cascade, evaluation=evaluation))
        assert response["statusCode"] == 200
        assert body_of(response)["cascades"] == []
        assert CASCADE_ID not in response["body"]

    def test_the_listing_keeps_only_the_rows_whose_trigger_database_the_caller_may_get(self):
        instance = enforcer()
        instance.enforce.side_effect = lambda obj, action: (
            obj["object__type"] == "complianceCascade" or obj["databaseId"] == DB)
        hidden = dict(PENDING, cascadeId="5b1e2c3d-0000-4000-8000-000000000002",
                      triggeredByDatabaseId="hidden-db", triggeredByAssetId="theirs")
        response, _ = _run(rest_event("GET", LIST_PATH), pending_rows=[PENDING, hidden],
                           instance=instance)
        rows = body_of(response)["cascades"]
        assert [r["cascadeId"] for r in rows] == [CASCADE_ID]
        assert "hidden-db" not in response["body"]
        assert "theirs" not in response["body"]

    def test_the_listing_function_yields_an_empty_list_on_an_empty_token_list(self):
        # Append-on-allow: reached with no identity, no row passes and Casbin is never constructed.
        spy = _EnforcerSpy()
        cascade_table = MagicMock(name="cascade_table")
        cascade_table.query.return_value = {"Items": [dict(PENDING)]}
        with patch(f"{MOD}.claims_and_roles", {"tokens": [], "roles": []}), \
                patch(f"{MOD}.CasbinEnforcer", spy.factory), \
                patch(f"{MOD}.cascade_table", cascade_table):
            response = svc.list_pending_cascades(rest_event("GET", LIST_PATH))
        assert response["statusCode"] == 200
        assert body_of(response)["cascades"] == []
        assert spy.constructions == []


@pytest.mark.unit
class TestListingRows:

    def test_each_row_carries_the_trigger_asset_as_database_id_and_asset_id(self):
        response, _ = _run(rest_event("GET", LIST_PATH), pending_rows=[PENDING])
        row = body_of(response)["cascades"][0]
        assert row["databaseId"] == DB and row["assetId"] == ASSET
        assert row["triggeredByDatabaseId"] == DB and row["triggeredByAssetId"] == ASSET
        assert row["cascadeId"] == CASCADE_ID and row["state"] == "pending_approval"

    def test_a_row_missing_its_trigger_attributes_carries_empty_strings(self):
        bare = {"cascadeId": CASCADE_ID, "state": "pending_approval"}
        response, _ = _run(rest_event("GET", LIST_PATH), pending_rows=[bare])
        row = body_of(response)["cascades"][0]
        assert row["databaseId"] == "" and row["assetId"] == ""


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("method,suffix,body", [
        ("GET", "", None), ("POST", "/approve", {}), ("POST", "/reject", {}),
    ])
    def test_a_bad_cascade_id_is_rejected(self, method, suffix, body):
        bad = "not-a-uuid-zq9"
        response, mocks = _run(
            rest_event(method, f"/compliance/cascades/{bad}{suffix}", {"cascadeId": bad}, body=body))
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        mocks["table"].get_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()
        mocks["invoke"].assert_not_called()

    @pytest.mark.parametrize("body,bad", [
        ({}, None),
        ({"databaseId": DB}, None),
        ({"assetId": ASSET}, None),
        ({"databaseId": "zq!zq", "assetId": ASSET}, "zq!zq"),
        ({"databaseId": DB, "assetId": "bad<id>"}, "bad<id>"),
        ({"databaseId": DB, "assetId": ASSET, "requireApproval": "sometimesq"}, "sometimesq"),
        ({"databaseId": DB, "assetId": ASSET, "reason": "r" * 1025}, "r" * 1025),
    ])
    def test_a_bad_create_body_is_rejected(self, body, bad):
        response, mocks = _run(rest_event("POST", LIST_PATH, body=body))
        assert response["statusCode"] == 400
        if bad is not None:
            assert bad not in response["body"]
        mocks["table"].put_item.assert_not_called()
        mocks["invoke"].assert_not_called()

    @pytest.mark.parametrize("path", [APPROVE_PATH, REJECT_PATH], ids=["approve", "reject"])
    def test_a_bad_approve_or_reject_body_is_rejected(self, path):
        bad = "q" * 1025
        response, mocks = _run(rest_event("POST", path, ID_PARAMS, body={"reason": bad}))
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        mocks["table"].update_item.assert_not_called()
        mocks["invoke"].assert_not_called()

    def test_a_body_that_is_not_json_is_rejected(self):
        bad = "[oops-zq"
        response, _ = _run(rest_event("POST", LIST_PATH, body=bad))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]
        assert bad not in response["body"]


@pytest.mark.unit
class TestCreate:

    def test_a_cascade_requiring_approval_is_created_pending_with_a_timeout(self):
        response, mocks = _run(rest_event("POST", LIST_PATH, body=dict(CREATE_BODY, reason=" why ")))
        item = put_items(mocks["table"])[0]
        assert item["state"] == "pending_approval"
        assert item["cascadeId"] == body_of(response)["cascadeId"]
        assert item["triggeredByDatabaseId"] == DB and item["triggeredByAssetId"] == ASSET
        assert item["triggerReason"] == "why"
        assert item["actor"] == USER
        assert item["requireApproval"] is True
        assert "approvalTimeoutAt" in item
        assert json.loads(item["nodes"]) == {} and json.loads(item["executionOrder"]) == []
        mocks["invoke"].assert_not_called()
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "cascade_triggered"
        assert audit["cascade_id"] == item["cascadeId"]
        assert response["statusCode"] == 200
        assert body_of(response) == {"message": "Cascade created", "cascadeId": item["cascadeId"],
                                     "state": "pending_approval"}

    def test_a_cascade_without_approval_is_handed_to_the_executor_and_accepted(self):
        response, mocks = _run(rest_event("POST", LIST_PATH, body=CREATE_NOW_BODY))
        item = put_items(mocks["table"])[0]
        assert item["state"] == "executing"
        assert "approvalTimeoutAt" not in item
        assert _invoked_cascade_id(mocks["invoke"]) == item["cascadeId"]
        assert response["statusCode"] == 202
        assert body_of(response) == {"message": "Cascade created", "cascadeId": item["cascadeId"],
                                     "state": "executing"}

    def test_the_row_and_the_audit_entry_precede_the_invoke(self):
        # The executor reads the row it is handed, so the put (and the audit entry, which the row's
        # abort path does not rewrite) must land before the asynchronous invoke is issued.
        manager = MagicMock(name="manager")
        cascade_table = MagicMock(name="cascade_table")
        lambda_client = MagicMock(name="lambda_client")
        write_audit = MagicMock(name="write_audit")
        manager.attach_mock(cascade_table.put_item, "put_item")
        manager.attach_mock(write_audit, "write_audit")
        manager.attach_mock(lambda_client.invoke, "invoke")
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{MOD}.cascade_table", cascade_table), \
                patch(f"{MOD}.lambda_client", lambda_client), \
                patch(f"{MOD}.cascade_executor_function_name", EXECUTOR_FUNCTION), \
                patch(f"{STORE}.get_asset_item", return_value={"assetId": ASSET}), \
                patch(f"{STORE}.write_audit", write_audit):
            response = svc.lambda_handler(rest_event("POST", LIST_PATH, body=CREATE_NOW_BODY),
                                          MagicMock())
        assert response["statusCode"] == 202
        assert [name for name, _, _ in manager.mock_calls] == ["put_item", "write_audit", "invoke"]

    def test_an_invoke_that_raises_aborts_the_row_and_reports_it(self):
        response, mocks = _run(rest_event("POST", LIST_PATH, body=CREATE_NOW_BODY),
                               invoke_raises=True)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Cascade could not be started"
        aborted = _aborted_writes(mocks["table"])
        assert len(aborted) == 1
        assert aborted[0]["abortReason"]
        assert aborted[0]["completedAt"]
        abort = mocks["table"].update_item.call_args.kwargs
        assert abort["Key"] == {"cascadeId": put_items(mocks["table"])[0]["cascadeId"]}
        # Only a row still `executing` is aborted; a run that did start keeps its terminal state.
        condition = abort["ConditionExpression"]
        assert ":executing" in condition
        assert abort["ExpressionAttributeValues"][":executing"] == "executing"

    def test_a_missing_source_asset_is_refused(self):
        response, mocks = _run(rest_event("POST", LIST_PATH, body=CREATE_NOW_BODY),
                               asset_exists=False)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Asset not found"
        mocks["table"].put_item.assert_not_called()
        mocks["invoke"].assert_not_called()


@pytest.mark.unit
class TestApproveAndReject:

    def test_approve_moves_pending_to_executing_and_hands_off_to_the_executor(self):
        response, mocks = _run(rest_event("POST", APPROVE_PATH, ID_PARAMS, body={"reason": "go"}))
        update = mocks["table"].update_item.call_args.kwargs
        assert update["ExpressionAttributeValues"][":state"] == "executing"
        assert update["ExpressionAttributeValues"][":pending"] == "pending_approval"
        assert update["ExpressionAttributeValues"][":reason"] == "go"
        assert update["ExpressionAttributeValues"][":actor"] == USER
        assert "#s = :pending" in update["ConditionExpression"]
        assert _invoked_cascade_id(mocks["invoke"]) == CASCADE_ID
        audit = mocks["audit"].call_args
        assert audit.kwargs["event_type"] == "cascade_approved"
        assert audit.args == (DB, ASSET)
        assert response["statusCode"] == 202
        assert body_of(response) == {"message": "Cascade approved", "cascadeId": CASCADE_ID,
                                     "state": "executing"}

    def test_an_approve_whose_invoke_raises_aborts_the_row_and_reports_it(self):
        response, mocks = _run(rest_event("POST", APPROVE_PATH, ID_PARAMS, body={}),
                               invoke_raises=True)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Cascade could not be started"
        writes = update_values(mocks["table"])
        assert writes[0]["state"] == "executing"
        assert writes[1]["state"] == "aborted"
        assert writes[1]["abortReason"] and writes[1]["completedAt"]
        assert mocks["audit"].call_args.kwargs["event_type"] == "cascade_approved"

    def test_reject_moves_pending_to_aborted_without_invoking(self):
        response, mocks = _run(rest_event("POST", REJECT_PATH, ID_PARAMS, body={"reason": "no"}))
        update = mocks["table"].update_item.call_args.kwargs
        assert update["ExpressionAttributeValues"][":state"] == "aborted"
        assert update["ExpressionAttributeValues"][":reason"] == "no"
        assert "completedAt = :now" in update["UpdateExpression"]
        assert "#s = :pending" in update["ConditionExpression"]
        mocks["invoke"].assert_not_called()
        assert mocks["audit"].call_args.kwargs["event_type"] == "cascade_rejected"
        assert response["statusCode"] == 200

    @pytest.mark.parametrize("path", [APPROVE_PATH, REJECT_PATH])
    def test_a_cascade_that_is_not_pending_is_refused(self, path):
        response, mocks = _run(rest_event("POST", path, ID_PARAMS, body={}),
                               transition_succeeds=False)
        assert response["statusCode"] == 400
        assert "not in pending_approval state" in body_of(response)["message"]
        mocks["invoke"].assert_not_called()
        mocks["audit"].assert_not_called()

    @pytest.mark.parametrize("path", [APPROVE_PATH, REJECT_PATH])
    def test_a_cascade_that_does_not_exist_is_refused_before_the_transition(self, path):
        response, mocks = _run(rest_event("POST", path, ID_PARAMS, body={}), cascade_item=None)
        assert response["statusCode"] == 400
        assert "not in pending_approval state" in body_of(response)["message"]
        mocks["table"].update_item.assert_not_called()
        mocks["invoke"].assert_not_called()
        mocks["audit"].assert_not_called()

    def test_default_reasons_apply_when_the_body_is_empty(self):
        _, mocks = _run(rest_event("POST", APPROVE_PATH, ID_PARAMS))
        assert mocks["table"].update_item.call_args.kwargs["ExpressionAttributeValues"][":reason"] == "approved"
        _, mocks = _run(rest_event("POST", REJECT_PATH, ID_PARAMS))
        assert mocks["table"].update_item.call_args.kwargs["ExpressionAttributeValues"][":reason"] == "rejected"

    def test_getting_a_missing_cascade_is_not_found(self):
        response, _ = _run(rest_event("GET", BY_ID_PATH, ID_PARAMS), cascade_item=None)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Cascade not found"
