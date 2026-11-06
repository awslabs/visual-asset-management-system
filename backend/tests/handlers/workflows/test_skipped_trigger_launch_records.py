# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#998: a fileUpload trigger launch refused by a concurrency restriction is recorded as a SKIPPED
execution, one per blocking run and lock key.

Covers the record builders (deterministic uuid5 id, the single atomic UpdateItem, real two-writer
aggregation on a moto table), the execute handler's skip writer (trigger launches only, the 400 is
unchanged, best-effort, the input-row cap), and the read surfaces (list fields, the details `skip`
block and its gated blockingExecutionId, the logs answer, abort/rerun/permanent delete on SKIPPED, and
old rows reading exactly as before)."""

import json
import os

import boto3
import pytest
from moto import mock_aws
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows"),
    ("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
]:
    os.environ.setdefault(_name, _value)

from tests.handlers.workflows.test_execute_per_input_file_version_lock import (  # noqa: E402
    MOD as EXEC_MOD, WF_DB, WF_ID, _LockTable, _run, ewv2,
)
from backend.backend.common import validators  # noqa: E402
from backend.backend.handlers.workflows import executionService as es  # noqa: E402

er = ewv2.er
el = ewv2.el
SVC = "backend.backend.handlers.workflows.executionService"

ONE_FILE = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
            "triggerType": "fileUpload"}
ASSET_KEY = f"{WF_DB}:{WF_ID}|asset|db1:a1"
HOLDER = "e" * 32


def _cross_call_event(body=ONE_FILE):
    # The shape workflowTriggerDispatch sends: no headers, identity in lambdaCrossCall.
    return {
        "requestContext": {"http": {"method": "POST", "path": f"/workflows/{WF_DB}/{WF_ID}/execute"}},
        "pathParameters": {"workflowDatabaseId": WF_DB, "workflowId": WF_ID},
        "queryStringParameters": {},
        "body": json.dumps(body),
        "lambdaCrossCall": {"userName": "SYSTEM_USER"},
    }


class _MainTable:
    """The main execution table: answers the writer's count read and records its UpdateItem, answering
    ALL_NEW with a running count the way DynamoDB's ADD would."""

    def __init__(self, count=0, fail=False, journal=None):
        self.count, self.fail, self.updates = count, fail, []
        self.journal = journal if journal is not None else []

    def update_item(self, **kwargs):
        if self.fail:
            raise RuntimeError("throttled")
        self.journal.append("main")
        self.updates.append(kwargs)
        self.count += 1
        return {"Attributes": {"skippedLaunchCount": self.count,
                               "executionStartDate": "2026-11-06T10:00:00Z",
                               "executionStatus": "SKIPPED"}}

    def get_item(self, **_kwargs):
        return {"Item": {"skippedLaunchCount": self.count}} if self.count else {}

    def put_item(self, **_kwargs):
        raise AssertionError("a refused launch writes no launched-run main row")


class _InputsTable:
    def __init__(self, fail=False, journal=None):
        self.puts, self.fail = [], fail
        self.journal = journal if journal is not None else []

    def put_item(self, **kwargs):
        if self.fail:
            raise RuntimeError("throttled")
        self.journal.append("inputs")
        self.puts.append(kwargs["Item"])
        return {}


class _ConfigTable:
    """The configuration table's conditional put: `existing` stands in for a row an earlier refusal
    wrote, returned (as DynamoDB does) with the failed condition."""

    def __init__(self, existing=None, journal=None):
        self.existing, self.puts = existing, []
        self.journal = journal if journal is not None else []

    def put_item(self, **kwargs):
        self.journal.append("config")
        # The put is conditional (first refusal wins); the moto test below proves the condition holds.
        assert "ConditionExpression" in kwargs
        if self.existing is not None:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"},
                               "Item": {k: {"S": v} for k, v in self.existing.items()}}, "PutItem")
        self.puts.append(kwargs["Item"])
        return {}


def _launch_refused(main, inputs, event=None, tokens=("SYSTEM_USER",), holder=HOLDER, config=None):
    lock_table = _LockTable(held={ASSET_KEY: holder})
    return _run(lock_table, restriction="perAsset", tokens=tokens,
                event=event if event is not None else _cross_call_event(), main_table=main,
                inputs_table=inputs, config_table=config if config is not None else _ConfigTable())


