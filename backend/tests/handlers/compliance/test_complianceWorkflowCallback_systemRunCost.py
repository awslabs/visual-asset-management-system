# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the compliance workflow callback costs for a completion no compliance rule owns or waits on.

Every SYSTEM GenAI metadata run announces its completion once, through the same writers as any
workflow run: an upload's own run (no execution group), a reindex launch (`vec-{runId}-{chunk}`),
the unarchive relaunch, and the asset summary run. For each, the callback reads the
ExecutionIdIndex (the run is not a compliance execution) and the PipelineRuleWaitIndex of the
workflow (no rule waits), and, when the event names an execution group, one strongly consistent
GetItem of that group id as an evaluation still launching. Nothing is written and nothing is
invoked. A 10,000-file backfill therefore costs 20,000 index queries and 10,000 GetItems, all
returning nothing, beside its 10,000 callback invocations.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance.test_complianceWorkflowCallback import completion_event
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceWorkflowCallback as callback

GROUPS = [None, "vec-11111111-2222-4333-8444-555555555555-0007"]


def _run(execution_group_id):
    table = MagicMock(name="evaluation_table")
    table.query.return_value = {"Items": []}
    table.get_item.return_value = {}
    lambda_client = MagicMock(name="lambda_client")
    with patch.object(store, "evaluation_table", table), \
            patch.object(store, "lambda_client", lambda_client):
        response = callback.lambda_handler(
            completion_event(execution_group_id=execution_group_id), MagicMock())
    return response, table, lambda_client


@pytest.mark.unit
@pytest.mark.parametrize("execution_group_id", GROUPS, ids=["upload-run", "reindex-run"])
def test_a_completion_no_rule_owns_or_waits_on_reads_two_indexes_and_writes_nothing(
        execution_group_id):
    response, table, lambda_client = _run(execution_group_id)
    assert response["statusCode"] == 200
    assert sorted(call.kwargs["IndexName"] for call in table.query.call_args_list) == \
        ["ExecutionIdIndex", store.PIPELINE_WAIT_INDEX]
    expected_gets = 1 if execution_group_id else 0
    assert table.get_item.call_count == expected_gets
    if expected_gets:
        assert table.get_item.call_args.kwargs["Key"] == {"evaluationId": execution_group_id}
        assert table.get_item.call_args.kwargs.get("ConsistentRead") is True
    table.put_item.assert_not_called()
    table.update_item.assert_not_called()
    lambda_client.invoke.assert_not_called()
    assert json.dumps(response)  # serializable answer, as EventBridge expects
