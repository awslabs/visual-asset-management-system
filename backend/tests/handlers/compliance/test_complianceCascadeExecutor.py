# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceCascadeExecutor: the Lambda entry point (a `{"cascadeId"}` event runs the cascade; an
event without a UUID cascadeId is rejected without a write; a run that raises leaves the row
`aborted` with an `abortReason`, never `executing`), and execute_cascade itself -- descendant
discovery through the asset-link DAG, topological order, the node cap, per-node states and the
terminal `completed` write with `completedAt`."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, CASCADE_ID, DB, update_values,
)
from handlers.compliance import complianceCascadeExecutor as executor

EXECUTOR = "handlers.compliance.complianceCascadeExecutor"
STORE = "handlers.compliance.complianceEvaluationStore"

PENDING = {"cascadeId": CASCADE_ID, "state": "pending_approval",
           "triggeredByDatabaseId": DB, "triggeredByAssetId": ASSET}
EXECUTING = dict(PENDING, state="executing")


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


def _patched(graph, cascade_item, schema_for=None, evaluation=None, cascade_table=None):
    cascade_table = cascade_table if cascade_table is not None else MagicMock(name="cascade_table")
    cascade_table.get_item.return_value = {"Item": dict(cascade_item)} if cascade_item else {}
    evaluation = evaluation or (lambda db, asset, schema, actor: {
        "evaluationId": f"eval-{asset}", "verdict": "compliant"})
    schema_for = schema_for or (lambda asset: "schema-1")
    patches = [
        patch(f"{EXECUTOR}.cascade_table", cascade_table),
        patch(f"{STORE}.get_child_links", side_effect=graph.children),
        patch(f"{STORE}.get_parent_links", side_effect=graph.parents),
        patch(f"{STORE}.get_compliance_record",
              side_effect=lambda db, asset: {"schemaName": schema_for(asset)}),
        patch(f"{STORE}.run_evaluation", side_effect=evaluation),
        patch(f"{STORE}.write_audit"),
    ]
    return cascade_table, patches


def _run_executor(graph, cascade_item, schema_for=None, evaluation=None):
    cascade_table, patches = _patched(graph, cascade_item, schema_for, evaluation)
    started = [p.start() for p in patches]
    try:
        result = executor.execute_cascade(CASCADE_ID)
    finally:
        for p in reversed(patches):
            p.stop()
    return result, {"table": cascade_table, "run_evaluation": started[4], "audit": started[5]}