# --------------------------------------------------------------------------- #
# Record builders
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestSkipRecordBuilders:
    def test_the_id_is_deterministic_per_lock_key_and_holder(self):
        a = er.skip_record_execution_id(ASSET_KEY, HOLDER)
        assert a == er.skip_record_execution_id(ASSET_KEY, HOLDER)
        assert a != er.skip_record_execution_id(ASSET_KEY, "f" * 32)
        assert a != er.skip_record_execution_id(ASSET_KEY + "x", HOLDER)

    def test_the_id_passes_the_guid_validator(self):
        valid, message = validators.validate_guid("executionId",
                                                  er.skip_record_execution_id(ASSET_KEY, HOLDER))
        assert valid, message

    def test_the_update_sets_identity_once_and_counts_every_skip(self):
        update = er.build_skip_record_update(
            execution_id="s" * 32, workflow_database_id=WF_DB, workflow_id=WF_ID, workflow_arn="arn",
            skipped_at="2026-11-06T10:00:00Z", skip_scope="asset", skip_reason="busy",
            blocking_execution_id=HOLDER)
        assert update["Key"] == {"workflowExecutionId": "s" * 32,
                                 "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}"}
        expression = update["UpdateExpression"]
        assert expression.endswith(" ADD #count :one")
        assert "#lastSkipped = :lastSkipped" in expression
        names, values = update["ExpressionAttributeNames"], update["ExpressionAttributeValues"]
        by_name = {names[k]: values[":" + k[1:]] for k in names if k.startswith("#a")}
        assert by_name["executionStatus"] == "SKIPPED"
        assert by_name["executionStopDate"] == by_name["executionStartDate"] == "2026-11-06T10:00:00Z"
        assert by_name["triggerType"] == "File-Upload"
        assert by_name["triggeredByUserId"] == "SYSTEM_USER"
        assert by_name["blockingExecutionId"] == HOLDER
        assert by_name["allListPartition"] == er.ALL_EXECUTIONS_LIST_PARTITION
        # Every identity attribute is first-write-wins.
        for key in (k for k in names if k.startswith("#a")):
            assert f"{key} = if_not_exists({key}, :{key[1:]})" in expression
        assert "executionGroupId" not in by_name
        assert update["ReturnValues"] == "ALL_NEW"

    def test_list_fields_are_empty_for_any_other_row(self):
        assert er.skip_list_fields({"executionStatus": "SUCCEEDED"}) == {}
        assert er.skip_list_fields({}) == {}
        assert er.skip_list_fields(None) == {}

    def test_skipped_is_terminal_for_every_writer(self):
        assert "SKIPPED" in ewv2.er.EXECUTION_STATUS_SKIPPED
        from backend.backend.common.workflows import executionOutputs as eo
        assert "SKIPPED" in eo.TERMINAL_STATUSES
        assert "SKIPPED" in es.TERMINAL_STATUSES
        values = {}
        assert ":term4" in eo.not_terminal_condition(values)
        assert "SKIPPED" in values.values()


@pytest.mark.unit
class TestTwoWritersAggregateOnOneRecord:
    """The UpdateItem is real DynamoDB semantics: two writers on one id make one row with a count of
    two and the first writer's dates."""

    @mock_aws
    def test_two_skips_make_one_record_with_a_count_of_two(self):
        client = boto3.resource("dynamodb", region_name="us-east-1")
        table = client.create_table(
            TableName="main", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                       {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "workflowExecutionId", "AttributeType": "S"},
                                  {"AttributeName": "workflowDatabaseId:workflowId",
                                   "AttributeType": "S"}])
        skip_id = er.skip_record_execution_id(ASSET_KEY, HOLDER)
        for when in ("2026-11-06T10:00:00Z", "2026-11-06T10:05:00Z"):
            table.update_item(**er.build_skip_record_update(
                execution_id=skip_id, workflow_database_id=WF_DB, workflow_id=WF_ID,
                workflow_arn="arn", skipped_at=when, skip_scope="asset", skip_reason="busy",
                blocking_execution_id=HOLDER))
        rows = table.scan()["Items"]
        assert len(rows) == 1
        row = rows[0]
        assert row["skippedLaunchCount"] == 2
        assert row["executionStartDate"] == row["executionStopDate"] == "2026-11-06T10:00:00Z"
        assert row["lastSkippedDate"] == "2026-11-06T10:05:00Z"
        assert row["executionStatus"] == "SKIPPED"


