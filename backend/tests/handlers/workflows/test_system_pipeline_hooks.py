# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow-core hooks for system pipelines (common/workflows/systemPipelines.py).

A system pipeline run is one VAMS launches for a feature. What holds it apart from an ordinary
execution, and what the core deliberately does NOT do, is pinned here:

1. Trigger and context are SERVER-ONLY. The system trigger value and the system execution context are
   accepted only from a lambdaCrossCall carrying SYSTEM_USER and the system-pipeline source marker. A
   public execute request (or a trigger dispatch, a re-run or the schema importer) can never set either:
   the request model refuses a system trigger value, and a body or unmarked event carrying the context
   is ignored.
2. The trigger value is GENERIC. Core accepts any value in the documented format (the System- prefix
   plus a safe identifier, bounded length) and stores it as given; it keeps no list of features.
3. There is NO terminal callback. Every terminal writer (end state, error handler, abort, group abort,
   read-path reconcile, the status change push) writes a system run's row exactly as it writes an
   ordinary run's. A feature tracks its run by the workflowExecutionId it stored.
4. Re-run of a system run is refused with a 400.
5. Core workflow code names no specific pipeline or feature (a source scan).

The race tests run the real guarded writes against a moto DynamoDB table, so the condition is exercised
rather than pattern-matched.
"""

import contextlib
import json
import os
import pathlib
import re
import sys
import types

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("ASSET_UPLOAD_TABLE_NAME", "t-asset-upload"),
    ("DATABASE_STORAGE_TABLE_NAME", "t-db"),
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
    ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
    ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc"),
    ("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "t-upload"),
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
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
    ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates"),
    ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema"),
    ("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows"),
    ("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
    ("WORKFLOW_EXECUTION_LOG_GROUP_ARN", "arn:aws:logs:us-east-1:1:log-group:vams-wf:*"),
    ("WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME", "t-wf-locks"),
]:
    os.environ.setdefault(_name, _value)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ewv2  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh  # noqa: E402
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

# Every handler holds the SAME helper module object (imported as common.workflows.systemPipelines);
# take it from a handler so a patch reaches the module the handlers call.
sp = le.sp
assert ewv2.sp is sp

ES = "backend.backend.handlers.workflows.executionService"
EW = "backend.backend.handlers.workflows.executeWorkflow"
WF_DB, WF_ID = "wdb", "wf1"
EXEC = "e1000000000000000000000000000001"
EXEC2 = "e2000000000000000000000000000002"
# A placeholder system trigger value: core treats every well-formed value alike.
SYSTEM_TRIGGER = "System-TestFeature"
CONTEXT = {"requestId": "r-1", "objectKey": "feature/x/object.json"}

# The main-row attribute the removed callback design used to hold an unconfirmed abort's callback.
REMOVED_PENDING_ATTRIBUTE = "systemCallbackPending"


def _row(execution_id=EXEC, trigger=SYSTEM_TRIGGER, status="RUNNING", context=CONTEXT):
    row = {"workflowExecutionId": execution_id,
           "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}",
           "workflowDatabaseId": WF_DB, "workflowId": WF_ID,
           "workflow_execution_arn": f"arn:aws:states:us-east-1:1:execution:wf:{execution_id}",
           "executionStatus": status, "executionStopDate": "", "triggerType": trigger,
           "triggeredByUserId": "SYSTEM_USER", "executionStartDate": "2026-11-05T00:00:00Z"}
    if context is not None and sp.is_system_trigger(trigger):
        row["systemExecutionContext"] = sp.serialize_context(context)
    return row


@contextlib.contextmanager
def _main_table(*rows):
    """A moto-backed V2 main execution table seeded with `rows`; yields the table."""
    from moto import mock_aws
    import boto3
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="main-sp",
            KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                       {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
                {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        table = ddb.Table("main-sp")
        for row in rows:
            table.put_item(Item=row)
        yield table


def _stored(table, execution_id=EXEC):
    return table.get_item(Key={"workflowExecutionId": execution_id,
                               "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}"})["Item"]


def _without(item, *keys):
    return {k: v for k, v in item.items() if k not in keys}


# ---------------------------------------------------------------------------------------------------
# 1) The helper: who may set the system trigger, and the generic trigger value contract
# ---------------------------------------------------------------------------------------------------

def _marked(spec=None, user="SYSTEM_USER", source=sp.SYSTEM_PIPELINE_SOURCE_MARKER):
    event = {"lambdaCrossCall": {"userName": user, "source": source}}
    if spec is not None:
        event[sp.SYSTEM_EXECUTION_EVENT_KEY] = spec
    return event


@pytest.mark.unit
class TestSystemTriggerFormat:
    @pytest.mark.parametrize("value", [
        "System-A", "System-TestFeature", "System-Feature-2", "System-x1-y2-z3",
        "System-" + "a" * (sp.MAX_SYSTEM_TRIGGER_LENGTH - len("System-")),  # the bound is inclusive
    ])
    def test_a_well_formed_value_is_a_system_trigger(self, value):
        assert sp.is_system_trigger(value) is True

    @pytest.mark.parametrize("value", [
        "Manual", "File-Upload", "manual", "fileUpload",           # the public values
        "System-", "System", "system-Feature", "SYSTEM-Feature",  # prefix exact, identifier required
        "System--Feature", "System-Feature-", "System-Fe ature",  # one hyphen between words, no space
        "System-Feat_ure", "System-Feat:ure", "System-Feat/ure",  # no separators beyond the hyphen
        "System-Feät", "System-Feature\n",                        # ASCII only, no trailing newline
        "System-" + "a" * (sp.MAX_SYSTEM_TRIGGER_LENGTH - len("System-") + 1),  # over the bound
        "", None, 7, ["System-A"],
    ])
    def test_anything_else_is_not(self, value):
        assert sp.is_system_trigger(value) is False

    def test_core_keeps_no_list_of_system_trigger_values(self):
        # The contract is the format; a registry of features would make core name them.
        assert not [name for name in vars(sp) if re.search(r"TRIGGER_TYPES|TRIGGER_TYPE_TO_STORED", name)]


@pytest.mark.unit
class TestResolveSystemExecution:
    def test_a_marked_call_stores_the_trigger_as_given_and_compact_context(self):
        stored, text = sp.resolve_system_execution(
            _marked({"triggerType": SYSTEM_TRIGGER, "context": {"b": 2, "a": 1}}))
        assert stored == SYSTEM_TRIGGER
        assert text == '{"a":1,"b":2}'

    def test_an_absent_context_is_stored_as_the_empty_object(self):
        assert sp.resolve_system_execution(_marked({"triggerType": SYSTEM_TRIGGER})) == (
            SYSTEM_TRIGGER, "{}")

    @pytest.mark.parametrize("event", [
        # An API Gateway event: the context can only ride in the body, which is never read here.
        {"requestContext": {"http": {"method": "POST"}},
         "body": json.dumps({"systemExecution": {"triggerType": SYSTEM_TRIGGER, "context": {"x": 1}}})},
        # A top-level system object WITHOUT the marker (e.g. riding a trigger-dispatch envelope).
        {"lambdaCrossCall": {"userName": "SYSTEM_USER"},
         sp.SYSTEM_EXECUTION_EVENT_KEY: {"triggerType": SYSTEM_TRIGGER, "context": {}}},
        # The schema importer's marker is a different marker.
        {"lambdaCrossCall": {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"},
         sp.SYSTEM_EXECUTION_EVENT_KEY: {"triggerType": SYSTEM_TRIGGER}},
        # The marker under a real user's identity (a re-run cross-call carries the caller's name).
        _marked({"triggerType": SYSTEM_TRIGGER}, user="alice@example.com"),
        # The source marker inside the body is just body data.
        {"body": json.dumps({"lambdaCrossCall": {"userName": "SYSTEM_USER",
                                                 "source": sp.SYSTEM_PIPELINE_SOURCE_MARKER}})},
        None, "not-an-event",
    ])
    def test_no_other_caller_reaches_the_system_trigger(self, event):
        assert sp.resolve_system_execution(event) is None

    @pytest.mark.parametrize("spec", [
        None,                                                  # marker without the system object
        SYSTEM_TRIGGER,                                        # not an object
        {},                                                    # no trigger
        {"triggerType": "manual"},                             # a public trigger is not a system one
        {"triggerType": "Manual"},
        {"triggerType": "TestFeature"},                        # the prefix is required
        {"triggerType": "System-bad value"},                   # outside the format
        {"triggerType": SYSTEM_TRIGGER, "context": ["a"]},     # context must be an object
        {"triggerType": SYSTEM_TRIGGER, "context": "text"},
        {"triggerType": SYSTEM_TRIGGER,
         "context": {"k": "x" * sp.MAX_SYSTEM_EXECUTION_CONTEXT_BYTES}},  # over the bound
    ])
    def test_a_malformed_marked_call_is_refused(self, spec):
        with pytest.raises(sp.SystemExecutionError):
            sp.resolve_system_execution(_marked(spec))

    def test_the_context_bound_is_inclusive(self):
        # '{"k":"' + value + '"}' is 8 bytes of structure.
        value = "x" * (sp.MAX_SYSTEM_EXECUTION_CONTEXT_BYTES - 8)
        _stored_value, text = sp.resolve_system_execution(
            _marked({"triggerType": SYSTEM_TRIGGER, "context": {"k": value}}))
        assert len(text.encode("utf-8")) == sp.MAX_SYSTEM_EXECUTION_CONTEXT_BYTES


# ---------------------------------------------------------------------------------------------------
# 2) The execute contract: a public request can never set the trigger or the context
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestPublicRequestModel:
    @pytest.mark.parametrize("trigger", [SYSTEM_TRIGGER, "System-Other"])
    def test_the_request_model_refuses_a_system_trigger(self, trigger):
        # pydantic's ValidationError subclasses ValueError, which avoids the dual-import identity trap.
        with pytest.raises(ValueError, match="triggerType must be one of"):
            ewv2.ExecuteWorkflowRequestV2Model(triggerType=trigger)

    def test_a_context_in_the_body_is_dropped_by_the_model(self):
        parsed = ewv2.ExecuteWorkflowRequestV2Model(
            systemExecutionContext={"x": 1}, systemExecution={"triggerType": SYSTEM_TRIGGER})
        assert not hasattr(parsed, "systemExecutionContext")
        assert not hasattr(parsed, "systemExecution")
        assert parsed.triggerType == "manual"


def _harness():
    from backend.tests.handlers.workflows import test_executeWorkflow as tew
    return tew


def _launch_through_handler(event, tokens=("user1",)):
    """Drive the execute handler's happy path (test_executeWorkflow's harness). Returns
    (response, main row put, pipeline-1 manifest, SFN input)."""
    tew = _harness()
    orchestration = tew.TestExecuteOrchestration()
    p = orchestration._patches()
    tables = {}
    with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
            p["asset_bucket"], p["exists"], p["enforcer"], \
            patch(f"{EW}.request_to_claims", return_value={"tokens": list(tokens)}), \
            patch(f"{EW}.s3c") as m_s3, patch(f"{EW}.sfn_client") as m_sfn, \
            patch(f"{EW}.dynamodb") as m_dynamo:
        m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
        response = ewv2.lambda_handler(event, MagicMock())
    main_puts = [c.kwargs["Item"] for c in
                 tables.get(ewv2.workflow_execution_database_v2, MagicMock()).put_item.call_args_list]
    manifests = [json.loads(c.kwargs["Body"].decode("utf-8")) for c in m_s3.put_object.call_args_list
                 if c.kwargs.get("Key", "").endswith("pipeline1/manifest.json")]
    sfn_inputs = [json.loads(c.kwargs["input"]) for c in m_sfn.start_execution.call_args_list]
    return (response, main_puts[0] if main_puts else None, manifests[0] if manifests else None,
            sfn_inputs[0] if sfn_inputs else None)


_BODY = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}


def _system_event(spec):
    tew = _harness()
    event = tew._event(body=_BODY)
    event.pop("headers", None)
    event["requestContext"].pop("authorizer", None)
    event["lambdaCrossCall"] = {"userName": "SYSTEM_USER", "source": sp.SYSTEM_PIPELINE_SOURCE_MARKER}
    event[sp.SYSTEM_EXECUTION_EVENT_KEY] = spec
    return event


@pytest.mark.unit
class TestExecuteHandlerSystemTrigger:
    def test_a_public_request_naming_a_system_trigger_is_refused_400(self):
        tew = _harness()
        response, main, _manifest, _sfn = _launch_through_handler(
            tew._event(body=dict(_BODY, triggerType=SYSTEM_TRIGGER)))
        assert response["statusCode"] == 400
        assert main is None

    def test_a_public_request_carrying_the_context_launches_as_an_ordinary_run(self):
        tew = _harness()
        body = dict(_BODY, systemExecutionContext=CONTEXT,
                    systemExecution={"triggerType": SYSTEM_TRIGGER, "context": CONTEXT},
                    lambdaCrossCall={"userName": "SYSTEM_USER",
                                     "source": sp.SYSTEM_PIPELINE_SOURCE_MARKER})
        response, main, manifest, sfn_input = _launch_through_handler(tew._event(body=body))
        assert response["statusCode"] == 200, response["body"]
        assert main["triggerType"] == "Manual"
        assert "systemExecutionContext" not in main
        assert "systemExecutionContext" not in manifest
        assert "requestId" not in json.dumps(sfn_input)

    def test_an_ordinary_run_keeps_its_exact_manifest_and_row_shape(self):
        # Non-system runs unchanged: no new key anywhere.
        tew = _harness()
        response, main, manifest, _sfn = _launch_through_handler(tew._event(body=_BODY))
        assert response["statusCode"] == 200
        assert set(manifest) == {"schemaVersion", "inputFiles", "inputMetadataS3Location", "outputs",
                                 "outputTarget", "auxBucket", "auxTempPrefix",
                                 "auxPreviewPipelineSuffix", "systemConfig"}
        assert "systemExecutionContext" not in main
        assert main["triggerType"] == "Manual"

    def test_a_marked_call_records_the_system_trigger_and_hands_the_context_to_the_manifest(self):
        response, main, manifest, sfn_input = _launch_through_handler(
            _system_event({"triggerType": SYSTEM_TRIGGER, "context": CONTEXT}),
            tokens=("SYSTEM_USER",))
        assert response["statusCode"] == 200, response["body"]
        assert main["triggerType"] == SYSTEM_TRIGGER
        assert main["triggeredByUserId"] == "SYSTEM_USER"
        assert json.loads(main["systemExecutionContext"]) == CONTEXT
        assert manifest["systemExecutionContext"] == CONTEXT
        # Never inside systemConfig, and never in the logged Step Functions input.
        assert "systemExecutionContext" not in manifest["systemConfig"]
        assert "requestId" not in json.dumps(sfn_input)
        assert sp.SYSTEM_EXECUTION_EVENT_KEY not in json.dumps(sfn_input)
        # The feature tracks the run by this id.
        assert json.loads(response["body"])["message"]

    @pytest.mark.parametrize("spec", [
        {"triggerType": SYSTEM_TRIGGER, "context": "nope"},
        {"triggerType": "Not-A-System-Value"},
    ])
    def test_a_malformed_marked_call_is_refused_before_any_read(self, spec):
        with patch(f"{EW}._get_workflow") as get_workflow:
            response, main, _manifest, _sfn = _launch_through_handler(
                _system_event(spec), tokens=("SYSTEM_USER",))
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == sp.SYSTEM_EXECUTION_INVALID_MESSAGE
        get_workflow.assert_not_called()
        assert main is None


# ---------------------------------------------------------------------------------------------------
# 3) The terminal writers: a system run's row is written exactly like an ordinary run's
# ---------------------------------------------------------------------------------------------------

class _Dynamo:
    """A dynamo resource stand-in: the main table is the real (moto) one, the rest record calls."""

    def __init__(self, main_name, main_table):
        self.main_name, self.main_table = main_name, main_table
        self.others = {}

    def Table(self, name):  # noqa: N802 - mirrors boto3
        if name == self.main_name:
            return self.main_table
        return self.others.setdefault(name, MagicMock())


def _end_state(dynamo, status="SUCCEEDED", error=""):
    po._write_execution_outputs(
        dynamo, EXEC, "pe-1", WF_DB, WF_ID, "run-bucket", [], [], [], "result", "log", "", "",
        status, error)


def _end_state_row(trigger):
    with _main_table(_row(trigger=trigger)) as table:
        _end_state(_Dynamo(po.workflow_execution_database_v2, table))
        return _stored(table)


@pytest.mark.unit
@pytest.mark.aws
class TestTerminalWritersTreatSystemRunsAlike:
    def test_the_end_state_writes_a_system_run_like_a_manual_run(self):
        system, manual = _end_state_row(SYSTEM_TRIGGER), _end_state_row("Manual")
        assert system["executionStatus"] == manual["executionStatus"] == "SUCCEEDED"
        assert _without(system, "executionStopDate", "lastSfnSyncCheckDate", "triggerType",
                        "systemExecutionContext") == \
            _without(manual, "executionStopDate", "lastSfnSyncCheckDate", "triggerType")
        assert system["systemExecutionContext"] == sp.serialize_context(CONTEXT)

    def test_the_end_state_losing_the_race_keeps_the_other_writers_status(self):
        with _main_table(_row(status="ABORTED")) as table:
            _end_state(_Dynamo(po.workflow_execution_database_v2, table))
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_the_error_handler_fails_a_system_run_like_any_run(self):
        with _main_table(_row()) as table:
            assert _error_handler(table) == {"handled": True}
            stored = _stored(table)
        assert stored["executionStatus"] == "FAILED"
        assert "step failed" in stored.get("executionError", "")

    def test_the_error_handler_keeps_an_already_terminal_row(self):
        with _main_table(_row(status="SUCCEEDED")) as table:
            _error_handler(table)
            assert _stored(table)["executionStatus"] == "SUCCEEDED"

    def test_an_abort_writes_aborted(self):
        with _main_table(_row()) as table:
            with _abort(table, [_row()]) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_an_abort_losing_to_the_end_state_keeps_its_status(self):
        with _main_table(_row(status="SUCCEEDED")) as table:
            with _abort(table, [_row(status="RUNNING")]) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert _stored(table)["executionStatus"] == "SUCCEEDED"

    def test_a_group_abort_aborts_every_member(self):
        rows = [_row(EXEC), _row(EXEC2)]
        with _main_table(*rows) as table:
            with _abort(table, rows) as drive:
                assert drive["group"]()["statusCode"] == 200
            assert {_stored(table, e)["executionStatus"] for e in (EXEC, EXEC2)} == {"ABORTED"}

    @pytest.mark.parametrize("trigger", [SYSTEM_TRIGGER, "Manual"])
    def test_an_unconfirmed_stop_writes_aborted_and_nothing_else(self, trigger):
        with _main_table(_row(trigger=trigger)) as table:
            with _abort(table, [_row(trigger=trigger)], stop_confirmed=False) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            stored = _stored(table)
        assert stored["executionStatus"] == "ABORTED"
        assert REMOVED_PENDING_ATTRIBUTE not in stored

    def test_no_writer_invokes_another_lambda_for_a_system_run(self):
        # The removed callback was an asynchronous Lambda invoke; no terminal path makes one now.
        with _main_table(_row()) as table, \
                patch.object(le.lambda_client, "invoke",
                             side_effect=AssertionError("lambda invoke")) as invoke, \
                _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
            le._reconcile_main_status(EXEC, _row())
            le.lambda_handler(_status_event(), MagicMock())
            with _abort(table, [_stored(table)]) as drive:
                drive["single"](EXEC)
        invoke.assert_not_called()


def _error_handler(table):
    with patch.object(heh.dynamodb, "Table",
                      side_effect=lambda name: table if name == heh.workflow_execution_database_v2
                      else MagicMock()), \
            patch.object(heh, "_get_pipeline_rows", return_value=[]), \
            patch.object(heh, "_fetch_execution_log", return_value=""), \
            patch.object(heh, "_execution_start_date", return_value=""), \
            patch.object(heh, "_refresh_execution_log"), \
            patch.object(heh.el, "release_locks_for_execution"):
        return heh.lambda_handler(
            {"body": {"workflowExecutionId": EXEC, "workflowDatabaseId": WF_DB, "workflowId": WF_ID},
             "errorInfo": {"Error": "States.TaskFailed", "Cause": "step failed"}}, MagicMock())


@contextlib.contextmanager
def _abort(table, rows, stop_confirmed=True):
    """Patch the abort route around the real guarded main-row write; `rows` are what the abort READS
    (which may differ from what the table holds, to stage a race). `stop_confirmed=False` makes
    StopExecution fail while DescribeExecution still shows the execution running. Yields
    single/group drivers."""
    by_id = {r["workflowExecutionId"]: r for r in rows}
    stop = {"return_value": {}} if stop_confirmed else {"side_effect": ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "t"}}, "StopExecution")}
    with patch.object(le.dynamodb, "Table",
                      side_effect=lambda name: table if name == le.workflow_execution_database_v2
                      else MagicMock()), \
            patch(f"{ES}.get_execution_main_row", side_effect=lambda eid: dict(by_id[eid])), \
            patch(f"{ES}.authorize_abort", return_value=(True, "")), \
            patch(f"{ES}.get_pipeline_execution_rows", return_value=[]), \
            patch.object(le.sfn, "stop_execution", **stop), \
            patch.object(le.el, "sfn_execution_has_ended", return_value=False), \
            patch(f"{ES}.log_actions"), \
            patch.object(le.el, "release_locks_for_execution"), \
            patch(f"{ES}._executions_in_group", return_value=[dict(r) for r in rows]):
        yield {
            "single": lambda eid: le.abort_execution({"requestContext": {"http": {}}}, eid),
            "group": lambda: le.abort_group({"requestContext": {"http": {}}}, "g1"),
        }


def _stopped(status="ABORTED"):
    from datetime import datetime, timezone
    return {"status": status, "stopDate": datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc),
            "startDate": datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)}


@contextlib.contextmanager
def _reconcile_env(table):
    with patch.object(le.dynamodb, "Table",
                      side_effect=lambda name: table if name == le.workflow_execution_database_v2
                      else MagicMock()), \
            patch(f"{ES}._settle_reconciled_terminal"), \
            patch(f"{ES}._terminal_log_and_error", return_value=("", "")):
        yield


@pytest.mark.unit
@pytest.mark.aws
class TestReadPathReconcile:
    def test_the_details_reconcile_records_a_run_stopped_outside_vams(self):
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("TIMED_OUT")):
            le._reconcile_main_status(EXEC, _row())
            assert _stored(table)["executionStatus"] == "TIMED_OUT"

    def test_the_details_reconcile_losing_the_race_keeps_the_other_writers_status(self):
        with _main_table(_row(status="FAILED")) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
            le._reconcile_main_status(EXEC, _row())  # the read saw RUNNING
            assert _stored(table)["executionStatus"] == "FAILED"

    def test_a_terminal_row_is_never_polled(self):
        done = dict(_row(status="ABORTED"), executionStopDate="2026-11-05T02:00:00Z")
        with _main_table(done) as table, _reconcile_env(table), \
                patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                patch.object(le.sfn, "describe_execution") as describe:
            le._reconcile_main_status(EXEC, dict(done))
        describe.assert_not_called()

    def test_the_details_read_swallows_a_throttled_describe(self):
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", side_effect=_throttled()):
            le._reconcile_main_status(EXEC, _row())
            assert _stored(table)["executionStatus"] == "RUNNING"


def _status_event(status="ABORTED", execution_id=EXEC, execution_arn=None,
                  state_machine="arn:aws:states:us-east-1:1:stateMachine:vams-wf1abcd1234"):
    """An EventBridge Step Functions execution status change event, as the rule delivers it."""
    return {
        "version": "0", "id": "ev-1", "detail-type": "Step Functions Execution Status Change",
        "source": "aws.states", "account": "1", "region": "us-east-1",
        "time": "2026-11-05T02:00:00Z", "resources": [execution_arn or _row(execution_id)[
            "workflow_execution_arn"]],
        "detail": {
            "executionArn": execution_arn or _row(execution_id)["workflow_execution_arn"],
            "stateMachineArn": state_machine, "name": execution_id, "status": status,
            "startDate": 1762304400000, "stopDate": 1762308000000,
            "input": "{\"secret\": \"input-must-not-be-logged\"}", "output": None,
        },
    }


def _throttled(operation="DescribeExecution"):
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                       operation)


@pytest.mark.unit
@pytest.mark.aws
class TestStatusChangeEvent:
    """The push: the execution service handling a Step Functions execution status change event, for
    every workflow run (system or not)."""

    @pytest.mark.parametrize("trigger", [SYSTEM_TRIGGER, "Manual"])
    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"])
    def test_each_terminal_status_is_reconciled(self, status, trigger):
        with _main_table(_row(trigger=trigger)) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped(status)):
            assert le.lambda_handler(_status_event(status), MagicMock()) == {
                "handled": True, "reconciled": True}
            assert _stored(table)["executionStatus"] == status

    def test_the_status_recorded_is_step_functions_own_not_the_events(self):
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("TIMED_OUT")):
            le.lambda_handler(_status_event("SUCCEEDED"), MagicMock())
            assert _stored(table)["executionStatus"] == "TIMED_OUT"

    def test_the_poll_interval_does_not_delay_the_push(self):
        recent = dict(_row(), lastSfnSyncCheckDate=le.er.iso_now())
        with _main_table(recent) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
            le.lambda_handler(_status_event(), MagicMock())
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_a_duplicate_event_does_nothing_more(self):
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")) as d:
            le.lambda_handler(_status_event(), MagicMock())
            assert le.lambda_handler(_status_event(), MagicMock()) == {
                "handled": True, "reconciled": False}
        assert d.call_count == 1

    def test_the_push_racing_a_read_keeps_the_first_writers_status(self):
        # The push read RUNNING; a read-path reconcile finished the row before the push's write.
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                patch(f"{ES}.get_execution_main_row", return_value=_row()):
            le._reconcile_main_status(EXEC, _row())
            assert le.lambda_handler(_status_event(), MagicMock()) == {
                "handled": True, "reconciled": True}
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_a_run_finished_through_the_state_machine_costs_one_read(self):
        done = dict(_row(status="SUCCEEDED"), executionStopDate="2026-11-05T02:00:00Z")
        with _main_table(done) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution") as describe:
            le.lambda_handler(_status_event("SUCCEEDED"), MagicMock())
        describe.assert_not_called()

    def test_an_unconfirmed_abort_needs_nothing_from_the_event(self):
        with _main_table(_row()) as table:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            with _reconcile_env(table), \
                    patch.object(le.sfn, "describe_execution") as describe:
                assert le.lambda_handler(_status_event("ABORTED"), MagicMock()) == {
                    "handled": True, "reconciled": False}
            describe.assert_not_called()
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_a_missing_row_is_ignored(self):
        with _main_table() as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution") as describe:
            assert le.lambda_handler(_status_event(), MagicMock())["handled"] is False
        describe.assert_not_called()

    # The rule matches every VAMS deployment's workflow state machines in the account and Region, so
    # most events it delivers belong to another deployment. Each must end quietly: a raise would make
    # Lambda retry the event and spend this deployment's reads on executions it does not own (in-dev
    # D2, #1135). No Step Functions call and no write.
    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"])
    @pytest.mark.parametrize("event", [
        # Another deployment's run, an execution id this deployment never stored.
        lambda status: _status_event(
            status, execution_id="99999999-9999-4999-8999-999999999999",
            execution_arn="arn:aws:states:us-east-1:1:execution:vams-other1234:"
                          "99999999-9999-4999-8999-999999999999",
            state_machine="arn:aws:states:us-east-1:1:stateMachine:vams-other1234"),
        # The same execution id under another deployment's state machine.
        lambda status: _status_event(
            status, execution_arn="arn:aws:states:us-east-1:1:execution:vams-other1234:" + EXEC,
            state_machine="arn:aws:states:us-east-1:1:stateMachine:vams-other1234"),
        # A run of a non-VAMS state machine whose name happens to start with the prefix.
        lambda status: dict(_status_event(status), detail=dict(
            _status_event(status)["detail"], name="nightly-batch-run",
            executionArn="arn:aws:states:us-east-1:1:execution:vams-reports:nightly-batch-run")),
        # A detail with no execution ARN.
        lambda status: dict(_status_event(status), detail=dict(
            _status_event(status)["detail"], executionArn=None)),
    ], ids=["unknown-execution", "same-id-other-arn", "non-guid-name", "no-execution-arn"])
    def test_another_deployments_event_ends_without_raising_or_any_side_effect(self, event, status):
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution",
                             side_effect=AssertionError("describe")) as describe:
            result = le.lambda_handler(event(status), MagicMock())
            assert result["handled"] is False
            stored = _stored(table)
            assert stored["executionStatus"] == "RUNNING"
            assert not stored.get("executionStopDate")
        describe.assert_not_called()

    @pytest.mark.parametrize("event", [
        {"source": "aws.states", "detail-type": "Step Functions Execution Status Change",
         "detail": {"name": "not-an-execution-id", "status": "ABORTED", "executionArn": "x"}},
        {"source": "aws.states", "detail-type": "Step Functions Execution Status Change",
         "detail": {"name": EXEC, "status": "RUNNING", "executionArn": "x"}},
    ])
    def test_a_non_vams_or_non_terminal_event_is_ignored_before_any_read(self, event):
        with patch(f"{ES}.get_execution_main_row", side_effect=AssertionError("read")):
            assert le.lambda_handler(event, MagicMock())["handled"] is False

    def test_the_event_is_never_logged_and_never_reaches_the_api_dispatch(self):
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                patch.object(le.logger, "info") as info, \
                patch(f"{ES}.request_to_claims", side_effect=AssertionError("api path")):
            le.lambda_handler(_status_event(), MagicMock())
        assert "input-must-not-be-logged" not in str(info.call_args_list)

    def test_the_permanent_delete_function_ignores_the_event(self):
        with patch.dict(os.environ, {le.EXECUTION_SERVICE_ROUTES_ENV: le.ROUTE_SET_PERMANENT_DELETE}), \
                patch(f"{ES}.get_execution_main_row", side_effect=AssertionError("read")):
            assert le.lambda_handler(_status_event(), MagicMock())["handled"] is False

    @pytest.mark.parametrize("event", [
        {"requestContext": {"http": {"method": "GET", "path": "/workflows/executions"}},
         "source": "aws.states"},
        {"body": json.dumps({"source": "aws.states",
                             "detail-type": "Step Functions Execution Status Change",
                             "detail": {}})},
    ])
    def test_an_api_request_is_never_taken_for_a_status_event(self, event):
        assert le.is_sfn_status_change_event(event) is False


@pytest.mark.unit
@pytest.mark.aws
class TestStatusEventRetriesOnFailure:
    """The push raises when it could not do its work, so Lambda's asynchronous retries deliver the
    event again; a read path stays best-effort; a lost guard is never a failure."""

    def test_a_throttled_describe_raises_and_the_retry_reconciles_once(self):
        with _main_table(_row()) as table, _reconcile_env(table):
            with patch.object(le.sfn, "describe_execution", side_effect=_throttled()):
                with pytest.raises(ClientError):
                    le.lambda_handler(_status_event(), MagicMock())
            assert _stored(table)["executionStatus"] == "RUNNING"
            # Lambda's retry of the same event, now that Step Functions answers.
            with patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
                assert le.lambda_handler(_status_event(), MagicMock()) == {
                    "handled": True, "reconciled": True}
                # A further retry or duplicate finds the row terminal.
                assert le.lambda_handler(_status_event(), MagicMock()) == {
                    "handled": True, "reconciled": False}
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_a_failed_guarded_write_raises(self):
        with _main_table(_row()) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                patch(f"{ES}._persist_reconciled_main_row", side_effect=_throttled("UpdateItem")):
            with pytest.raises(ClientError):
                le.lambda_handler(_status_event(), MagicMock())

    def test_a_failed_main_row_read_raises(self):
        with patch(f"{ES}.get_execution_main_row", side_effect=_throttled("Query")):
            with pytest.raises(ClientError):
                le.lambda_handler(_status_event(), MagicMock())

    def test_a_lost_guard_returns_without_raising(self):
        # The push read RUNNING; another writer finished the row before the push's guarded write.
        with _main_table(_row(status="FAILED")) as table, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                patch(f"{ES}.get_execution_main_row", return_value=_row()):
            assert le.lambda_handler(_status_event(), MagicMock()) == {
                "handled": True, "reconciled": True}
            assert _stored(table)["executionStatus"] == "FAILED"


# ---------------------------------------------------------------------------------------------------
# 4) Re-run of a system run is refused; the list filter and record model take the value as given
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestRerunGuard:
    def _rerun(self, row, visible=True):
        with patch(f"{ES}.get_execution_main_row", return_value=row), \
                patch(f"{ES}._execution_visible_to_caller", return_value=visible), \
                patch(f"{ES}._enforce_api_route", return_value=True), \
                patch(f"{ES}.get_workflow_execution_configuration_row", return_value={}), \
                patch(f"{ES}._reconstruct_execute_request", return_value={"inputFiles": []}), \
                patch(f"{ES}.log_actions"), \
                patch.object(le, "claims_and_roles", {"tokens": ["user1"]}), \
                patch.object(le.lambda_client, "invoke") as invoke:
            invoke.return_value = {"Payload": MagicMock(read=MagicMock(return_value=json.dumps(
                {"statusCode": 200, "body": json.dumps({"message": {"executionId": "new"}})}
            ).encode("utf-8")))}
            response = le.rerun_execution({"requestContext": {}}, EXEC,
                                          MagicMock(executionGroupId=None))
        return response, invoke

    @pytest.mark.parametrize("trigger", [SYSTEM_TRIGGER, "System-Another-Feature"])
    def test_a_system_run_is_refused_400_and_launches_nothing(self, trigger):
        response, invoke = self._rerun(_row(trigger=trigger))
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == sp.SYSTEM_RERUN_REFUSED_MESSAGE
        invoke.assert_not_called()

    def test_a_caller_who_cannot_see_the_run_gets_403_not_the_refusal(self):
        response, invoke = self._rerun(_row(), visible=False)
        assert response["statusCode"] == 403
        invoke.assert_not_called()

    @pytest.mark.parametrize("trigger", ["Manual", "File-Upload"])
    def test_an_ordinary_run_still_re_runs(self, trigger):
        response, invoke = self._rerun(_row(trigger=trigger))
        assert response["statusCode"] == 200
        invoke.assert_called_once()


@pytest.mark.unit
class TestTriggerFilterSpelling:
    @pytest.mark.parametrize("value, stored", [
        ("manual", "Manual"), ("fileUpload", "File-Upload"),
        (SYSTEM_TRIGGER, SYSTEM_TRIGGER), (" " + SYSTEM_TRIGGER + " ", SYSTEM_TRIGGER),
        ("Other", "Other"),
    ])
    def test_request_spellings_map_to_stored_values(self, value, stored):
        assert le._stored_trigger_type(value) == stored

    @pytest.mark.parametrize("trigger", ["Manual", "File-Upload", SYSTEM_TRIGGER, "System-X"])
    def test_the_stored_record_model_accepts_public_and_system_values(self, trigger):
        from models.executions import WorkflowExecutionRecord
        record = WorkflowExecutionRecord(workflowExecutionId=EXEC, workflowId=WF_ID,
                                         workflowDatabaseId=WF_DB, triggerType=trigger)
        assert record.triggerType == trigger

    @pytest.mark.parametrize("trigger", ["Other", "manual", "System-", "System-bad value"])
    def test_the_stored_record_model_refuses_anything_else(self, trigger):
        from models.executions import WorkflowExecutionRecord
        with pytest.raises(ValueError, match="triggerType must be one of"):
            WorkflowExecutionRecord(workflowExecutionId=EXEC, workflowId=WF_ID,
                                    workflowDatabaseId=WF_DB, triggerType=trigger)


# ---------------------------------------------------------------------------------------------------
# 5) Core workflow code names no specific pipeline or feature, and has no callback registry
# ---------------------------------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
_CORE_SOURCES = [
    _REPO_ROOT / "backend" / "backend" / "common" / "workflows",
    _REPO_ROOT / "backend" / "backend" / "handlers" / "workflows",
    _REPO_ROOT / "infra" / "lib" / "lambdaBuilder" / "workflowFunctions.ts",
]
# Feature-specific trigger or pipeline names (any spelling: Zip-Download, zipDownload, zip_archive,
# utility-zip-archive, ...) and the removed terminal-callback registry.
_FORBIDDEN = re.compile(
    r"zip[-_ ]?(download|archive)"
    r"|CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER|SystemPipelineCallback|notify_terminal_transition"
    r"|SYSTEM_TRIGGER_TYPE_TO_STORED|SYSTEM_PIPELINE_TRIGGER_TYPES",
    re.IGNORECASE)


def _core_files():
    for source in _CORE_SOURCES:
        if source.is_file():
            yield source
        else:
            yield from sorted(p for p in source.rglob("*") if p.suffix in (".py", ".ts"))


@pytest.mark.unit
class TestCoreNamesNoPipeline:
    def test_the_scanned_sources_exist(self):
        missing = [str(s) for s in _CORE_SOURCES if not s.exists()]
        if missing == [str(_CORE_SOURCES[-1])]:
            pytest.skip("infra sources are not present in this checkout")
        assert not missing, missing

    def test_no_feature_specific_trigger_or_callback_name_in_core(self):
        hits = []
        for path in _core_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _FORBIDDEN.search(line):
                    hits.append(f"{path.relative_to(_REPO_ROOT)}:{number}: {line.strip()}")
        assert not hits, "core workflow code must not name a specific pipeline:\n" + "\n".join(hits)

    def test_the_guard_catches_each_spelling(self):
        for sample in ("Zip-Download", "zipDownload", "zip_archive", "utility-zip-archive",
                       "ZipArchiveBuilder", "CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER"):
            assert _FORBIDDEN.search(sample), sample
        # Ordinary Python is not a false hit.
        assert not _FORBIDDEN.search("for n, v in zip(names, values)")
