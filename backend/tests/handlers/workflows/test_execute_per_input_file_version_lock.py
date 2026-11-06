# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""perInputFileVersion at launch: the selected file versions are locked after every validation and
render and before the state machine starts; a held version answers the fixed generic 400 and starts
nothing; every launch failure after the acquire releases the keys from memory, because the input rows
the terminal handlers derive keys from may not exist yet on those paths.

The lock table is a fake that records the conditional writes the real executionLocks module issues and
can be told which keys are held (answering as DynamoDB does, with the holder returned); everything else
follows the stubbing shape of test_executeWorkflow.py::TestExecuteOrchestration."""

import contextlib
import json
import os
import sys
import time
import types

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

# executeWorkflow resolves these at import (mirrors test_executeWorkflow.py).
for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
    ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates"),
    ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema"),
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
    ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
    ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME", "t-wf-locks"),
]:
    os.environ.setdefault(_name, _value)

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ewv2  # noqa: E402
from backend.tests.workflowDeploymentStub import with_current_deployment  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"
# Taken from the handler's own module attributes: the test tree puts both `backend/` and
# `backend/backend/` on sys.path, so a re-imported module is a DIFFERENT object.
el = ewv2.el
er = ewv2.er

WF_DB, WF_ID = "db1", "wf1"


def _event(body):
    return {
        "requestContext": {"http": {"method": "POST", "path": f"/workflows/{WF_DB}/{WF_ID}/execute"},
                           "authorizer": {}},
        "pathParameters": {"workflowDatabaseId": WF_DB, "workflowId": WF_ID},
        "queryStringParameters": {},
        "headers": {"authorization": "Bearer t"},
        "body": json.dumps(body),
    }


def _workflow(restriction):
    return {
        "databaseId": WF_DB, "workflowId": WF_ID, "workflowName": "WF", "enabled": True,
        "archived": False, "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1",
        "jobNames": ["job-p1"],
        "specifiedPipelines": [{"pipelineDatabaseId": "db1", "pipelineId": "p1", "jobName": "p1"}],
        "systemConfig": {
            "inputFileArity": "multi",
            "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                           "wholeAssetAllowed": True, "folderAllowed": True},
            "metadataInputs": {"assetMetadata": False, "fileMetadata": False, "fileAttributes": False,
                               "databaseMetadata": False},
            "inputFileFilters": {"allow": [], "exclude": []},
            "concurrencyRestriction": restriction,
            "outputTarget": {"locationType": "asset", "allowOverride": False},
        },
    }


# taskTimeout above the one-day floor, so the expiry proves the max() path rather than the floor.
_PIPELINE = {
    "databaseId": "db1", "pipelineId": "p1", "pipelineName": "P1", "enabled": True, "archived": False,
    "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"},
                        "waitForCallback": "Disabled", "taskTimeout": "100000"},
    "systemConfig": {"inputFileArity": "multi", "requireTemplate": False,
                     "allowCustomTemplateOverride": False,
                     "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                                    "wholeAssetAllowed": True, "folderAllowed": True},
                     "inputFileFilters": {"allow": [], "exclude": []}},
}
_ASSET = {"databaseId": "db1", "assetId": "a1", "assetName": "A1", "bucketId": "bkt-1",
          "assetLocation": {"Key": "a1/"}}
_TWO_FILES = {"inputFiles": [
    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/g.glb"},
]}
# What the launch must write, built the way the WorkflowExecutionInputs rows store the key: asset root
# "a1/" + relative "/f.glb" -> full key "a1/f.glb" -> normalized "/a1/f.glb"; version = resolvedVersionId.
KEY_F = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "v-resolved")
KEY_G = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/g.glb", "v-resolved")


class _LockTable:
    """Records the launch's conditional writes on the lock table. `held` maps a key to the execution id
    DynamoDB would return for a live row (ReturnValuesOnConditionCheckFailure=ALL_OLD), and
    `acquired_at` to that row's acquiredAt; a takeover put naming that same holder and acquiredAt
    succeeds."""

    def __init__(self, held=None, acquired_at=None):
        self.held = dict(held or {})
        self.acquired_at = dict(acquired_at or {})
        self.puts, self.deletes, self.journal = [], [], []

    def put_item(self, **kwargs):
        self.puts.append(kwargs)
        key = kwargs["Item"]["lockKey"]
        self.journal.append(("lock", key))
        holder = self.held.get(key)
        values = kwargs.get("ExpressionAttributeValues", {})
        takeover = (holder is not None and values.get(":holder") == holder
                    and values.get(":acquiredAt") == self.acquired_at.get(key))
        if holder is not None and not takeover:
            item = {"workflowExecutionId": {"S": holder}}
            if key in self.acquired_at:
                item["acquiredAt"] = {"S": self.acquired_at[key]}
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "held"},
                               "Item": item}, "PutItem")
        return {}

    def delete_item(self, **kwargs):
        self.deletes.append(kwargs)
        self.journal.append(("release", kwargs["Key"]["lockKey"]))
        return {}


def _allow_enforcer():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


_THROTTLED = ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                         "StopExecution")


def _run(lock_table, restriction="perInputFileVersion", body=_TWO_FILES, persist_error=None,
         write_error=None, start_error=None, tokens=("user1",), event=None,
         resolved_version="v-resolved", real_stop=False, stop_error=None, describe=None,
         main_table=None, inputs_table=None, config_table=None):
    """Drive lambda_handler through a launch; returns (response, sfn mock, logger mock, stop spy).
    `tokens` is the identity request_to_claims answers with and `event` replaces the default REST-shaped
    event (a lambdaCrossCall event, say), so the trigger-dispatched path can be driven through the same
    harness. With `real_stop` the handler's own stop helper runs against the Step Functions mock (its
    StopExecution raising `stop_error`, its DescribeExecution answering or raising `describe`) and the
    stop spy is None. `main_table` stands in for the main execution table when given, and `inputs_table`
    for the workflow-input table, and `config_table` for the workflow configuration table."""
    def _table(name):
        if name == ewv2.workflow_execution_locks_table:
            return lock_table
        if main_table is not None and name == ewv2.workflow_execution_database_v2:
            return main_table
        if inputs_table is not None and name == ewv2.workflow_execution_inputs_table:
            return inputs_table
        if config_table is not None and name == ewv2.workflow_execution_configuration_table:
            return config_table
        return MagicMock()

    def _start(**kw):
        lock_table.journal.append(("start", kw["name"]))
        if start_error is not None:
            raise start_error
        return {"executionArn": "arn:exec"}

    with contextlib.ExitStack() as stack:
        for context in (
            patch(f"{MOD}._get_workflow",
                  return_value=with_current_deployment(_workflow(restriction), [_PIPELINE])),
            patch(f"{MOD}._get_pipeline", return_value=dict(_PIPELINE)),
            patch(f"{MOD}._get_asset", return_value=dict(_ASSET)),
            patch(f"{MOD}._default_run_bucket",
                  return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}),
            patch(f"{MOD}._asset_bucket_details",
                  return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}),
            patch(f"{MOD}._input_exists_in_s3", return_value=(True, resolved_version)),
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_enforcer()),
            patch(f"{MOD}.request_to_claims", return_value={"tokens": list(tokens)}),
            patch(f"{MOD}.s3c"),
            patch.object(ewv2.dynamodb, "Table", side_effect=_table),
        ):
            stack.enter_context(context)
        sfn = stack.enter_context(patch(f"{MOD}.sfn_client"))
        sfn.start_execution.side_effect = _start
        if stop_error is not None:
            sfn.stop_execution.side_effect = stop_error
        if isinstance(describe, Exception):
            sfn.describe_execution.side_effect = describe
        elif describe is not None:
            sfn.describe_execution.return_value = describe
        log = stack.enter_context(patch(f"{MOD}.logger"))
        stop = None if real_stop else stack.enter_context(
            patch(f"{MOD}._stop_started_execution", return_value=True))
        if persist_error is not None:
            stack.enter_context(patch(f"{MOD}._persist_execution_records", side_effect=persist_error))
        if write_error is not None:
            stack.enter_context(patch(f"{MOD}._write_execution_input_files", side_effect=write_error))
        response = ewv2.lambda_handler(event if event is not None else _event(body), MagicMock())
    return response, sfn, log, stop


def _warnings(log_mock):
    return " ".join(str(c.args[0]) for c in log_mock.warning.call_args_list)


@pytest.mark.unit
class TestLockKeysForLaunch:
    def test_keys_use_the_stored_input_key_shape_and_the_resolved_version(self):
        selected = [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "resolvedVersionId": "v1"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/", "resolvedVersionId": ""},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "resolvedVersionId": "v1"},
        ]
        keys = ewv2._lock_keys_for_launch(_workflow("perInputFileVersion"), selected,
                                          {("db1", "a1"): dict(_ASSET)})
        # Parity with the row the terminal handlers read: same normalizer, same full key.
        stored = er.build_workflow_execution_input_record(
            workflow_execution_id="e", database_id="db1", asset_id="a1",
            input_asset_file_key="a1/f.glb", execution_start_date="", workflow_id=WF_ID,
            workflow_database_id=WF_DB, version_id="v1")["inputAssetFileKey"]
        assert keys == [
            el.build_lock_key(WF_DB, WF_ID, "db1", "a1", stored, "v1"),
            el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/", ""),
        ]

    def test_the_none_restriction_yields_no_keys(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                     "resolvedVersionId": "v1"}]
        assert ewv2._lock_keys_for_launch(_workflow("none"), selected,
                                          {("db1", "a1"): dict(_ASSET)}) == []

    def test_per_asset_and_per_input_file_yield_their_own_scoped_keys(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                     "resolvedVersionId": "v1"},
                    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/g.glb",
                     "resolvedVersionId": "v2"}]
        assets = {("db1", "a1"): dict(_ASSET)}
        assert ewv2._lock_keys_for_launch(_workflow("perAsset"), selected, assets) == [
            f"{WF_DB}:{WF_ID}|asset|db1:a1"]
        assert ewv2._lock_keys_for_launch(_workflow("perInputFile"), selected, assets) == [
            f"{WF_DB}:{WF_ID}|assetFile|db1:a1:/a1/f.glb",
            f"{WF_DB}:{WF_ID}|assetFile|db1:a1:/a1/g.glb"]


@pytest.mark.unit
class TestLaunchAcquiresBeforeStarting:
    def test_one_lock_per_selected_file_version_is_taken_before_the_machine_starts(self):
        table = _LockTable()
        response, sfn, _log, _stop = _run(table)
        assert response["statusCode"] == 200
        execution_id = json.loads(response["body"])["message"]["executionId"]
        assert [p["Item"]["lockKey"] for p in table.puts] == [KEY_F, KEY_G]
        assert table.puts, "no lock row was written"
        for put in table.puts:
            assert put["Item"]["workflowExecutionId"] == execution_id
            assert "attribute_not_exists(lockKey)" in put["ConditionExpression"]
            assert "expiresAt" in put["ConditionExpression"]
            # taskTimeout 100000 exceeds the one-day floor, so the row lives 100000 + 1800 seconds.
            assert put["Item"]["expiresAt"] - put["ExpressionAttributeValues"][":now"] == 100000 + 1800
            assert put["ReturnValuesOnConditionCheckFailure"] == "ALL_OLD"
        assert [kind for kind, _ in table.journal] == ["lock", "lock", "start"]
        sfn.start_execution.assert_called_once()
        assert table.deletes == [], "a successful launch keeps its locks for the terminal release"

    def test_the_none_restriction_touches_no_lock_row(self):
        table = _LockTable()
        response, _sfn, _log, _stop = _run(table, restriction="none")
        assert response["statusCode"] == 200
        assert table.puts == [] and table.deletes == []


@pytest.mark.unit
class TestConflictAnswers400:
    def test_a_held_version_answers_the_fixed_body_and_starts_nothing(self):
        table = _LockTable(held={KEY_G: "e-holder"})
        response, sfn, _log, _stop = _run(table)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[el.LOCK_SCOPE_ASSET_FILE_VERSION]
        sfn.start_execution.assert_not_called()
        # The first key was taken and rolled back before the 400 (multi-file rollback).
        assert [d["Key"]["lockKey"] for d in table.deletes] == [KEY_F]
        assert "workflowExecutionId" in table.deletes[0]["ConditionExpression"]

    def test_the_body_carries_no_identifiers_and_the_log_carries_the_key_and_holder(self):
        table = _LockTable(held={KEY_G: "e-holder"})
        response, _sfn, log, _stop = _run(table)
        body = response["body"]
        assert json.loads(body) == {"message": el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[el.LOCK_SCOPE_ASSET_FILE_VERSION]}
        for identifier in ("db1", "a1", "f.glb", "g.glb", "v-resolved", "wf1", "e-holder", "|"):
            assert identifier not in body
        logged = _warnings(log)
        assert KEY_G in logged
        assert "e-holder" in logged

    def test_the_cli_mapping_substrings_survive(self):
        # tools/VamsCLI/vamscli/utils/api_client.py maps this response to WorkflowAlreadyRunningError
        # by substring; the body is the contract that mapping reads.
        assert "already running" in el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[el.LOCK_SCOPE_ASSET_FILE_VERSION]
        assert "conflicting execution" in el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[el.LOCK_SCOPE_ASSET_FILE_VERSION]


@pytest.mark.unit
class TestLaunchFailuresReleaseFromMemory:
    def test_a_failure_before_the_input_rows_exist_releases_both_keys_and_stops_the_machine(self):
        # _persist_execution_records writes the input rows; failing it is exactly the case in which
        # no terminal handler can rebuild the keys, so the release must come from the launch itself.
        table = _LockTable()
        response, sfn, _log, stop = _run(table, persist_error=RuntimeError("row write failed"))
        assert response["statusCode"] == 500
        sfn.start_execution.assert_called_once()
        stop.assert_called_once_with("arn:exec")
        assert {d["Key"]["lockKey"] for d in table.deletes} == {KEY_F, KEY_G}
        assert [kind for kind, _ in table.journal] == ["lock", "lock", "start", "release", "release"]

    def test_a_failed_input_file_write_releases_and_never_starts(self):
        table = _LockTable()
        response, sfn, _log, stop = _run(table, write_error=RuntimeError("s3 put failed"))
        assert response["statusCode"] == 500
        sfn.start_execution.assert_not_called()
        stop.assert_not_called()
        assert {d["Key"]["lockKey"] for d in table.deletes} == {KEY_F, KEY_G}

    def test_a_failed_start_releases_without_a_stop(self):
        table = _LockTable()
        response, _sfn, _log, stop = _run(table, start_error=RuntimeError("states unavailable"))
        assert response["statusCode"] == 500
        stop.assert_not_called()
        assert {d["Key"]["lockKey"] for d in table.deletes} == {KEY_F, KEY_G}


@pytest.mark.unit
class TestAFailedRecordWriteReleasesOnlyAStoppedMachine:
    """After the state machine started and its records could not be written, the launch releases its
    locks only when the machine runs no further: the stop succeeded, or a describe after a failed stop
    shows it ended. A machine that may still be running keeps them for its terminal steps or the expiry."""

    def _launch(self, **stop):
        table = _LockTable()
        response, sfn, _log, _stop = _run(
            table, persist_error=RuntimeError("row write failed"), real_stop=True, **stop)
        assert response["statusCode"] == 500
        sfn.stop_execution.assert_called_once()
        return table, sfn

    def test_a_throttled_stop_of_a_still_running_machine_keeps_the_locks(self):
        table, sfn = self._launch(stop_error=_THROTTLED, describe={"status": "RUNNING"})
        sfn.describe_execution.assert_called_once_with(executionArn="arn:exec")
        assert table.deletes == []

    def test_a_stop_that_succeeds_releases_the_locks(self):
        table, sfn = self._launch()
        sfn.describe_execution.assert_not_called()
        assert {d["Key"]["lockKey"] for d in table.deletes} == {KEY_F, KEY_G}

    @pytest.mark.parametrize("status", sorted(el.SFN_ENDED_STATUSES))
    def test_a_failed_stop_of_a_machine_that_already_ended_releases_the_locks(self, status):
        table, _sfn = self._launch(stop_error=_THROTTLED, describe={"status": status})
        assert {d["Key"]["lockKey"] for d in table.deletes} == {KEY_F, KEY_G}

    def test_a_failed_stop_whose_describe_also_fails_keeps_the_locks(self):
        table, _sfn = self._launch(stop_error=_THROTTLED, describe=ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "DescribeExecution"))
        assert table.deletes == []

    def test_a_machine_pending_redrive_keeps_the_locks(self):
        table, _sfn = self._launch(stop_error=_THROTTLED, describe={"status": "PENDING_REDRIVE"})
        assert table.deletes == []


def _iso_seconds_ago(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


@pytest.mark.unit
class TestALaunchTakesOverAStrandedLock:
    """A row whose holder died between taking its locks and writing its execution record is taken over
    once it is older than this handler's own timeout (EXECUTE_WORKFLOW_TIMEOUT_SECONDS) plus the
    margin; the holder's record is read from the main execution table by its full key."""

    def _launch(self, monkeypatch, age_seconds, timeout="900", main_row=None):
        if timeout is None:
            monkeypatch.delenv(ewv2.EXECUTE_WORKFLOW_TIMEOUT_ENV, raising=False)
        else:
            monkeypatch.setenv(ewv2.EXECUTE_WORKFLOW_TIMEOUT_ENV, timeout)
        table = _LockTable(held={KEY_F: "e-dead"}, acquired_at={KEY_F: _iso_seconds_ago(age_seconds)})
        main_table = MagicMock()
        main_table.get_item.return_value = {"Item": main_row} if main_row else {}
        response, sfn, _log, _stop = _run(table, main_table=main_table)
        return response, sfn, table, main_table

    def test_a_row_older_than_the_timeout_whose_holder_has_no_record_is_taken_over(self, monkeypatch):
        response, sfn, table, main_table = self._launch(monkeypatch, 900 + 300 + 60)
        assert response["statusCode"] == 200
        sfn.start_execution.assert_called_once()
        main_table.get_item.assert_called_once()
        call = main_table.get_item.call_args.kwargs
        assert call["Key"] == {"workflowExecutionId": "e-dead", "workflowDatabaseId:workflowId": "db1:wf1"}
        assert call["ConsistentRead"] is True
        takeover = [p for p in table.puts if ":holder" in p["ExpressionAttributeValues"]]
        assert [p["Item"]["lockKey"] for p in takeover] == [KEY_F]
        assert takeover[0]["ExpressionAttributeValues"][":holder"] == "e-dead"

    def test_the_bound_is_read_from_the_handlers_own_timeout(self, monkeypatch):
        response, sfn, _table, main_table = self._launch(monkeypatch, 900 + 300 + 60, timeout="3600")
        assert response["statusCode"] == 400
        sfn.start_execution.assert_not_called()
        main_table.get_item.assert_not_called()

    def test_without_the_variable_the_bound_is_lambdas_maximum_timeout(self, monkeypatch):
        response, _sfn, _table, _main = self._launch(monkeypatch, 900 + 300 + 60, timeout=None)
        assert response["statusCode"] == 200
        response, _sfn, _table, _main = self._launch(monkeypatch, 900 + 300 - 60, timeout=None)
        assert response["statusCode"] == 400

    def test_a_young_row_is_refused(self, monkeypatch):
        response, sfn, _table, main_table = self._launch(monkeypatch, 60)
        assert response["statusCode"] == 400
        sfn.start_execution.assert_not_called()
        main_table.get_item.assert_not_called()

    def test_an_old_row_whose_holder_record_exists_is_refused(self, monkeypatch):
        response, sfn, _table, _main = self._launch(
            monkeypatch, 86400, main_row={"workflowExecutionId": "e-dead"})
        assert response["statusCode"] == 400
        sfn.start_execution.assert_not_called()

    def test_a_failed_record_read_refuses_the_launch(self, monkeypatch):
        monkeypatch.setenv(ewv2.EXECUTE_WORKFLOW_TIMEOUT_ENV, "900")
        table = _LockTable(held={KEY_F: "e-dead"}, acquired_at={KEY_F: _iso_seconds_ago(86400)})
        main_table = MagicMock()
        main_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "GetItem")
        response, sfn, _log, _stop = _run(table, main_table=main_table)
        assert response["statusCode"] == 400
        sfn.start_execution.assert_not_called()