# --------------------------------------------------------------------------- #
# The execute handler's skip writer
# --------------------------------------------------------------------------- #

TRIGGER_TEMPLATES = {"pipe1": {"templateId": "trigger-template"}}


@pytest.mark.unit
class TestTheExecuteHandlerRecordsRefusedTriggerLaunches:
    def test_a_refused_trigger_launch_writes_one_skip_record_and_keeps_its_400(self):
        journal = []
        main, inputs, config = (_MainTable(journal=journal), _InputsTable(journal=journal),
                                _ConfigTable(journal=journal))
        body = dict(ONE_FILE, pipelineExecutionParameters=TRIGGER_TEMPLATES)
        response, sfn, _log, _stop = _launch_refused(main, inputs, event=_cross_call_event(body),
                                                     config=config)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == el.LOCK_CONFLICT_MESSAGE_BY_SCOPE["asset"]
        sfn.start_execution.assert_not_called()
        skip_id = er.skip_record_execution_id(ASSET_KEY, HOLDER)
        # The rows the read check is evaluated over exist before the main row lists the record.
        assert journal == ["config", "inputs", "main"]
        assert len(main.updates) == 1
        assert main.updates[0]["Key"]["workflowExecutionId"] == skip_id
        cfg = config.puts[0]
        assert cfg["workflowExecutionId"] == skip_id
        assert cfg["pipelineExecutionParameters"] == TRIGGER_TEMPLATES
        assert (cfg["outputDatabaseId"], cfg["outputAssetId"]) == ("db1", "a1")
        # The trigger named no output extension, so a re-run inherits the workflow's default.
        assert "outputFileBaseExecutionPathExtension" not in cfg
        row = inputs.puts[0]
        # #1009's per-version sort key, the same builder a launched run's rows use, never pinned.
        assert row["databaseId:assetId:inputAssetFileKey"] == er.workflow_input_sort_key(
            "db1", "a1", "/a1/f.glb", "v-resolved")
        assert row["versionPinned"] is False
        assert row["workflowExecutionId"] == skip_id
        # Every row of the record carries the same first-skip date.
        values = main.updates[0]["ExpressionAttributeValues"]
        names = main.updates[0]["ExpressionAttributeNames"]
        start = next(values[":" + k[1:]] for k, v in names.items() if v == "executionStartDate")
        assert row["executionStartDate"] == cfg["executionStartDate"] == start

    def test_a_later_refusal_keeps_the_first_skip_date(self):
        main, inputs = _MainTable(count=3), _InputsTable()
        config = _ConfigTable(existing={"workflowExecutionId": "x",
                                        "executionStartDate": "2026-11-06T09:00:00Z"})
        response, _sfn, _log, _stop = _launch_refused(main, inputs, config=config)
        assert response["statusCode"] == 400
        assert config.puts == []
        assert inputs.puts[0]["executionStartDate"] == "2026-11-06T09:00:00Z"
        names = main.updates[0]["ExpressionAttributeNames"]
        values = main.updates[0]["ExpressionAttributeValues"]
        start = next(values[":" + k[1:]] for k, v in names.items() if v == "executionStartDate")
        assert start == "2026-11-06T09:00:00Z"
        assert values[":lastSkipped"] != "2026-11-06T09:00:00Z"
        assert main.count == 4

    def test_a_manual_launch_refused_writes_nothing(self):
        main, inputs, config = _MainTable(), _InputsTable(), _ConfigTable()
        body = dict(ONE_FILE, triggerType="manual")
        response, _sfn, _log, _stop = _launch_refused(main, inputs, event=_cross_call_event(body),
                                                      tokens=("user1",), config=config)
        assert response["statusCode"] == 400
        assert main.updates == [] and inputs.puts == [] and config.puts == []

    def test_a_user_naming_the_file_upload_trigger_type_writes_nothing(self):
        # Only the dispatcher's SYSTEM_USER cross-call records skips.
        main, inputs, config = _MainTable(), _InputsTable(), _ConfigTable()
        response, _sfn, _log, _stop = _launch_refused(main, inputs, tokens=("user1",), config=config)
        assert response["statusCode"] == 400
        assert main.updates == [] and inputs.puts == [] and config.puts == []

    def test_a_failed_input_write_writes_no_main_row_and_keeps_the_400(self):
        main, inputs = _MainTable(), _InputsTable(fail=True)
        response, _sfn, _log, _stop = _launch_refused(main, inputs)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == el.LOCK_CONFLICT_MESSAGE_BY_SCOPE["asset"]
        assert main.updates == []

    def test_a_failed_main_write_still_answers_the_same_400(self):
        main, inputs = _MainTable(fail=True), _InputsTable()
        response, _sfn, _log, _stop = _launch_refused(main, inputs)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == el.LOCK_CONFLICT_MESSAGE_BY_SCOPE["asset"]

    def test_past_the_cap_a_refusal_is_only_counted(self):
        main, inputs = _MainTable(count=er.MAX_SKIP_RECORD_INPUT_ROWS), _InputsTable()
        response, _sfn, _log, _stop = _launch_refused(main, inputs)
        assert response["statusCode"] == 400
        assert len(main.updates) == 1 and inputs.puts == []

    def test_the_last_listed_refusal_still_writes_its_file(self):
        main, inputs = _MainTable(count=er.MAX_SKIP_RECORD_INPUT_ROWS - 1), _InputsTable()
        _launch_refused(main, inputs)
        assert len(inputs.puts) == 1

    def test_a_conflict_with_no_known_holder_writes_nothing(self):
        conflict = el.ExecutionLockConflict(ASSET_KEY, "")
        assert ewv2._record_skipped_launch({"databaseId": WF_DB, "workflowId": WF_ID}, conflict,
                                           [], {}) == ""


