# SPDX-License-Identifier: Apache-2.0

"""End to end on the combined branch: #1271's overlapping end-state attempts announce FAILED then
SUCCEEDED for one run (D2), and #1504's supersede path makes the compliance rule follow the SUCCEEDED.

The two `workflow.execution.completed` entries are the ones the real end-state step
(`processWorkflowExecutionOutput.record_execution_outputs`) puts on the bus; each is delivered to the
real compliance callback in the EventBridge shape (`Detail` JSON as `detail`, `DetailType` as
`detail-type`), in the order they were published."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance.test_complianceWorkflowCallback import Callback
from backend.tests.handlers.workflows.test_workflow_completion_event_emit import (  # noqa: E402
    BUS, EXEC_ID, PREFIX, _tables, po,
)

pytestmark = pytest.mark.unit


def _announce(status, events_client):
    main_table = MagicMock()
    main_table.update_item.return_value = {"Attributes": {}}
    dynamo = _tables(main_table, MagicMock(), po.workflow_execution_configuration_table)
    with patch.object(po, "events_client", events_client), \
            patch.object(po, "orchestration_bus_arn", BUS), \
            patch.object(po, "orchestration_event_source_prefix", PREFIX), \
            patch.object(po.eo, "set_pipeline_status"):
        assert po.record_execution_outputs(
            dynamo, EXEC_ID, "pexec-1", "GLOBAL", "wf-1", "bucket",
            output_files=[], output_metadata=[], output_results=[], result_log="",
            execution_log="", log_group_arn="", log_stream_name="",
            execution_status=status) is True


def _delivered(events_client):
    """Each published entry as EventBridge delivers it to the callback, in publish order."""
    events = []
    for call in events_client.put_events.call_args_list:
        entry = call.kwargs["Entries"][0]
        events.append({"source": entry["Source"], "detail-type": entry["DetailType"],
                       "detail": json.loads(entry["Detail"])})
    return events


def test_the_retried_runs_succeeded_supersedes_the_rules_failed():
    events_client = MagicMock()
    _announce("FAILED", events_client)
    _announce("SUCCEEDED", events_client)
    first, second = _delivered(events_client)
    assert [first["detail"]["status"], second["detail"]["status"]] == ["FAILED", "SUCCEEDED"]

    harness = Callback()
    rows = harness.evaluation_rows.rows
    rows["eval-1#residual-bound"]["executionId"] = EXEC_ID
    rows["eval-1"]["executionId"] = EXEC_ID

    failed = json.loads(harness.run(first)["body"])
    assert failed["verdict"] == "quarantined"
    succeeded = json.loads(harness.run(second)["body"])
    assert succeeded["superseded"] is True
    assert succeeded["verdict"] == "compliant"
    assert rows["eval-1#residual-bound"]["executionStatus"] == "SUCCEEDED"
    # A redelivery of either announcement changes nothing further.
    assert harness.run(first)["body"] == "Already processed"
    assert harness.run(second)["body"] == "Already processed"
    assert rows["eval-1"]["verdict"] == "compliant"