_ONE_FILE = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
_WHOLE_ASSET = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/"}]}


@pytest.mark.unit
class TestAnEmptyVersionLocksLikePerInputFile:
    """An unversioned bucket, and a folder or whole-asset selection, resolve no version id, so the
    perInputFileVersion key ends in an empty version: every launch on that file or folder then shares one
    key, which is perInputFile's behaviour."""

    def test_an_unversioned_file_takes_the_empty_version_key(self):
        table = _LockTable()
        response, _sfn, _log, _stop = _run(table, body=_ONE_FILE, resolved_version="")
        assert response["statusCode"] == 200
        assert [p["Item"]["lockKey"] for p in table.puts] == [
            el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "")]
        assert table.puts[0]["Item"]["lockKey"].endswith("/a1/f.glb#")

    def test_a_second_launch_on_the_same_unversioned_file_is_refused(self):
        held = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "")
        table = _LockTable(held={held: "e-first"})
        response, sfn, _log, _stop = _run(table, body=_ONE_FILE, resolved_version="")
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == \
            el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[el.LOCK_SCOPE_ASSET_FILE_VERSION]
        sfn.start_execution.assert_not_called()

    def test_a_whole_asset_selection_locks_the_asset_root_with_an_empty_version(self):
        table = _LockTable()
        response, _sfn, _log, _stop = _run(table, body=_WHOLE_ASSET, resolved_version="")
        assert response["statusCode"] == 200
        assert [p["Item"]["lockKey"] for p in table.puts] == [
            el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/", "")]

    def test_a_versioned_launch_does_not_collide_with_the_unversioned_key(self):
        # A held empty-version row (a run from before the bucket's versioning was turned on) does not
        # block a launch that resolves a real version id: they are different keys.
        held = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "")
        table = _LockTable(held={held: "e-first"})
        response, _sfn, _log, _stop = _run(table, body=_ONE_FILE, resolved_version="v-new")
        assert response["statusCode"] == 200