@pytest.mark.unit
class TestConcurrentRefusalsAgainstRealTables:
    """Two refusals (concurrent writers, or one SQS redelivery) against moto tables: one record, one
    configuration row, one input row, a count of exactly two, and one first-skip date on every row."""

    @mock_aws
    def test_two_refusals_make_one_record(self):
        resource = boto3.resource("dynamodb", region_name="us-east-1")

        def _table(name, keys):
            return resource.create_table(
                TableName=name, BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": keys[0], "KeyType": "HASH"},
                           {"AttributeName": keys[1], "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in keys])

        main = _table(ewv2.workflow_execution_database_v2,
                      ("workflowExecutionId", "workflowDatabaseId:workflowId"))
        inputs = _table(ewv2.workflow_execution_inputs_table,
                        ("workflowExecutionId", "databaseId:assetId:inputAssetFileKey"))
        config = _table(ewv2.workflow_execution_configuration_table,
                        ("workflowExecutionId", "recordType"))
        workflow = {"databaseId": WF_DB, "workflowId": WF_ID, "workflow_arn": "arn",
                    "specifiedPipelines": [{"pipelineId": "p1"}]}
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                     "resolvedVersionId": "v1"}]
        assets = {("db1", "a1"): {"databaseId": "db1", "assetId": "a1", "bucketId": "b",
                                  "assetLocation": {"Key": "a1/"}}}
        conflict = el.ExecutionLockConflict(ASSET_KEY, HOLDER)
        times = iter(["2026-11-06T10:00:00Z", "2026-11-06T10:05:00Z"])
        with patch.object(ewv2, "dynamodb", resource), \
                patch.object(ewv2.er, "iso_now", side_effect=lambda: next(times)), \
                patch(f"{EXEC_MOD}._asset_bucket_details", return_value={"bucketName": "bkt"}):
            for _ in range(2):
                assert ewv2._record_skipped_launch(
                    workflow, conflict, selected, assets,
                    pipeline_execution_parameters=TRIGGER_TEMPLATES)
        main_rows, input_rows, config_rows = (main.scan()["Items"], inputs.scan()["Items"],
                                              config.scan()["Items"])
        assert len(main_rows) == len(input_rows) == len(config_rows) == 1
        assert main_rows[0]["skippedLaunchCount"] == 2
        assert main_rows[0]["lastSkippedDate"] == "2026-11-06T10:05:00Z"
        first = "2026-11-06T10:00:00Z"
        assert (main_rows[0]["executionStartDate"], main_rows[0]["executionStopDate"],
                input_rows[0]["executionStartDate"], config_rows[0]["executionStartDate"]) == (
            first, first, first, first)
        assert config_rows[0]["pipelineExecutionParameters"] == TRIGGER_TEMPLATES


