# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceCascadeService + complianceCascadeExecutor: dispatch of the five method+path pairs,
both authorization tiers, input validation, the cascade state transitions (create -> pending_approval
or executing; approve -> executing and run; reject -> aborted; both conditioned on pending_approval),
and the executor's descendant discovery (topological order, node cap, per-node states)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, CASCADE_ID, DB, USER, body_of, claims_for, enforcer, put_items, rest_event,
)
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceCascadeService as svc

MOD = "handlers.compliance.complianceCascadeService"
EXECUTOR = "handlers.compliance.complianceCascadeExecutor"
STORE = "handlers.compliance.complianceEvaluationStore"

LIST_PATH = "/compliance/cascades"
BY_ID_PATH = f"/compliance/cascades/{CASCADE_ID}"
APPROVE_PATH = f"/compliance/cascades/{CASCADE_ID}/approve"
REJECT_PATH = f"/compliance/cascades/{CASCADE_ID}/reject"
ID_PARAMS = {"cascadeId": CASCADE_ID}
CREATE_BODY = {"databaseId": DB, "assetId": ASSET}
PENDING = {"cascadeId": CASCADE_ID, "state": "pending_approval",
           "triggeredByDatabaseId": DB, "triggeredByAssetId": ASSET}


def _conditional_failure():
    return svc.dynamodb.meta.client.exceptions.ConditionalCheckFailedException(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")


def _run(event, tokens=(USER,), api=True, obj=True, cascade_item=PENDING, pending_rows=None,
         asset_exists=True, transition_succeeds=True, execute_result=None):
    cascade_table = MagicMock(name="cascade_table")
    cascade_table.get_item.return_value = {"Item": dict(cascade_item)} if cascade_item else {}
    cascade_table.query.return_value = {"Items": list(pending_rows or [])}
    if not transition_succeeds:
        cascade_table.update_item.side_effect = _conditional_failure()
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer(api=api, obj=obj)), \
            patch(f"{MOD}.cascade_table", cascade_table), \
            patch(f"{STORE}.get_asset_item",
                  return_value={"assetId": ASSET} if asset_exists else None), \
            patch(f"{STORE}.write_audit") as write_audit, \
            patch(f"{MOD}.execute_cascade",
                  return_value=execute_result or {"status": "completed", "evaluated": 0,
                                                  "results": []}) as execute:
        response = svc.lambda_handler(event, MagicMock())
    return response, {"table": cascade_table, "audit": write_audit, "execute": execute}


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
        assert response["statusCode"] == 200, response
        assert body_of(response)["message"] == "Cascade approved and executed"

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


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens", [(), (USER,)], ids=["empty-tokens", "api-denied"])
    def test_tier_one_denies(self, tokens):
        response, mocks = _run(rest_event("GET", LIST_PATH), tokens=tokens, api=False,
                               pending_rows=[PENDING])
        assert response["statusCode"] == 403
        mocks["table"].query.assert_not_called()

    @pytest.mark.parametrize("method,path,params,body", [
        ("POST", LIST_PATH, None, CREATE_BODY),
        ("GET", BY_ID_PATH, ID_PARAMS, None),
        ("POST", APPROVE_PATH, ID_PARAMS, {}),
        ("POST", REJECT_PATH, ID_PARAMS, {}),
    ])
    def test_tier_two_denial_changes_nothing(self, method, path, params, body):
        response, mocks = _run(rest_event(method, path, params, body=body), obj=False)
        assert response["statusCode"] == 403
        mocks["table"].put_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()
        mocks["table"].get_item.assert_not_called()
        mocks["execute"].assert_not_called()

    def test_the_listing_filters_to_what_the_caller_may_get(self):
        response, _ = _run(rest_event("GET", LIST_PATH), obj=False, pending_rows=[PENDING])
        assert response["statusCode"] == 200
        assert body_of(response)["cascades"] == []

    def test_approve_checks_a_cascade_object_by_id(self):
        instance = enforcer()
        cascade_table = MagicMock()
        cascade_table.get_item.return_value = {"Item": dict(PENDING)}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.cascade_table", cascade_table), \
                patch(f"{STORE}.write_audit"), patch(f"{MOD}.execute_cascade", return_value={}):
            svc.lambda_handler(rest_event("POST", APPROVE_PATH, ID_PARAMS, body={}), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceCascade", "cascadeId": CASCADE_ID}, "POST")


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("method,suffix,body", [
        ("GET", "", None), ("POST", "/approve", {}), ("POST", "/reject", {}),
    ])
    def test_a_cascade_id_that_is_not_a_uuid_is_rejected(self, method, suffix, body):
        response, mocks = _run(
            rest_event(method, f"/compliance/cascades/not-a-uuid{suffix}",
                       {"cascadeId": "not-a-uuid"}, body=body))
        assert response["statusCode"] == 400
        mocks["table"].get_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()

    @pytest.mark.parametrize("body", [
        {}, {"databaseId": DB}, {"assetId": ASSET}, {"databaseId": "x", "assetId": ASSET},
        {"databaseId": DB, "assetId": "bad<id>"},
        {"databaseId": DB, "assetId": ASSET, "requireApproval": "sometimes"},
    ])
    def test_a_bad_create_body_is_rejected(self, body):
        response, mocks = _run(rest_event("POST", LIST_PATH, body=body))
        assert response["statusCode"] == 400
        mocks["table"].put_item.assert_not_called()

    def test_a_body_that_is_not_json_is_rejected(self):
        response, _ = _run(rest_event("POST", LIST_PATH, body="[oops"))
        assert response["statusCode"] == 400
        assert "Invalid JSON" in body_of(response)["message"]


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
        mocks["execute"].assert_not_called()
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "cascade_triggered"
        assert audit["cascade_id"] == item["cascadeId"]
        assert "result" not in body_of(response)

    def test_a_cascade_without_approval_executes_immediately(self):
        response, mocks = _run(rest_event("POST", LIST_PATH,
                                          body=dict(CREATE_BODY, requireApproval=False)),
                               execute_result={"status": "completed", "evaluated": 2})
        item = put_items(mocks["table"])[0]
        assert item["state"] == "executing"
        assert "approvalTimeoutAt" not in item
        mocks["execute"].assert_called_once_with(item["cascadeId"])
        body = body_of(response)
        assert body["state"] == "executing"
        assert body["result"] == {"status": "completed", "evaluated": 2}

    def test_a_missing_source_asset_is_refused(self):
        response, mocks = _run(rest_event("POST", LIST_PATH, body=CREATE_BODY), asset_exists=False)
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Asset not found"
        mocks["table"].put_item.assert_not_called()


@pytest.mark.unit
class TestApproveAndReject:

    def test_approve_moves_pending_to_executing_and_runs_the_cascade(self):
        response, mocks = _run(rest_event("POST", APPROVE_PATH, ID_PARAMS, body={"reason": "go"}),
                               execute_result={"status": "completed", "evaluated": 1})
        update = mocks["table"].update_item.call_args.kwargs
        assert update["ExpressionAttributeValues"][":state"] == "executing"
        assert update["ExpressionAttributeValues"][":pending"] == "pending_approval"
        assert update["ExpressionAttributeValues"][":reason"] == "go"
        assert update["ExpressionAttributeValues"][":actor"] == USER
        assert "#s = :pending" in update["ConditionExpression"]
        mocks["execute"].assert_called_once_with(CASCADE_ID)
        audit = mocks["audit"].call_args
        assert audit.kwargs["event_type"] == "cascade_approved"
        assert audit.args == (DB, ASSET)
        assert body_of(response)["result"] == {"status": "completed", "evaluated": 1}

    def test_reject_moves_pending_to_aborted_without_executing(self):
        response, mocks = _run(rest_event("POST", REJECT_PATH, ID_PARAMS, body={"reason": "no"}))
        update = mocks["table"].update_item.call_args.kwargs
        assert update["ExpressionAttributeValues"][":state"] == "aborted"
        assert update["ExpressionAttributeValues"][":reason"] == "no"
        assert "completedAt = :now" in update["UpdateExpression"]
        assert "#s = :pending" in update["ConditionExpression"]
        mocks["execute"].assert_not_called()
        assert mocks["audit"].call_args.kwargs["event_type"] == "cascade_rejected"
        assert response["statusCode"] == 200

    @pytest.mark.parametrize("path", [APPROVE_PATH, REJECT_PATH])
    def test_a_cascade_that_is_not_pending_is_refused(self, path):
        response, mocks = _run(rest_event("POST", path, ID_PARAMS, body={}),
                               transition_succeeds=False)
        assert response["statusCode"] == 400
        assert "not in pending_approval state" in body_of(response)["message"]
        mocks["execute"].assert_not_called()
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


def _link(from_asset, to_asset, database_id=DB):
    return {"fromAssetDatabaseId": database_id, "fromAssetId": from_asset,
            "toAssetDatabaseId": database_id, "toAssetId": to_asset}


class Graph:
    """An asset-link DAG served through the store's two link readers."""

    def __init__(self, edges):
        self.edges = [_link(a, b) for a, b in edges]

    def children(self, database_id, asset_id):
        return [e for e in self.edges if e["fromAssetId"] == asset_id]

    def parents(self, database_id, asset_id):
        return [e for e in self.edges if e["toAssetId"] == asset_id]


def _run_executor(graph, cascade_item, schema_for=None, evaluation=None):
    cascade_table = MagicMock(name="cascade_table")
    cascade_table.get_item.return_value = {"Item": dict(cascade_item)} if cascade_item else {}
    evaluation = evaluation or (lambda db, asset, schema, actor: {
        "evaluationId": f"eval-{asset}", "verdict": "compliant"})
    schema_for = schema_for or (lambda asset: "schema-1")
    with patch(f"{EXECUTOR}.cascade_table", cascade_table), \
            patch(f"{STORE}.get_child_links", side_effect=graph.children), \
            patch(f"{STORE}.get_parent_links", side_effect=graph.parents), \
            patch(f"{STORE}.get_compliance_record",
                  side_effect=lambda db, asset: {"schemaName": schema_for(asset)}), \
            patch(f"{STORE}.run_evaluation", side_effect=evaluation) as run_evaluation, \
            patch(f"{STORE}.write_audit") as write_audit:
        result = executor.execute_cascade(CASCADE_ID)
    return result, {"table": cascade_table, "run_evaluation": run_evaluation, "audit": write_audit}


EXECUTING = dict(PENDING, state="executing")


@pytest.mark.unit
class TestExecutor:

    def test_descendants_are_evaluated_parents_before_children(self):
        # A -> B, A -> C, B -> D, C -> D: D must follow both B and C whichever order BFS found them.
        graph = Graph([(ASSET, "B"), (ASSET, "C"), ("B", "D"), ("C", "D")])
        result, mocks = _run_executor(graph, EXECUTING)
        evaluated = [c.args[1] for c in mocks["run_evaluation"].call_args_list]
        assert set(evaluated) == {"B", "C", "D"}
        assert evaluated.index("D") > evaluated.index("B")
        assert evaluated.index("D") > evaluated.index("C")
        assert {c.args[3] for c in mocks["run_evaluation"].call_args_list} == {"cascade"}
        assert result["status"] == "completed"
        assert result["evaluated"] == 3
        assert {r["node"]: r["status"] for r in result["results"]} == {
            f"{DB}:B": "compliant", f"{DB}:C": "compliant", f"{DB}:D": "compliant"}

    def test_the_source_itself_is_not_re_evaluated(self):
        graph = Graph([(ASSET, "B"), ("B", ASSET)])
        _, mocks = _run_executor(graph, EXECUTING)
        assert [c.args[1] for c in mocks["run_evaluation"].call_args_list] == ["B"]

    def test_the_execution_order_and_node_states_land_on_the_cascade_row(self):
        graph = Graph([(ASSET, "B"), ("B", "C")])
        _, mocks = _run_executor(graph, EXECUTING,
                                 schema_for=lambda asset: "" if asset == "C" else "s")
        updates = [c.kwargs for c in mocks["table"].update_item.call_args_list]
        first = updates[0]["ExpressionAttributeValues"]
        assert json.loads(first[":order"]) == [f"{DB}:B", f"{DB}:C"]
        assert json.loads(first[":nodes"]) == {f"{DB}:B": "pending", f"{DB}:C": "pending"}
        assert first[":total"] == 2
        final_nodes = json.loads(
            [u for u in updates if ":nodes" in u["ExpressionAttributeValues"]
             and ":order" not in u["ExpressionAttributeValues"]][-1]["ExpressionAttributeValues"][":nodes"])
        assert final_nodes == {f"{DB}:B": "compliant", f"{DB}:C": "skipped"}
        completion = updates[-1]["ExpressionAttributeValues"]
        assert completion[":state"] == "completed"
        assert [r["status"] for r in json.loads(completion[":results"])] == ["compliant", "skipped"]

    def test_a_node_without_a_schema_is_skipped(self):
        graph = Graph([(ASSET, "B")])
        result, mocks = _run_executor(graph, EXECUTING, schema_for=lambda asset: "")
        mocks["run_evaluation"].assert_not_called()
        assert result["results"] == [{"node": f"{DB}:B", "status": "skipped"}]

    def test_a_node_whose_evaluation_raises_is_recorded_as_error(self):
        graph = Graph([(ASSET, "B"), (ASSET, "C")])

        def evaluation(db, asset, schema, actor):
            if asset == "B":
                raise RuntimeError("boom")
            return {"evaluationId": "e", "verdict": "quarantined"}

        result, _ = _run_executor(graph, EXECUTING, evaluation=evaluation)
        statuses = {r["node"]: r["status"] for r in result["results"]}
        assert statuses == {f"{DB}:B": "error", f"{DB}:C": "quarantined"}

    def test_discovery_stops_at_the_node_cap(self):
        edges = [(ASSET, "n0")] + [(f"n{i}", f"n{i + 1}") for i in range(executor.MAX_CASCADE_NODES + 5)]
        graph = Graph(edges)
        with patch(f"{STORE}.get_child_links", side_effect=graph.children):
            descendants = executor.discover_all_descendants(DB, ASSET)
        assert len(descendants) == executor.MAX_CASCADE_NODES

    def test_a_cycle_does_not_revisit_a_node(self):
        graph = Graph([(ASSET, "B"), ("B", "C"), ("C", "B")])
        with patch(f"{STORE}.get_child_links", side_effect=graph.children):
            descendants = executor.discover_all_descendants(DB, ASSET)
        assert [d["assetId"] for d in descendants] == ["B", "C"]

    def test_no_descendants_completes_the_cascade_immediately(self):
        result, mocks = _run_executor(Graph([]), EXECUTING)
        assert result == {"cascadeId": CASCADE_ID, "status": "completed", "evaluated": 0,
                          "results": []}
        completion = mocks["table"].update_item.call_args.kwargs["ExpressionAttributeValues"]
        assert completion[":state"] == "completed"
        mocks["run_evaluation"].assert_not_called()

    def test_completion_is_audited_and_notified(self, notifications_aws):
        notifications_aws.dynamodb_client.query.return_value = {"Items": [
            {"assetName": {"S": "Root"}, "snsTopic": {"S": "arn:aws:sns:us-east-1:1:t"}}]}
        _, mocks = _run_executor(Graph([(ASSET, "B")]), EXECUTING)
        audit = mocks["audit"].call_args.kwargs
        assert audit["event_type"] == "cascade_completed"
        assert audit["actor"] == "SYSTEM_USER"
        assert audit["details"] == {"nodesEvaluated": 1}
        published = notifications_aws.sns_client.publish.call_args.kwargs
        assert "CASCADE COMPLETE" in published["Subject"]
        assert "compliant: 1" in published["Message"]

    @pytest.mark.parametrize("item,error", [
        (None, "Cascade not found"), (PENDING, "Cascade not in executing state"),
    ])
    def test_a_cascade_that_is_not_executing_is_refused(self, item, error):
        result, mocks = _run_executor(Graph([(ASSET, "B")]), item)
        assert result == {"cascadeId": CASCADE_ID, "error": error}
        mocks["run_evaluation"].assert_not_called()
        mocks["table"].update_item.assert_not_called()