@pytest.mark.unit
class TestTheKeyHoldsTheFullBucketPath:
    """The key carries the file's full key within its asset bucket (asset root plus asset-relative path),
    the same value the input rows store. A file moved or renamed while its run is going is a different
    key, so a run on the new path is not blocked by the run on the old one."""

    def _keys(self, restriction, items, assets):
        return ewv2._lock_keys_for_launch(_workflow(restriction), items, assets)

    def test_the_same_relative_path_in_two_assets_is_two_keys(self):
        items = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "resolvedVersionId": "v"},
                 {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/f.glb", "resolvedVersionId": "v"}]
        assets = {("db1", "a1"): dict(_ASSET),
                  ("db1", "a2"): dict(_ASSET, assetId="a2", assetLocation={"Key": "nested/a2/"})}
        for restriction in ("perInputFile", "perInputFileVersion"):
            keys = self._keys(restriction, items, assets)
            assert len(keys) == 2, restriction
            assert any(":/a1/f.glb" in k for k in keys) and any(":/nested/a2/f.glb" in k for k in keys)

    def test_a_renamed_file_is_a_different_key(self):
        assets = {("db1", "a1"): dict(_ASSET)}
        before = self._keys("perInputFile", [{"databaseId": "db1", "assetId": "a1",
                                              "relativeFileKey": "/old/f.glb"}], assets)
        after = self._keys("perInputFile", [{"databaseId": "db1", "assetId": "a1",
                                             "relativeFileKey": "/new/f.glb"}], assets)
        assert before != after
        assert before == [f"{WF_DB}:{WF_ID}|assetFile|db1:a1:/a1/old/f.glb"]

    def test_a_relative_key_that_already_carries_the_root_is_not_doubled(self):
        assets = {("db1", "a1"): dict(_ASSET)}
        assert self._keys("perInputFile", [{"databaseId": "db1", "assetId": "a1",
                                            "relativeFileKey": "a1/f.glb"}], assets) == \
            self._keys("perInputFile", [{"databaseId": "db1", "assetId": "a1",
                                         "relativeFileKey": "/f.glb"}], assets)