# --------------------------------------------------------------------------- #
# Read surfaces
# --------------------------------------------------------------------------- #

SKIP_ID = er.skip_record_execution_id(ASSET_KEY, HOLDER)
SKIP_ROW = {
    "workflowExecutionId": SKIP_ID, "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}",
    "workflowId": WF_ID, "workflowDatabaseId": WF_DB, "executionStatus": "SKIPPED",
    "executionStartDate": "2026-11-06T10:00:00Z", "executionStopDate": "2026-11-06T10:00:00Z",
    "lastSkippedDate": "2026-11-06T10:09:00Z", "triggerType": "File-Upload",
    "triggeredByUserId": "SYSTEM_USER", "skippedLaunchCount": 49, "skipScope": "asset",
    "skipReason": el.LOCK_CONFLICT_MESSAGE_BY_SCOPE["asset"], "blockingExecutionId": HOLDER,
    "workflow_execution_arn": "", "executionLog": "", "executionError": "",
}
OLD_ROW = {
    "workflowExecutionId": "a" * 32, "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}",
    "workflowId": WF_ID, "workflowDatabaseId": WF_DB, "executionStatus": "SUCCEEDED",
    "executionStartDate": "2026-11-01T10:00:00Z", "executionStopDate": "2026-11-01T10:05:00Z",
    "triggerType": "Manual", "triggeredByUserId": "user1", "executionLog": "", "executionError": "",
}
_INPUT = {"workflowExecutionId": SKIP_ID, "databaseId": "db1", "assetId": "a1",
          "inputAssetFileKey": "/a1/f.glb"}


def _list(rows_by_id, inputs):
    return es.build_execution_items(
        inputs, fetch_main_row=lambda i: dict(rows_by_id[i]),
        describe_execution=MagicMock(side_effect=AssertionError("no poll for a stopped row")),
        persist_main_row=MagicMock(), workflow_id_filter="", workflow_database_id="")


@pytest.mark.unit
class TestListsShowSkipRecords:
    def test_the_asset_list_carries_the_count_scope_and_reason_only(self):
        item = _list({SKIP_ID: SKIP_ROW}, [_INPUT])[0]
        assert item["executionStatus"] == "SKIPPED"
        assert item["skippedLaunchCount"] == 49
        assert item["skipScope"] == "asset"
        assert item["skipReason"] == SKIP_ROW["skipReason"]
        assert "blockingExecutionId" not in item and "lastSkippedDate" not in item

    def test_an_old_row_lists_exactly_as_before(self):
        old_input = dict(_INPUT, workflowExecutionId=OLD_ROW["workflowExecutionId"])
        item = _list({OLD_ROW["workflowExecutionId"]: OLD_ROW}, [old_input])[0]
        assert not set(item) & {"skippedLaunchCount", "skipScope", "skipReason", "skip"}

    def test_the_global_list_row(self):
        item = es._global_list_row(dict(SKIP_ROW))
        assert (item["skippedLaunchCount"], item["skipScope"]) == (49, "asset")
        assert "blockingExecutionId" not in item
        assert not set(es._global_list_row(dict(OLD_ROW))) & {"skippedLaunchCount", "skipScope",
                                                             "skipReason"}

    def test_the_status_filter_selects_and_ignores_skip_records(self):
        assert es._global_list_matches_filters(SKIP_ROW, {"status": "SKIPPED"})
        assert not es._global_list_matches_filters(OLD_ROW, {"status": "SKIPPED"})
        assert not es._global_list_matches_filters(SKIP_ROW, {"status": "RUNNING"})


