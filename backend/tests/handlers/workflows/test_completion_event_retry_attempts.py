# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1271 with the compliance completion event (#749): the end-state write reports whether it landed
(`record_execution_outputs` returns it, so only that attempt records the run's end) AND the landed
write still announces `workflow.execution.completed`. A refused write neither announces nor reports
True. A SUCCEEDED that replaces an earlier attempt's end-state FAILED (#1271 D2) is a landed write
and announces, so a consumer that keeps the first announcement sees FAILED first; the last test pins
that sequence so a change to it is deliberate."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.workflows.test_workflow_completion_event_emit import (  # noqa: E402
    BUS, EXEC_ID, PREFIX, STARTED, _tables, po,
)

pytestmark = pytest.mark.unit


def _refused():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
                       "UpdateItem")


def _record(main_table, events_client, status="SUCCEEDED"):
    dynamo = _tables(main_table, MagicMock(), po.workflow_execution_configuration_table)
    with patch.object(po, "events_client", events_client), \
            patch.object(po, "orchestration_bus_arn", BUS), \
            patch.object(po, "orchestration_event_source_prefix", PREFIX), \
            patch.object(po.eo, "set_pipeline_status"):
        return po.record_execution_outputs(
            dynamo, EXEC_ID, "pexec-1", "wf-db", "wf-1", "bucket",
            output_files=[], output_metadata=[], output_results=[], result_log="", execution_log="",
            log_group_arn="", log_stream_name="", execution_status=status)


def _statuses(events_client):
    return [json.loads(c.kwargs["Entries"][0]["Detail"])["status"]
            for c in events_client.put_events.call_args_list]


def test_a_landed_write_announces_and_reports_it_landed():
    main_table = MagicMock()
    main_table.update_item.return_value = {"Attributes": {"executionStartDate": STARTED}}
    events_client = MagicMock()
    assert _record(main_table, events_client) is True
    assert _statuses(events_client) == ["SUCCEEDED"]


def test_a_refused_write_neither_announces_nor_reports_it_landed():
    main_table = MagicMock()
    main_table.update_item.side_effect = _refused()
    events_client = MagicMock()
    assert _record(main_table, events_client) is False
    assert _statuses(events_client) == []


def test_overlapping_attempts_announce_once_each_landed_write():
    # Attempt 1 records the end; attempt 2's identical SUCCEEDED is refused by the guard.
    main_table = MagicMock()
    main_table.update_item.side_effect = [{"Attributes": {}}, _refused()]
    events_client = MagicMock()
    assert [_record(main_table, events_client), _record(main_table, events_client)] == [True, False]
    assert _statuses(events_client) == ["SUCCEEDED"]


def test_a_succeeded_that_replaces_an_end_state_failed_is_announced_after_it():
    # #1271 D2: an earlier-finishing attempt wrote FAILED, a later attempt's SUCCEEDED replaces it.
    # Both writes land, so both announce; a consumer that keeps the first (the compliance callback's
    # conditional tracking write) records FAILED. Pinned so a change to this sequence is deliberate.
    main_table = MagicMock()
    main_table.update_item.side_effect = [{"Attributes": {}}, {"Attributes": {}}]
    events_client = MagicMock()
    assert _record(main_table, events_client, "FAILED") is True
    assert _record(main_table, events_client, "SUCCEEDED") is True
    assert _statuses(events_client) == ["FAILED", "SUCCEEDED"]