def _run_handler(event, graph=None, cascade_item=EXECUTING, evaluation=None, cascade_table=None):
    cascade_table, patches = _patched(graph or Graph([(ASSET, "B")]), cascade_item,
                                      evaluation=evaluation, cascade_table=cascade_table)
    started = [p.start() for p in patches]
    try:
        result = executor.lambda_handler(event, MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()
    return result, {"table": cascade_table, "run_evaluation": started[4], "audit": started[5]}


def _states_written(table):
    return [u["state"] for u in update_values(table) if "state" in u]


@pytest.mark.unit
class TestLambdaHandler:

    def test_a_valid_event_runs_the_cascade_to_completion(self):
        result, mocks = _run_handler({"cascadeId": CASCADE_ID})
        assert result["status"] == "completed"
        assert result["cascadeId"] == CASCADE_ID
        assert [c.args[1] for c in mocks["run_evaluation"].call_args_list] == ["B"]
        assert _states_written(mocks["table"]) == ["completed"]
        completion = update_values(mocks["table"])[-1]
        assert completion["completedAt"]

    @pytest.mark.parametrize("event", [
        None, [], "5b1e2c3d-0000-4000-8000-000000000001", {}, {"cascadeId": None},
        {"cascadeId": 42}, {"cascadeId": ""}, {"cascadeId": "not-a-uuid-zq9"},
        {"cascadeId": {"cascadeId": CASCADE_ID}}, {"cascade_id": CASCADE_ID},
    ], ids=["none", "list", "bare-string", "empty", "null-id", "int-id", "empty-id", "not-uuid",
            "nested", "wrong-key"])
    def test_an_event_without_a_uuid_cascade_id_is_rejected_without_a_read_or_write(self, event):
        result, mocks = _run_handler(event)
        assert result == {"error": "Invalid cascade executor event"}
        assert "zq9" not in json.dumps(result)
        mocks["table"].get_item.assert_not_called()
        mocks["table"].update_item.assert_not_called()
        mocks["run_evaluation"].assert_not_called()

    def test_a_run_that_raises_marks_the_row_aborted_with_a_reason(self):
        # A failure inside the per-node loop is recorded on the node and the run continues; a
        # failure OUTSIDE it (here, the progress write to the cascade row) is what reaches the
        # handler, and it must not leave the row `executing` with nothing running it.
        cascade_table = MagicMock(name="cascade_table")
        writes = {"count": 0}

        def failing_progress_write(**kwargs):
            writes["count"] += 1
            if writes["count"] == 1:
                raise RuntimeError("dynamodb unavailable")

        cascade_table.update_item.side_effect = failing_progress_write
        result, mocks = _run_handler({"cascadeId": CASCADE_ID}, cascade_table=cascade_table)
        assert result == {"cascadeId": CASCADE_ID, "status": "aborted",
                          "error": "Cascade execution failed"}
        assert _states_written(mocks["table"]) == ["aborted"]
        aborted = update_values(mocks["table"])[-1]
        assert aborted["abortReason"] == "Cascade execution failed"
        assert aborted["completedAt"]
        assert mocks["table"].update_item.call_args.kwargs["Key"] == {"cascadeId": CASCADE_ID}

    def test_a_failure_before_any_node_runs_still_aborts_the_row(self):
        graph = Graph([(ASSET, "B")])
        cascade_table = MagicMock(name="cascade_table")
        cascade_table.get_item.return_value = {"Item": dict(EXECUTING)}
        with patch(f"{EXECUTOR}.cascade_table", cascade_table), \
                patch(f"{STORE}.get_child_links", side_effect=RuntimeError("links unavailable")), \
                patch(f"{STORE}.run_evaluation") as run_evaluation:
            result = executor.lambda_handler({"cascadeId": CASCADE_ID}, MagicMock())
        assert result["status"] == "aborted"
        run_evaluation.assert_not_called()
        assert _states_written(cascade_table) == ["aborted"]

    def test_an_abort_write_that_itself_fails_raises_so_the_invocation_is_retried(self):
        # With the row still `executing`, surfacing the failure is what gets it out of that state:
        # the asynchronous invocation is retried and the retry runs the cascade again.
        cascade_table = MagicMock(name="cascade_table")
        cascade_table.get_item.return_value = {"Item": dict(EXECUTING)}
        cascade_table.update_item.side_effect = RuntimeError("dynamodb unavailable")
        with patch(f"{EXECUTOR}.cascade_table", cascade_table), \
                patch(f"{STORE}.get_child_links", side_effect=Graph([(ASSET, "B")]).children), \
                patch(f"{STORE}.get_parent_links", return_value=[]), \
                patch(f"{STORE}.get_compliance_record", return_value={"schemaName": "s"}), \
                patch(f"{STORE}.run_evaluation"), patch(f"{STORE}.write_audit"):
            with pytest.raises(RuntimeError):
                executor.lambda_handler({"cascadeId": CASCADE_ID}, MagicMock())

    def test_the_abort_is_conditional_on_the_row_still_executing(self):
        # A failure AFTER the completion write (here the completion audit entry) must not flip a
        # `completed` row to `aborted`: the abort write is conditioned on `executing`, and the
        # conditional failure is absorbed.
        cascade_table = MagicMock(name="cascade_table")
        cascade_table.get_item.return_value = {"Item": dict(EXECUTING)}
        conditional_failure = executor.dynamodb.meta.client.exceptions.ConditionalCheckFailedException(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")

        def update_item(**kwargs):
            if "ConditionExpression" in kwargs:
                raise conditional_failure

        cascade_table.update_item.side_effect = update_item
        graph = Graph([(ASSET, "B")])
        with patch(f"{EXECUTOR}.cascade_table", cascade_table), \
                patch(f"{STORE}.get_child_links", side_effect=graph.children), \
                patch(f"{STORE}.get_parent_links", side_effect=graph.parents), \
                patch(f"{STORE}.get_compliance_record", return_value={"schemaName": "s"}), \
                patch(f"{STORE}.run_evaluation",
                      return_value={"evaluationId": "e", "verdict": "compliant"}), \
                patch(f"{STORE}.write_audit", side_effect=RuntimeError("audit unavailable")):
            result = executor.lambda_handler({"cascadeId": CASCADE_ID}, MagicMock())
        assert result["status"] == "aborted"
        writes = update_values(cascade_table)
        assert [w["state"] for w in writes if "state" in w] == ["completed", "aborted"]
        abort = cascade_table.update_item.call_args.kwargs
        condition = abort["ConditionExpression"]
        assert ":executing" in condition
        assert abort["ExpressionAttributeValues"][":executing"] == "executing"

    @pytest.mark.parametrize("item,error", [
        (None, "Cascade not found"), (PENDING, "Cascade not in executing state"),
    ])
    def test_a_cascade_that_is_not_executing_is_left_alone(self, item, error):
        result, mocks = _run_handler({"cascadeId": CASCADE_ID}, cascade_item=item)
        assert result == {"cascadeId": CASCADE_ID, "error": error}
        mocks["run_evaluation"].assert_not_called()
        mocks["table"].update_item.assert_not_called()


@pytest.mark.unit
class TestExecuteCascade:

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
        assert completion[":now"]
        assert [r["status"] for r in json.loads(completion[":results"])] == ["compliant", "skipped"]

    def test_the_terminal_write_carries_state_and_completed_at(self):
        _, mocks = _run_executor(Graph([(ASSET, "B")]), EXECUTING)
        terminal = update_values(mocks["table"])[-1]
        assert terminal["state"] == "completed"
        assert terminal["completedAt"]
        assert json.loads(terminal["results"]) == [
            {"node": f"{DB}:B", "status": "compliant", "evaluationId": "eval-B"}]

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

        result, mocks = _run_executor(graph, EXECUTING, evaluation=evaluation)
        statuses = {r["node"]: r["status"] for r in result["results"]}
        assert statuses == {f"{DB}:B": "error", f"{DB}:C": "quarantined"}
        # A node failure does not abort the cascade: the row still ends `completed`.
        assert _states_written(mocks["table"]) == ["completed"]

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
        completion = update_values(mocks["table"])[-1]
        assert completion["state"] == "completed"
        assert completion["completedAt"]
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