def _details(main_row, blocking_allowed, blocking_row=True):
    files = [{"databaseId": "db1", "assetId": "a1", "inputAssetFileKey": f"/a1/f{i}.glb",
              "versionId": ""} for i in range(25)]

    def _main_row(execution_id):
        if execution_id == main_row["workflowExecutionId"]:
            return dict(main_row)
        return {"workflowExecutionId": execution_id} if blocking_row else None

    def _authorize(execution_id, _item, _action, config_row=None):
        if execution_id == main_row["workflowExecutionId"]:
            return True, ""
        return (True, "") if blocking_allowed else (False, "asset GET denied")

    with patch(f"{SVC}.get_execution_main_row", side_effect=_main_row), \
            patch(f"{SVC}.get_workflow_execution_configuration_row", return_value={}), \
            patch(f"{SVC}.authorize_execution_access", side_effect=_authorize), \
            patch(f"{SVC}.assemble_execution_details",
                  return_value={"executionStatus": main_row["executionStatus"], "inputFiles": files}):
        response = es.get_execution_details({}, main_row["workflowExecutionId"])
    assert response["statusCode"] == 200
    return json.loads(response["body"])["message"]


@pytest.mark.unit
class TestTheDetailsSkipBlock:
    def test_a_caller_who_may_read_the_blocking_run_sees_its_id(self):
        skip = _details(SKIP_ROW, blocking_allowed=True)["skip"]
        assert skip["blockingExecutionId"] == HOLDER
        assert skip["count"] == 49 and skip["scope"] == "asset"
        assert skip["reason"] == SKIP_ROW["skipReason"]
        assert skip["firstSkippedDate"] == "2026-11-06T10:00:00Z"
        assert skip["lastSkippedDate"] == "2026-11-06T10:09:00Z"
        assert len(skip["files"]) == er.MAX_SKIP_DETAIL_FILES
        assert skip["filesTruncated"] is True

    def test_a_denied_caller_and_a_gone_run_get_the_same_block(self):
        denied = _details(SKIP_ROW, blocking_allowed=False)["skip"]
        gone = _details(SKIP_ROW, blocking_allowed=True, blocking_row=False)["skip"]
        assert "blockingExecutionId" not in denied
        assert denied == gone

    def test_an_old_row_has_no_skip_block(self):
        assert "skip" not in _details(OLD_ROW, blocking_allowed=True)

    def test_a_count_past_the_cap_flags_the_files_truncated(self):
        block = es.skip_details_block(dict(SKIP_ROW, skippedLaunchCount=150, blockingExecutionId=""),
                                      [{"databaseId": "db1", "assetId": "a1",
                                        "inputAssetFileKey": "/a1/f.glb", "versionId": ""}])
        assert block["filesTruncated"] is True and len(block["files"]) == 1

    def test_a_small_record_is_not_truncated(self):
        block = es.skip_details_block(dict(SKIP_ROW, skippedLaunchCount=3, blockingExecutionId=""),
                                      [{"databaseId": "db1", "assetId": "a1",
                                        "inputAssetFileKey": "/a1/f.glb", "versionId": ""}])
        assert block["filesTruncated"] is False


@pytest.mark.unit
class TestTheLogsOfASkipRecord:
    def _logs(self, mode):
        with patch(f"{SVC}.get_execution_main_row", return_value=dict(SKIP_ROW)), \
                patch(f"{SVC}.authorize_execution_access", return_value=(True, "")), \
                patch(f"{SVC}._full_log_search", side_effect=AssertionError("no CloudWatch read")), \
                patch(f"{SVC}._sfn_execution_history_events",
                      side_effect=AssertionError("no history read")):
            response = es.get_execution_logs({}, SKIP_ID, {"mode": mode})
        assert response["statusCode"] == 200
        return json.loads(response["body"])["message"]

    def test_truncated_mode_says_there_are_no_logs(self):
        message = self._logs("truncated")
        assert message["executionLog"] == es.SKIPPED_EXECUTION_LOG_MESSAGE
        assert message["logsSource"] == "stored"

    def test_full_mode_says_there_are_no_logs(self):
        message = self._logs("full")
        assert message["events"] == [] and message["nextToken"] is None
        assert message["warnings"] == [es.SKIPPED_EXECUTION_LOG_MESSAGE]


