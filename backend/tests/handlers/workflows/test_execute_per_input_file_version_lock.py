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
    DynamoDB would return for a live row (ReturnValuesOnConditionCheckFailure=ALL_OLD)."""

    def __init__(self, held=None):
        self.held = dict(held or {})
        self.puts, self.deletes, self.journal = [], [], []

    def put_item(self, **kwargs):
        self.puts.append(kwargs)
        self.journal.append(("lock", kwargs["Item"]["lockKey"]))
        holder = self.held.get(kwargs["Item"]["lockKey"])
        if holder is not None:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "held"},
                               "Item": {"workflowExecutionId": {"S": holder}}}, "PutItem")
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


def _run(lock_table, restriction="perInputFileVersion", body=_TWO_FILES, persist_error=None,
         write_error=None, start_error=None):
    """Drive lambda_handler through a launch; returns (response, sfn mock, logger mock, stop spy)."""
    def _table(name):
        return lock_table if name == ewv2.workflow_execution_locks_table else MagicMock()

    def _start(**kw):
        lock_table.journal.append(("start", kw["name"]))
        if start_error is not None:
            raise start_error
        return {"executionArn": "arn:exec"}

    with contextlib.ExitStack() as stack:
        for context in (
            patch(f"{MOD}._get_workflow", return_value=_workflow(restriction)),
            patch(f"{MOD}._get_pipeline", return_value=dict(_PIPELINE)),
            patch(f"{MOD}._get_asset", return_value=dict(_ASSET)),
            patch(f"{MOD}._default_run_bucket",
                  return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}),
            patch(f"{MOD}._asset_bucket_details",
                  return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}),
            patch(f"{MOD}._input_exists_in_s3", return_value=(True, "v-resolved")),
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_enforcer()),
            patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}),
            patch(f"{MOD}._running_execution_exists", return_value=False),
            patch(f"{MOD}.s3c"),
            patch.object(ewv2.dynamodb, "Table", side_effect=_table),
        ):
            stack.enter_context(context)
        sfn = stack.enter_context(patch(f"{MOD}.sfn_client"))
        sfn.start_execution.side_effect = _start
        log = stack.enter_context(patch(f"{MOD}.logger"))
        stop = stack.enter_context(patch(f"{MOD}._stop_started_execution"))
        if persist_error is not None:
            stack.enter_context(patch(f"{MOD}._persist_execution_records", side_effect=persist_error))
        if write_error is not None:
            stack.enter_context(patch(f"{MOD}._write_execution_input_files", side_effect=write_error))
        response = ewv2.lambda_handler(_event(body), MagicMock())
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

    def test_other_restrictions_yield_no_keys(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                     "resolvedVersionId": "v1"}]
        for restriction in ("none", "perAsset", "perInputFile"):
            assert ewv2._lock_keys_for_launch(_workflow(restriction), selected,
                                              {("db1", "a1"): dict(_ASSET)}) == []


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

    def test_other_restrictions_touch_no_lock_row(self):
        for restriction in ("none", "perAsset", "perInputFile"):
            table = _LockTable()
            response, _sfn, _log, _stop = _run(table, restriction=restriction)
            assert response["statusCode"] == 200
            assert table.puts == [] and table.deletes == []

    def test_the_legacy_guard_reads_nothing_under_the_lock_restriction(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]
        with patch.object(ewv2.dynamodb, "Table") as table_factory:
            assert ewv2._running_execution_exists(
                WF_DB, WF_ID, selected, {("db1", "a1"): dict(_ASSET)}, "perInputFileVersion") is False
        table_factory.assert_not_called()


@pytest.mark.unit
class TestConflictAnswers400:
    def test_a_held_version_answers_the_fixed_body_and_starts_nothing(self):
        table = _LockTable(held={KEY_G: "e-holder"})
        response, sfn, _log, _stop = _run(table)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == el.LOCK_CONFLICT_MESSAGE
        sfn.start_execution.assert_not_called()
        # The first key was taken and rolled back before the 400 (multi-file rollback).
        assert [d["Key"]["lockKey"] for d in table.deletes] == [KEY_F]
        assert "workflowExecutionId" in table.deletes[0]["ConditionExpression"]

    def test_the_body_carries_no_identifiers_and_the_log_carries_the_key_and_holder(self):
        table = _LockTable(held={KEY_G: "e-holder"})
        response, _sfn, log, _stop = _run(table)
        body = response["body"]
        assert json.loads(body) == {"message": el.LOCK_CONFLICT_MESSAGE}
        for identifier in ("db1", "a1", "f.glb", "g.glb", "v-resolved", "wf1", "e-holder", "|"):
            assert identifier not in body
        logged = _warnings(log)
        assert KEY_G in logged
        assert "e-holder" in logged

    def test_the_cli_mapping_substrings_survive(self):
        # tools/VamsCLI/vamscli/utils/api_client.py maps this response to WorkflowAlreadyRunningError
        # by substring; the body is the contract that mapping reads.
        assert "already running" in el.LOCK_CONFLICT_MESSAGE
        assert "conflicting execution" in el.LOCK_CONFLICT_MESSAGE


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
