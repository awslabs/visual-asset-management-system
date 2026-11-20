# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A cascade run that would reach its Lambda deadline continues in a new run, and a cascade whose
run stopped is aborted by the next read instead of staying `executing`.

A cascade used to run in one invocation: one that reached the 15-minute timeout stayed `executing`
for good, and Lambda's two asynchronous retries started it again from the first node, evaluating
and launching the pipeline rules of the nodes already done. Now Lambda does not retry the executor
(its asynchronous invoke configuration, asserted in the infra tests), a run stops before its
deadline and hands the cascade to the next run with the nodes done so far, a run that does not hold
the cascade (`runNumber`) does nothing, and a reader aborts a cascade whose run made no progress for
`CASCADE_RUN_STALE_SECONDS`.

The cascade table is a moto table built from the CDK definition, so each run reads what the one
before it wrote; the hand-over's invocation is captured and delivered to the handler.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import ASSET, CASCADE_ID, DB
from backend.tests.handlers.compliance.test_complianceCascadeExecutor import EXECUTING, Graph
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceEvaluationStore as store

STORE = "handlers.compliance.complianceEvaluationStore"
TRIGGER = "handlers.compliance.complianceTrigger"
FUNCTION_NAME = "complianceCascadeExecutor-test"
PLENTY = 600_000
LITTLE = executor.RUN_DEADLINE_MARGIN_MS - 1