@pytest.mark.unit
class TestActionsOnASkipRecord:
    def test_abort_of_a_skip_record_is_refused_and_writes_nothing(self):
        # SKIPPED has nothing to stop: the abort answers 400 after authorization (owner decision
        # 2026-11-06); test_execution_list_exclude_status.py covers the refused caller and the group.
        with patch(f"{SVC}.get_execution_main_row", return_value=dict(SKIP_ROW)), \
                patch(f"{SVC}.authorize_abort", return_value=(True, "")), \
                patch(f"{SVC}._persist_reconciled_main_row") as persist, \
                patch(f"{SVC}.sfn") as sfn, \
                patch(f"{SVC}.el.release_locks_for_execution") as release:
            response = es.abort_execution({}, SKIP_ID)
        assert response["statusCode"] == 400
        persist.assert_not_called()
        sfn.stop_execution.assert_not_called()
        release.assert_not_called()

    def test_rerun_replays_the_skipped_files_as_a_manual_launch(self):
        row = er.build_workflow_execution_input_record(
            workflow_execution_id=SKIP_ID, database_id="db1", asset_id="a1",
            input_asset_file_key="a1/f.glb", execution_start_date="2026-11-06T10:00:00Z",
            workflow_id=WF_ID, workflow_database_id=WF_DB, asset_root_s3_key="a1/",
            version_id="v1", version_pinned=False)
        with patch(f"{SVC}._query_all", return_value=[row]), \
                patch(f"{SVC}.get_pipeline_execution_rows", return_value=[]):
            body = es._reconstruct_execute_request(SKIP_ID, dict(SKIP_ROW), {})
        assert body["inputFiles"] == [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]
        assert body["triggerType"] == "manual"

    def test_rerun_of_a_skip_record_uses_the_triggers_template(self):
        # D2: the refused trigger named a non-default template; the re-run replays it.
        row = er.build_workflow_execution_input_record(
            workflow_execution_id=SKIP_ID, database_id="db1", asset_id="a1",
            input_asset_file_key="a1/f.glb", execution_start_date="2026-11-06T10:00:00Z",
            workflow_id=WF_ID, workflow_database_id=WF_DB, asset_root_s3_key="a1/")
        config_row = er.build_workflow_configuration_record(
            workflow_execution_id=SKIP_ID, input_metadata="", specified_pipelines_snapshot=[],
            output_asset_id="a1", output_database_id="db1",
            pipeline_execution_parameters={"p1": {"templateId": "sleep280"}})
        config_row.pop("outputFileBaseExecutionPathExtension")
        with patch(f"{SVC}._query_all", return_value=[row]), \
                patch(f"{SVC}.get_pipeline_execution_rows", return_value=[]):
            body = es._reconstruct_execute_request(SKIP_ID, dict(SKIP_ROW), config_row)
        assert body["pipelineExecutionParameters"] == {"p1": {"templateId": "sleep280"}}
        assert (body["outputDatabaseId"], body["outputAssetId"]) == ("db1", "a1")
        assert "outputFileBaseExecutionPathExtension" not in body

    def test_a_launched_runs_rerun_ignores_a_stored_parameter_map(self):
        with patch(f"{SVC}._query_all", return_value=[]), \
                patch(f"{SVC}.get_pipeline_execution_rows", return_value=[]):
            body = es._reconstruct_execute_request(
                "a" * 32, dict(OLD_ROW), {"pipelineExecutionParameters": {"p1": {"templateId": "x"}}})
        assert body["pipelineExecutionParameters"] == {}

    def test_permanent_delete_removes_the_main_and_input_rows(self):
        deleted = []
        table = MagicMock()
        table.delete_item.side_effect = lambda **kw: deleted.append(kw["Key"])
        with patch(f"{SVC}.get_execution_main_row", return_value=dict(SKIP_ROW)), \
                patch(f"{SVC}.get_workflow_execution_configuration_row", return_value={}), \
                patch(f"{SVC}.authorize_abort", return_value=(True, "")), \
                patch(f"{SVC}.get_pipeline_execution_rows", return_value=[]), \
                patch(f"{SVC}._delete_all_rows") as delete_rows, \
                patch.object(es.dynamodb, "Table", return_value=table), \
                patch(f"{SVC}.sfn") as sfn, \
                patch(f"{SVC}.log_actions"):
            response = es.permanent_delete_execution({}, SKIP_ID)
        assert response["statusCode"] == 200
        sfn.describe_execution.assert_not_called()
        assert delete_rows.call_args.args[0] == es.workflow_execution_inputs_table
        assert {"workflowExecutionId": SKIP_ID,
                "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}"} in deleted
