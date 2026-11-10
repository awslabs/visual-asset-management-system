# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every DynamoDB call the workflow execute handler makes, per table, is one its role is granted.

The execute role carries one statement per group of tables that share an action list, naming each table
once with only the actions below (infra/lib/lambdaBuilder/workflowFunctions.ts, executeWorkflowDynamoStatements),
and infra/test/security/executionServiceRouteSplitGrants.test.ts pins those statements to the same list.
A new DynamoDB call on the launch path therefore fails here first, naming the grant to add.

The launch runs through lambda_handler with every table replaced by a recorder that answers as the
table would; nothing that reads a table is patched out, so a call a shared helper makes is recorded too.
The template reads, which a launch makes only for a templated pipeline, are recorded by calling their
helpers directly."""

import contextlib
import json

import pytest
from unittest.mock import MagicMock, patch

from tests.handlers.workflows.test_execute_per_input_file_version_lock import (  # noqa: F401
    MOD, WF_DB, WF_ID, _ASSET, _PIPELINE, _TWO_FILES, _allow_enforcer, _event, _workflow, ewv2,
)
from backend.tests.workflowDeploymentStub import with_current_deployment

#: What the execute role grants on each table: the reads of the launch's inputs (workflow, pipeline,
#: template and tag schema definitions, assets and their buckets), the execution records it writes, and
#: the main row's UpdateItem a launch refused by a concurrency restriction writes its skip record with.
#: The lock table's calls are granted by the role's own lock policy (EXECUTION_LOCK_ACQUIRE_ACTIONS).
EXECUTE_WORKFLOW_GRANTS = {
    "workflow": {"GetItem"},
    "pipeline": {"GetItem"},
    "asset": {"Query"},
    "buckets": {"Query", "Scan"},
    "templates": {"GetItem", "Query"},
    "tagSchema/index/TagSchemaByTemplateGSI": {"Query"},
    "main": {"GetItem", "PutItem", "UpdateItem"},
    "inputs": {"PutItem"},
    "configuration": {"PutItem"},
    "pipelineExecutions": {"PutItem"},
    "pipelineInputConfiguration": {"PutItem"},
    "pipelineInputMetadata": {"BatchWriteItem"},
    "locks": {"PutItem", "DeleteItem"},
}


def _logical_names():
    return {
        ewv2.workflow_table_name: "workflow",
        ewv2.pipeline_table_name: "pipeline",
        ewv2.asset_table_name: "asset",
        ewv2.buckets_table_name: "buckets",
        ewv2.templates_table_name: "templates",
        ewv2.tag_schema_table_name: "tagSchema",
        ewv2.workflow_execution_database_v2: "main",
        ewv2.workflow_execution_inputs_table: "inputs",
        ewv2.workflow_execution_configuration_table: "configuration",
        ewv2.pipeline_executions_table: "pipelineExecutions",
        ewv2.pipeline_execution_input_configuration_table: "pipelineInputConfiguration",
        ewv2.pipeline_execution_input_metadata_table: "pipelineInputMetadata",
        ewv2.workflow_execution_locks_table: "locks",
    }


class _BatchWriter:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put_item(self, **_kwargs):
        self.table.record("BatchWriteItem")

    def delete_item(self, **_kwargs):
        self.table.record("BatchWriteItem")


class _RecordingTable:
    """A table resource that records each call as (logical table, IAM action) and answers it with the
    rows `rows` holds for this table."""

    def __init__(self, name, calls, rows):
        self.name, self.calls, self.rows = name, calls, rows

    def record(self, action, index=None):
        self.calls.add((f"{self.name}/index/{index}" if index else self.name, action))

    def get_item(self, **_kwargs):
        self.record("GetItem")
        item = self.rows.get("item")
        return {"Item": item} if item else {}

    def query(self, **kwargs):
        self.record("Query", kwargs.get("IndexName"))
        return {"Items": list(self.rows.get("items", []))}

    def scan(self, **kwargs):
        self.record("Scan", kwargs.get("IndexName"))
        return {"Items": list(self.rows.get("scan", []))}

    def put_item(self, **_kwargs):
        self.record("PutItem")
        return {}

    def update_item(self, **_kwargs):
        self.record("UpdateItem")
        return {}

    def delete_item(self, **_kwargs):
        self.record("DeleteItem")
        return {}

    def batch_writer(self, **_kwargs):
        return _BatchWriter(self)


def _rows(restriction):
    bucket = {"bucketId": "bkt-1", "bucketName": "asset-bucket", "baseAssetsPrefix": "", "isDefault": True}
    return {
        "workflow": {"item": with_current_deployment(_workflow(restriction), [_PIPELINE])},
        "pipeline": {"item": dict(_PIPELINE)},
        "asset": {"items": [dict(_ASSET)]},
        "buckets": {"items": [bucket], "scan": [bucket]},
    }


def _tables(calls, rows):
    names = _logical_names()

    def _table(name):
        logical = names.get(name, name)
        return _RecordingTable(logical, calls, rows.get(logical, {}))

    return _table


_METADATA_ROW = {"databaseId": "db1", "assetId": "a1", "filePath": "/", "metadata": {"k": "v"},
                 "scope": "asset"}


def _launch(restriction="perAsset", metadata_rows=None, refused_trigger=False):
    """One launch through lambda_handler; returns (response, recorded calls). `metadata_rows` stands in
    for the input-metadata rows the launch records for each pipeline. With `refused_trigger` the launch
    is the trigger dispatcher's (SYSTEM_USER, triggerType fileUpload) and its lock is held by another
    run, so the handler writes the skip record instead of launching."""
    calls = set()
    body = dict(_TWO_FILES, triggerType="fileUpload") if refused_trigger else _TWO_FILES
    with contextlib.ExitStack() as stack:
        if metadata_rows is not None:
            stack.enter_context(patch.object(ewv2.er, "pipeline_metadata_envelope_rows",
                                             return_value=list(metadata_rows)))
        if refused_trigger:
            stack.enter_context(patch.object(
                ewv2.el, "acquire_locks",
                side_effect=ewv2.el.ExecutionLockConflict(f"{WF_DB}:{WF_ID}|asset|db1:a1", "e-holder")))
        for context in (
            patch(f"{MOD}._input_exists_in_s3", return_value=(True, "v-resolved")),
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_enforcer()),
            patch(f"{MOD}.request_to_claims",
                  return_value={"tokens": ["SYSTEM_USER" if refused_trigger else "user1"]}),
            patch(f"{MOD}.s3c"),
            patch(f"{MOD}._bucket_details_cache", {}),
            patch.object(ewv2.dynamodb, "Table", side_effect=_tables(calls, _rows(restriction))),
        ):
            stack.enter_context(context)
        sfn = stack.enter_context(patch(f"{MOD}.sfn_client"))
        sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        event = _event(body)
        if refused_trigger:
            # The dispatcher's envelope: a fileUpload trigger type is accepted only from a system launch.
            event["lambdaCrossCall"] = {"userName": "SYSTEM_USER"}
        response = ewv2.lambda_handler(event, MagicMock())
    return response, calls


def _granted(calls):
    return {(table, action) for table, action in calls
            if action in EXECUTE_WORKFLOW_GRANTS.get(table, set())}


@pytest.mark.unit
class TestTheLaunchCallsOnlyWhatTheRoleGrants:

    @pytest.mark.parametrize("restriction", ["none", "perAsset", "perInputFile", "perInputFileVersion"])
    def test_a_launch_makes_only_granted_calls(self, restriction):
        response, calls = _launch(restriction)
        # Control: the launch got as far as writing its records, so the recorded set is a whole launch.
        assert response["statusCode"] == 200, response
        assert ("main", "PutItem") in calls and ("pipelineExecutions", "PutItem") in calls
        assert calls == _granted(calls), sorted(calls - _granted(calls))

    def test_a_locking_launch_takes_its_lock(self):
        # The acquire reads a conflicting holder's main row only on a conflict; that read is recorded
        # by test_the_holder_record_read_is_granted.
        _response, calls = _launch("perAsset")
        assert ("locks", "PutItem") in calls

    def test_the_input_metadata_rows_go_out_through_one_batch_writer(self):
        response, calls = _launch("none", metadata_rows=[_METADATA_ROW])
        assert response["statusCode"] == 200, response
        assert ("pipelineInputMetadata", "BatchWriteItem") in calls
        assert calls == _granted(calls), sorted(calls - _granted(calls))

    def test_the_template_reads_are_granted(self):
        calls = set()
        rows = {"templates": {"items": [{"templateId": "t1", "isDefault": True}]}, "tagSchema": {}}
        with patch.object(ewv2.dynamodb, "Table", side_effect=_tables(calls, rows)):
            ewv2._get_template_row("db1", "p1", "t1")
            ewv2._get_default_template_id("db1", "p1")
            ewv2._load_tag_schema_fields("db1", "p1", "t1", "run-bucket")
        assert calls == {("templates", "GetItem"), ("templates", "Query"),
                         ("tagSchema/index/TagSchemaByTemplateGSI", "Query")}
        assert calls == _granted(calls)

    def test_the_holder_record_read_is_granted(self):
        calls = set()
        with patch.object(ewv2.dynamodb, "Table", side_effect=_tables(calls, {})):
            ewv2._holder_has_execution_record(WF_DB, WF_ID)("e-holder")
        assert calls == {("main", "GetItem")} == _granted(calls)

    def test_a_refused_trigger_launch_writes_its_skip_record_with_granted_calls(self):
        response, calls = _launch("perAsset", refused_trigger=True)
        assert response["statusCode"] == 400, response
        assert ("main", "UpdateItem") in calls and ("inputs", "PutItem") in calls
        assert calls == _granted(calls), sorted(calls - _granted(calls))

    def test_every_grant_is_used(self):
        # The grants are exact: each is made by some launch path above, the main-row UpdateItem by the
        # skip record a trigger launch refused by a concurrency restriction writes.
        calls = set()
        for restriction in ("none", "perAsset", "perInputFile", "perInputFileVersion"):
            calls |= _launch(restriction)[1]
        calls |= _launch("none", metadata_rows=[_METADATA_ROW])[1]
        calls |= _launch("perAsset", refused_trigger=True)[1]
        rows = {"templates": {"items": [{"templateId": "t1", "isDefault": True}]}}
        with patch.object(ewv2.dynamodb, "Table", side_effect=_tables(calls, rows)):
            ewv2._get_template_row("db1", "p1", "t1")
            ewv2._get_default_template_id("db1", "p1")
            ewv2._load_tag_schema_fields("db1", "p1", "t1", "run-bucket")
            ewv2._holder_has_execution_record(WF_DB, WF_ID)("e-holder")
            ewv2.el.release_locks(ewv2.dynamodb.Table(ewv2.workflow_execution_locks_table),
                                  ["k"], "e-holder")
        granted = {(table, action) for table, actions in EXECUTE_WORKFLOW_GRANTS.items()
                   for action in actions}
        assert granted - calls == set()
