# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every terminal writer releases the run's concurrency locks before it announces the completion.

The compliance workflow callback relaunches, on `workflow.execution.completed`, a pipeline rule whose
launch the workflow's lock refused (`complianceEvaluationStore.process_waiting_pipeline_rules`). A
relaunch that reaches the lock while the announced run still holds it is refused again and waits for
another completion of the workflow over that asset, which may never come, so the rule ends as an
error when its wait expires. The read-path reconcilers already released first; the end-state step,
the error handler and the abort (once the state machine is stopped) now do the same, so the event
is published only once the locks are free. A run whose stop is unconfirmed keeps its locks for its
own end-state step or error handler, as before.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.workflows.test_workflow_completion_event_emit import (
    BUS, PREFIX, STARTED, _abort, _conditional_failure, _record_outputs, _reconcile,
    _running_main_item,
)
from backend.backend.handlers.workflows import executionService as le
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po


def _ordered(module):
    """An events client and a lock release that append to one shared `order` list."""
    order = []
    events_client = MagicMock()
    events_client.put_events.side_effect = lambda **kw: order.append("publish")
    release = patch.object(module.el, "release_locks_for_execution",
                           side_effect=lambda *a, **kw: order.append("release") or 1)
    return order, events_client, release


@pytest.mark.unit
class TestTheLocksAreFreeBeforeTheCompletionIsAnnounced:

    def test_the_end_state_step(self):
        order, events_client, release = _ordered(po)
        main_table = MagicMock()
        main_table.update_item.return_value = {"Attributes": {"executionStartDate": STARTED}}
        with release:
            _record_outputs(main_table, events_client)
        assert order == ["release", "publish"]

    def test_the_end_state_step_that_lost_the_terminal_race_still_releases(self):
        order, events_client, release = _ordered(po)
        main_table = MagicMock()
        main_table.update_item.side_effect = _conditional_failure()
        with release:
            _record_outputs(main_table, events_client)
        assert order == ["release"]

    def test_the_end_state_step_whose_recording_raised_still_releases_and_announces_nothing(self):
        order, events_client, release = _ordered(po)
        main_table = MagicMock()
        main_table.update_item.side_effect = RuntimeError("throttled")
        with release, pytest.raises(RuntimeError):
            _record_outputs(main_table, events_client)
        assert order == ["release"]

    def test_the_error_handler(self):
        order, events_client, release = _ordered(heh)
        with release:
            _reconcile({"executionStatus": "RUNNING", "executionStartDate": STARTED}, events_client)
        assert order == ["release", "publish"]

    def test_the_error_handler_that_lost_the_terminal_race_still_releases(self):
        order, events_client, release = _ordered(heh)
        with release:
            _reconcile({"executionStatus": "RUNNING", "executionStartDate": STARTED}, events_client,
                       finalized=False)
        assert order == ["release"]

    def test_the_abort_of_a_stopped_run(self):
        order, events_client, release = _ordered(le)
        with release:
            _abort(_running_main_item(), events_client, persisted=True)
        assert order == ["release", "publish"]

    def test_the_abort_of_a_run_whose_stop_is_unconfirmed_keeps_its_locks(self):
        order, events_client, release = _ordered(le)
        with release, patch.object(le, "_stop_sfn_execution", return_value=False):
            with patch.object(le, "get_execution_main_row", return_value=_running_main_item()), \
                    patch.object(le, "authorize_abort", return_value=(True, "")), \
                    patch.object(le, "get_pipeline_execution_rows", return_value=[]), \
                    patch.object(le, "_persist_reconciled_main_row", return_value=True), \
                    patch.object(le, "log_actions"), \
                    patch.object(le.dynamodb, "Table"), \
                    patch.object(le.eo, "set_pipeline_status"), \
                    patch.object(le, "events_client", events_client), \
                    patch.object(le, "orchestration_bus_arn", BUS), \
                    patch.object(le, "orchestration_event_source_prefix", PREFIX):
                le.abort_execution({}, _running_main_item()["workflowExecutionId"])
        assert order == ["publish"]
