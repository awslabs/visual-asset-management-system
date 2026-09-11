# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-029: the main execution row's terminal status must be written atomically.

Several writers can finalize the same main row -- the error-handler lambda, the end-state lambda and
the abort API. `finalize_main_row` wrote the terminal status with no ConditionExpression, so a
concurrent abort and task failure each landed on top of the other and the execution reported whichever
write was last rather than what actually happened. The read-then-write the error handler performs
before calling (`reconcile_failed_execution` re-reads `executionStatus` first) narrows that window but
cannot close it.

The guard is the condition `set_pipeline_status` already applies to a pipeline row
(`attribute_not_exists(executionStatus) OR NOT executionStatus IN (...terminal)`), with ONLY
ConditionalCheckFailedException swallowed: losing that race is the expected outcome for the second
writer, while a throttle or a permissions failure must still surface rather than leave a row reporting
a non-terminal status forever.

The abort path's own main-row write (`executionService._persist_reconciled_main_row`) is the other
writer in this race; it carries the same guard, opt-in per caller, and is covered by
test_terminal_status_conditional_writes.py alongside the read paths that share that helper and must
stay unconditional. That file is the ratchet for the fix; this one is the behavioural suite -- the two
overlap on the "already terminal row is not regressed" case deliberately, because the ratchet asserts
the condition is emitted while the tests here evaluate it against the real DynamoDB parser."""

import os

import pytest
from unittest.mock import MagicMock, patch

# Env vars the error-handler lambda reads at import time.
for _k, _v in {
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
}.items():
    os.environ.setdefault(_k, _v)

from backend.backend.common.workflows import executionOutputs as eo
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh

EXEC_ID = "e2000000000000000000000000000002"
MAIN_KEY = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": "db:wf"}


def _client_error(code):
    """A botocore ClientError carrying `code` -- the shape DynamoDB raises for a failed write."""
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": code}}, "UpdateItem")


def _main_table():
    """A moto-backed V2 main execution table (PK workflowExecutionId, SK workflowDatabaseId:workflowId)."""
    import boto3
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName="main-race",
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    return ddb, ddb.Table("main-race")


@pytest.mark.unit
class TestSecondTerminalWriteLosesTheRace:
    """Exercised against the real DynamoDB expression parser, so the condition is evaluated rather
    than string-matched."""

    @pytest.mark.aws
    def test_an_already_terminal_row_keeps_its_status_and_nothing_raises(self):
        """An abort that already recorded ABORTED is not reverted by a later FAILED finalization.

        The whole write is rejected, so the stop date, log and error the winning writer recorded stay
        as they were -- a partial overwrite would be as wrong as a full one. The loser must not raise:
        both callers run this on a failure path where an exception would mask the original outcome."""
        from moto import mock_aws
        with mock_aws():
            ddb, table = _main_table()
            table.put_item(Item=dict(MAIN_KEY, executionStatus="ABORTED",
                                     executionStopDate="2026-01-01T00:00:00Z",
                                     executionLog="abort log", executionError=""))

            eo.finalize_main_row(ddb, "main-race", EXEC_ID, "db", "wf", "FAILED",
                                 "2026-02-02T00:00:00Z",
                                 execution_log="task failure log", execution_error="States.TaskFailed")

            item = table.get_item(Key=MAIN_KEY)["Item"]
            assert item["executionStatus"] == "ABORTED"
            assert item["executionStopDate"] == "2026-01-01T00:00:00Z"
            assert item["executionLog"] == "abort log"
            assert item["executionError"] == ""
            assert "lastSfnSyncCheckDate" not in item

    @pytest.mark.aws
    def test_every_terminal_status_blocks_a_later_write(self):
        """All four terminal statuses are guarded, not only the one the fix was written against."""
        from moto import mock_aws
        with mock_aws():
            ddb, table = _main_table()
            for index, terminal in enumerate(eo.TERMINAL_STATUSES):
                key = {"workflowExecutionId": f"e{index}", "workflowDatabaseId:workflowId": "db:wf"}
                table.put_item(Item=dict(key, executionStatus=terminal))
                eo.finalize_main_row(ddb, "main-race", f"e{index}", "db", "wf", "SUCCEEDED",
                                     "2026-02-02T00:00:00Z")
                assert table.get_item(Key=key)["Item"]["executionStatus"] == terminal

    @pytest.mark.aws
    def test_the_first_terminal_write_still_lands(self):
        """Control: the guard must not reject the normal case.

        Without this, the tests above are satisfied by a condition that rejects every write -- which
        would leave every execution stuck reporting RUNNING. A row with no status yet and a RUNNING row
        must both advance, with the log and error attached."""
        from moto import mock_aws
        with mock_aws():
            ddb, table = _main_table()
            running_key = {"workflowExecutionId": "e-running", "workflowDatabaseId:workflowId": "db:wf"}
            table.put_item(Item=dict(MAIN_KEY))
            table.put_item(Item=dict(running_key, executionStatus="RUNNING"))

            eo.finalize_main_row(ddb, "main-race", EXEC_ID, "db", "wf", "SUCCEEDED",
                                 "2026-02-02T00:00:00Z")
            eo.finalize_main_row(ddb, "main-race", "e-running", "db", "wf", "FAILED",
                                 "2026-02-02T00:00:00Z",
                                 execution_log="full log", execution_error="boom")

            first = table.get_item(Key=MAIN_KEY)["Item"]
            assert first["executionStatus"] == "SUCCEEDED"
            assert first["executionStopDate"] == "2026-02-02T00:00:00Z"
            assert first["lastSfnSyncCheckDate"] == "2026-02-02T00:00:00Z"
            second = table.get_item(Key=running_key)["Item"]
            assert second["executionStatus"] == "FAILED"
            assert second["executionLog"] == "full log"
            assert second["executionError"] == "boom"


@pytest.mark.unit
class TestOnlyTheLostRaceIsSwallowed:
    """Which failures the guard may absorb. The swallow is the whole risk of this change: too broad
    and a real write failure disappears, leaving the row reporting a non-terminal status forever."""

    def _dynamo_raising(self, error):
        table = MagicMock()
        table.update_item.side_effect = error
        return MagicMock(Table=MagicMock(return_value=table))

    def test_a_conditional_check_failure_does_not_reach_the_caller(self):
        dynamo = self._dynamo_raising(_client_error("ConditionalCheckFailedException"))
        eo.finalize_main_row(dynamo, "main-tbl", EXEC_ID, "db", "wf", "FAILED",
                             "2026-02-02T00:00:00Z")

    @pytest.mark.parametrize("code", ["ProvisionedThroughputExceededException",
                                      "AccessDeniedException",
                                      "ResourceNotFoundException"])
    def test_any_other_write_failure_propagates(self, code):
        """Control against an over-broad `except Exception`. A throttle, a missing IAM grant and a
        missing table are real defects; swallowing them would report the execution as reconciled while
        the stored status never changed."""
        dynamo = self._dynamo_raising(_client_error(code))
        with pytest.raises(Exception, match=code):
            eo.finalize_main_row(dynamo, "main-tbl", EXEC_ID, "db", "wf", "FAILED",
                                 "2026-02-02T00:00:00Z")


@pytest.mark.unit
class TestTheGuardCannotDriftFromThePipelineRowGuard:
    """The main-row and pipeline-row writes must keep using one condition. Two hand-maintained copies
    drift, and a drifted copy is invisible: the write still succeeds, it just stops guarding."""

    def test_both_writes_emit_the_identical_condition(self):
        main_table = MagicMock()
        pexec_table = MagicMock()
        eo.finalize_main_row(MagicMock(Table=MagicMock(return_value=main_table)),
                             "main-tbl", EXEC_ID, "db", "wf", "SUCCEEDED", "2026-02-02T00:00:00Z")
        eo.set_pipeline_status(MagicMock(Table=MagicMock(return_value=pexec_table)),
                               "pexec-tbl", "P1", EXEC_ID, "SUCCEEDED",
                               stop_date="2026-02-02T00:00:00Z")

        main_kwargs = main_table.update_item.call_args.kwargs
        pexec_kwargs = pexec_table.update_item.call_args.kwargs
        assert main_kwargs["ConditionExpression"] == pexec_kwargs["ConditionExpression"]
        assert "attribute_not_exists(executionStatus)" in main_kwargs["ConditionExpression"]
        assert "NOT executionStatus IN (" in main_kwargs["ConditionExpression"]
        # Every terminal status is a bound value, so the IN list is not silently short.
        assert set(eo.TERMINAL_STATUSES).issubset(set(main_kwargs["ExpressionAttributeValues"].values()))
        assert main_kwargs["ExpressionAttributeValues"][":st"] == "SUCCEEDED"


@pytest.mark.unit
class TestTheErrorHandlerLambdaReachesTheGuardedWrite:
    """The production call path: SFN Catch -> handleExecutionError.lambda_handler ->
    reconcile_failed_execution -> eo.finalize_main_row -> UpdateItem. `finalize_main_row` is NOT
    patched here, so the condition has to be on the call the deployed lambda actually makes -- the
    handler's own read-then-check of the stored status is exactly the window this closes."""

    def test_the_failed_finalization_is_condition_guarded(self):
        body = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId": "db", "workflowId": "wf"}
        logs_table = MagicMock()
        # The status read the handler performs before finalizing: still in flight, so it finalizes.
        main_table = MagicMock(query=MagicMock(return_value={"Items": [{"executionStatus": "RUNNING"}]}))

        def _table(name):
            return logs_table if name == heh.pipeline_execution_logs_table else main_table

        with patch.object(heh, "_get_pipeline_rows", return_value=[]), \
             patch.object(heh, "_fetch_execution_log", return_value="full log"), \
             patch.object(heh.dynamodb, "Table", side_effect=_table):
            resp = heh.lambda_handler(
                {"body": body, "errorInfo": {"Error": "States.TaskFailed"}}, MagicMock())

        # The return value is NOT the evidence: the handler returns {"handled": True} from its blanket
        # except branch too, so it reads the same whether the write happened or threw. That the write
        # was made exactly once is the assertion that pins the production path.
        assert resp == {"handled": True}
        main_table.update_item.assert_called_once()
        kwargs = main_table.update_item.call_args.kwargs
        assert kwargs["Key"] == MAIN_KEY
        assert "attribute_not_exists(executionStatus)" in kwargs["ConditionExpression"]
        assert "NOT executionStatus IN (" in kwargs["ConditionExpression"]
        values = kwargs["ExpressionAttributeValues"]
        assert values[":st"] == heh.FAILED_STATUS
        assert set(eo.TERMINAL_STATUSES).issubset(set(values.values()))