class World:
    """The cascade table, the descendant graph ASSET -> B -> C -> D, the evaluations run, the
    executor's own invocations and the audit entries written."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.evaluated = []
        self.invocations = []
        self.audits = []

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, doubles = _cdkTables.table_doubles(resource, (executor,))
        self.table = doubles["complianceCascadeStorage"]
        graph = Graph([(ASSET, "B"), ("B", "C"), ("C", "D")])
        lambda_client = MagicMock(name="lambda_client")
        lambda_client.invoke.side_effect = lambda **kwargs: self.invocations.append(
            json.loads(kwargs["Payload"]))
        self.patches += [
            patch(f"{STORE}.get_child_links", side_effect=graph.children),
            patch(f"{STORE}.get_parent_links", side_effect=graph.parents),
            patch(f"{STORE}.get_compliance_record",
                  side_effect=lambda db, asset, consistent_read=False: {"schemaName": "schema-1"}),
            patch(f"{STORE}.run_evaluation", side_effect=self._evaluate),
            patch(f"{STORE}.write_audit", side_effect=lambda *a, **k: self.audits.append(k)),
            patch(f"{TRIGGER}.asset_row_exists", return_value=True),
            patch.object(executor, "lambda_client", lambda_client),
        ]
        for active in self.patches:
            active.start()
        self.monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", FUNCTION_NAME)
        self.table.put_item(Item=dict(EXECUTING, createdAt=datetime.now(timezone.utc).isoformat()))
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    def _evaluate(self, database_id, asset_id, schema_name, actor):
        self.evaluated.append(asset_id)
        return {"evaluationId": f"eval-{asset_id}", "verdict": "compliant"}

    def deliver(self, event, remaining):
        """Run the handler on `event` with a context whose remaining time reads `remaining` in turn
        (then its last value)."""
        readings = list(remaining)
        context = MagicMock()
        context.get_remaining_time_in_millis.side_effect = (
            lambda: readings.pop(0) if len(readings) > 1 else readings[0])
        return executor.lambda_handler(event, context)

    def row(self):
        return self.table.get_item(Key={"cascadeId": CASCADE_ID}, ConsistentRead=True)["Item"]


@pytest.fixture
def world(monkeypatch):
    with World(monkeypatch) as w:
        yield w


@pytest.mark.unit
class TestARunNearItsDeadlineHandsOver:

    def test_the_next_run_goes_on_with_the_nodes_not_done(self, world):
        # Two nodes fit in the first run; the third would start too close to its deadline.
        first = world.deliver({"cascadeId": CASCADE_ID}, [PLENTY, PLENTY, LITTLE])
        assert first["continued"] is True and first["run"] == 1
        assert world.evaluated == ["B", "C"]
        row = world.row()
        assert row["state"] == "executing" and int(row["runNumber"]) == 1
        assert json.loads(row["nodes"]) == {f"{DB}:B": "compliant", f"{DB}:C": "compliant",
                                            f"{DB}:D": "pending"}
        assert row["heartbeatAt"]
        assert world.invocations == [{"cascadeId": CASCADE_ID, "run": 1}]

        second = world.deliver(world.invocations[0], [PLENTY])
        assert second["status"] == "completed"
        # Only the node the first run left: none is evaluated (or its pipeline rules launched) twice.
        assert world.evaluated == ["B", "C", "D"]
        row = world.row()
        assert row["state"] == "completed"
        assert [(entry["node"], entry["evaluationId"]) for entry in json.loads(row["results"])] == [
            (f"{DB}:B", "eval-B"), (f"{DB}:C", "eval-C"), (f"{DB}:D", "eval-D")]
        # The earlier run's results are in `results`; the completed row does not keep them twice.
        assert "partialResults" not in row
        assert [a["event_type"] for a in world.audits] == ["cascade_completed"]

    def test_a_run_that_no_longer_holds_the_cascade_does_nothing(self, world):
        world.deliver({"cascadeId": CASCADE_ID}, [PLENTY, LITTLE])
        evaluated, row = list(world.evaluated), world.row()
        # The first run's event delivered again (an asynchronous duplicate).
        repeated = world.deliver({"cascadeId": CASCADE_ID}, [PLENTY])
        assert repeated["error"] == "Cascade held by another run"
        assert world.evaluated == evaluated
        assert world.row() == row

    def test_a_run_whose_successor_cannot_be_invoked_ends_the_cascade_aborted(self, world):
        executor.lambda_client.invoke.side_effect = RuntimeError("throttled")
        result = world.deliver({"cascadeId": CASCADE_ID}, [LITTLE])
        assert result["status"] == "aborted"
        row = world.row()
        assert (row["state"], row["abortReason"]) == (
            "aborted", executor.CONTINUATION_FAILED_ABORT_REASON)

    @pytest.mark.parametrize("run", [-1, "1", True, 1.5])
    def test_an_event_whose_run_is_not_a_count_is_rejected_without_a_write(self, world, run):
        row = world.row()
        result = world.deliver({"cascadeId": CASCADE_ID, "run": run}, [PLENTY])
        assert result == {"error": "Invalid cascade executor event"}
        assert world.row() == row and world.evaluated == []


@pytest.mark.unit
class TestACascadeWhoseRunStoppedIsAbortedByTheNextRead:

    def _executing(self, world, **progress):
        world.table.put_item(Item=dict(EXECUTING, **progress))
        return world.row()

    def test_no_progress_for_the_bound_aborts_it(self, world):
        stale = (datetime.now(timezone.utc) - timedelta(
            seconds=store.CASCADE_RUN_STALE_SECONDS + 60)).isoformat()
        row = self._executing(world, createdAt=stale, heartbeatAt=stale)
        answered = store.expire_timed_out_cascade(world.table, row)
        assert (answered["state"], answered["abortReason"]) == (
            "aborted", store.CASCADE_RUN_STOPPED_REASON)
        assert world.row()["state"] == "aborted"
        assert [(a["event_type"], a["details"]["reason"]) for a in world.audits] == [
            ("cascade_timed_out", store.CASCADE_RUN_STOPPED_REASON)]
        # A later delivery of its run finds it aborted and does nothing.
        assert world.deliver({"cascadeId": CASCADE_ID}, [PLENTY])["error"] == (
            "Cascade not in executing state")
        assert world.evaluated == []

    def test_a_cascade_started_long_ago_without_progress_yet_counts_from_its_start(self, world):
        stale = (datetime.now(timezone.utc) - timedelta(
            seconds=store.CASCADE_RUN_STALE_SECONDS + 60)).isoformat()
        row = self._executing(world, createdAt=stale)
        assert store.expire_timed_out_cascade(world.table, row)["state"] == "aborted"

    def test_a_run_still_making_progress_is_left_alone(self, world):
        now = datetime.now(timezone.utc).isoformat()
        row = self._executing(world, createdAt=now, heartbeatAt=now)
        assert store.expire_timed_out_cascade(world.table, row)["state"] == "executing"
        assert world.audits == []

    def test_progress_made_after_the_read_keeps_it(self, world):
        stale = (datetime.now(timezone.utc) - timedelta(
            seconds=store.CASCADE_RUN_STALE_SECONDS + 60)).isoformat()
        row = self._executing(world, createdAt=stale, heartbeatAt=stale)
        # The run writes progress between the reader's read and its abort.
        world.table.update_item(Key={"cascadeId": CASCADE_ID},
                                UpdateExpression="SET heartbeatAt = :now",
                                ExpressionAttributeValues={
                                    ":now": datetime.now(timezone.utc).isoformat()})
        answered = store.expire_timed_out_cascade(world.table, row)
        assert answered["state"] == "executing"
        assert world.row()["state"] == "executing" and world.audits == []
