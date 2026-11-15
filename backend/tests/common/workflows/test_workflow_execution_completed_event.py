# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `workflow.execution.completed` orchestration-bus entry: a pure builder whose output is the
contract between the three emitters (end-state lambda, error handler, abort API) and the compliance
workflow callback that consumes it. The detail carries identifiers and timestamps only."""

import json

import pytest

from backend.backend.common.workflows import executionRecords as er

BUS = "arn:aws:events:us-east-1:123456789012:event-bus/vams-orchestration"
PREFIX = "vams.orchestration"
ARGS = dict(event_bus_arn=BUS, event_source_prefix=PREFIX, execution_id="exec-1",
            workflow_database_id="wf-db", workflow_id="wf-1", status="SUCCEEDED",
            started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:01:00+00:00")


@pytest.mark.unit
class TestWorkflowExecutionCompletedEvent:

    def test_the_entry_targets_the_bus_under_the_execution_source(self):
        entry = er.workflow_execution_completed_event(**ARGS)
        assert entry["EventBusName"] == BUS
        assert entry["Source"] == f"{PREFIX}.execution.exec-1"
        assert entry["DetailType"] == "workflow.execution.completed"
        assert entry["DetailType"] == er.WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE

    def test_the_source_starts_with_the_prefix_the_cdk_rule_matches_on(self):
        assert er.workflow_execution_completed_source(PREFIX, "exec-1").startswith(PREFIX)
        assert er.workflow_execution_completed_source(PREFIX, "exec-1") == \
            er.workflow_execution_completed_event(**ARGS)["Source"]

    def test_the_detail_carries_identifiers_and_timestamps_only(self):
        detail = json.loads(er.workflow_execution_completed_event(**ARGS)["Detail"])
        assert detail == {
            "executionId": "exec-1",
            "workflowDatabaseId": "wf-db",
            "workflowId": "wf-1",
            "status": "SUCCEEDED",
            "startedAt": "2026-01-01T00:00:00+00:00",
            "completedAt": "2026-01-01T00:01:00+00:00",
        }

    def test_an_execution_group_id_is_added_only_when_present(self):
        with_group = er.workflow_execution_completed_event(**ARGS, execution_group_id="grp-1")
        assert json.loads(with_group["Detail"])["executionGroupId"] == "grp-1"
        without_group = er.workflow_execution_completed_event(**ARGS, execution_group_id="")
        assert "executionGroupId" not in json.loads(without_group["Detail"])

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"])
    def test_the_stored_terminal_status_is_passed_through_verbatim(self, status):
        detail = json.loads(er.workflow_execution_completed_event(**dict(ARGS, status=status))["Detail"])
        assert detail["status"] == status

    def test_missing_timestamps_are_empty_strings_not_null(self):
        entry = er.workflow_execution_completed_event(**dict(ARGS, started_at=None, completed_at=""))
        detail = json.loads(entry["Detail"])
        assert detail["startedAt"] == "" and detail["completedAt"] == ""

    def test_the_detail_is_a_json_string_as_put_events_requires(self):
        entry = er.workflow_execution_completed_event(**ARGS)
        assert isinstance(entry["Detail"], str)
        assert set(entry) == {"EventBusName", "Source", "DetailType", "Detail"}
