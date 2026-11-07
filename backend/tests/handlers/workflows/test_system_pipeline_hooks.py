# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow-core hooks for system pipelines (common/workflows/systemPipelines.py).

A system pipeline run is one VAMS launches for a feature. Three things hold it apart from an ordinary
execution, and each is pinned here:

1. Trigger and context are SERVER-ONLY. The system trigger value and the system execution context are
   accepted only from a lambdaCrossCall carrying SYSTEM_USER and the system-pipeline source marker. A
   public execute request (or a trigger dispatch, a re-run or the schema importer) can never set either:
   the request model refuses a system trigger value, and a body or unmarked event carrying the context
   is ignored.
2. Terminal callback. Each of the five terminal writers (end state, error handler, abort, group abort,
   read-path reconcile in the list and details views) sends it exactly once per transition, and only
   when its own guarded write WON the shared terminal guard. A raising callback never changes the run's
   own terminal record. Non-system runs never touch an AWS client for it.
3. Re-run of a system run is refused with a 400.

The race tests run the real guarded writes against a moto DynamoDB table, so the condition is exercised
rather than pattern-matched.
"""

import contextlib
import json
import os
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
assert ewv2.sp is sp and heh.sp is sp and po.sp is sp

ES = "backend.backend.handlers.workflows.executionService"
EW = "backend.backend.handlers.workflows.executeWorkflow"
WF_DB, WF_ID = "wdb", "wf1"
EXEC = "e1000000000000000000000000000001"
EXEC2 = "e2000000000000000000000000000002"
CONTEXT = {"requestId": "r-1", "manifestKey": "preparedDownloads/x/manifest.json"}
CALLBACK_FN = "vams-zip-callback"
REGISTRY = json.dumps({"Zip-Download": CALLBACK_FN})


def _ccf():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "c"}},
                       "UpdateItem")


def _row(execution_id=EXEC, trigger="Zip-Download", status="RUNNING", context=CONTEXT):
    row = {"workflowExecutionId": execution_id,
           "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}",
           "workflowDatabaseId": WF_DB, "workflowId": WF_ID,
           "workflow_execution_arn": f"arn:aws:states:us-east-1:1:execution:wf:{execution_id}",
           "executionStatus": status, "executionStopDate": "", "triggerType": trigger,
           "triggeredByUserId": "SYSTEM_USER", "executionStartDate": "2026-11-05T00:00:00Z"}
    if context is not None and trigger in sp.SYSTEM_TRIGGER_TYPES:
        row["systemExecutionContext"] = sp.serialize_context(context)
    return row


@contextlib.contextmanager
def _main_table(*rows):
    """A moto-backed V2 main execution table seeded with `rows`; yields (name, table)."""
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


@contextlib.contextmanager
def _callback(registry=REGISTRY, invoke_error=None):
    """Install the registry and a recording Lambda client; yields the client mock."""
    client = MagicMock()
    if invoke_error is not None:
        client.invoke.side_effect = invoke_error
    with patch.dict(os.environ, {sp.CALLBACK_REGISTRY_ENV: registry}), \
            patch.object(sp, "_default_lambda_client", return_value=client):
        yield client


def _payloads(client):
    return [json.loads(c.kwargs["Payload"].decode("utf-8")) for c in client.invoke.call_args_list]


# ---------------------------------------------------------------------------------------------------
# 1) The helper: who may set the system trigger, and how the callback is sent
# ---------------------------------------------------------------------------------------------------

def _marked(spec=None, user="SYSTEM_USER", source=sp.SYSTEM_PIPELINE_SOURCE_MARKER):
    event = {"lambdaCrossCall": {"userName": user, "source": source}}
    if spec is not None:
        event[sp.SYSTEM_EXECUTION_EVENT_KEY] = spec
    return event


@pytest.mark.unit
class TestResolveSystemExecution:
    def test_a_marked_call_resolves_the_stored_trigger_and_compact_context(self):
        stored, text = sp.resolve_system_execution(
            _marked({"triggerType": "zipDownload", "context": {"b": 2, "a": 1}}))
        assert stored == "Zip-Download"
        assert text == '{"a":1,"b":2}'

    def test_an_absent_context_is_stored_as_the_empty_object(self):
        assert sp.resolve_system_execution(_marked({"triggerType": "zipDownload"})) == (
            "Zip-Download", "{}")

    @pytest.mark.parametrize("event", [
        # An API Gateway event: the context can only ride in the body, which is never read here.
        {"requestContext": {"http": {"method": "POST"}},
         "body": json.dumps({"systemExecution": {"triggerType": "zipDownload", "context": {"x": 1}}})},
        # A top-level system object WITHOUT the marker (e.g. riding a trigger-dispatch envelope).
        {"lambdaCrossCall": {"userName": "SYSTEM_USER"},
         sp.SYSTEM_EXECUTION_EVENT_KEY: {"triggerType": "zipDownload", "context": {}}},
        # The schema importer's marker is a different marker.
        {"lambdaCrossCall": {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"},
         sp.SYSTEM_EXECUTION_EVENT_KEY: {"triggerType": "zipDownload"}},
        # The marker under a real user's identity (a re-run cross-call carries the caller's name).
        _marked({"triggerType": "zipDownload"}, user="alice@example.com"),
        # The source marker inside the body is just body data.
        {"body": json.dumps({"lambdaCrossCall": {"userName": "SYSTEM_USER",
                                                 "source": sp.SYSTEM_PIPELINE_SOURCE_MARKER}})},
        None, "not-an-event",
    ])
    def test_no_other_caller_reaches_the_system_trigger(self, event):
        assert sp.resolve_system_execution(event) is None

    @pytest.mark.parametrize("spec", [
        None,                                              # marker without the system object
        "zipDownload",                                     # not an object
        {"triggerType": "Zip-Download"},                   # the STORED spelling is not a request value
        {"triggerType": "manual"},                         # a public trigger is not a system trigger
        {"triggerType": "zipDownload", "context": ["a"]},  # context must be an object
        {"triggerType": "zipDownload", "context": "text"},
        {"triggerType": "zipDownload",
         "context": {"k": "x" * sp.MAX_SYSTEM_EXECUTION_CONTEXT_BYTES}},  # over the bound
    ])
    def test_a_malformed_marked_call_is_refused(self, spec):
        with pytest.raises(sp.SystemExecutionError):
            sp.resolve_system_execution(_marked(spec))

    def test_the_bound_is_inclusive(self):
        # '{"k":"' + value + '"}' is 8 bytes of structure.
        value = "x" * (sp.MAX_SYSTEM_EXECUTION_CONTEXT_BYTES - 8)
        _stored, text = sp.resolve_system_execution(
            _marked({"triggerType": "zipDownload", "context": {"k": value}}))
        assert len(text.encode("utf-8")) == sp.MAX_SYSTEM_EXECUTION_CONTEXT_BYTES


@pytest.mark.unit
class TestCallbackRegistryAndNotify:
    @pytest.mark.parametrize("raw, expected", [
        ("", {}),
        ("not json", {}),
        ('["Zip-Download"]', {}),
        ('{"Manual": "fn", "Zip-Download": ""}', {}),
        (REGISTRY, {"Zip-Download": CALLBACK_FN}),
    ])
    def test_registry_parsing(self, raw, expected):
        with patch.dict(os.environ, {sp.CALLBACK_REGISTRY_ENV: raw}):
            assert sp.callback_registry() == expected

    @pytest.mark.parametrize("trigger", ["Manual", "File-Upload", "", None])
    def test_a_non_system_run_creates_no_client(self, trigger):
        row = _row(trigger=trigger)
        with patch.dict(os.environ, {sp.CALLBACK_REGISTRY_ENV: REGISTRY}), \
                patch.object(sp, "_default_lambda_client",
                             side_effect=AssertionError("client created for a non-system run")):
            assert sp.notify_terminal_transition(row, "SUCCEEDED", writer="endState") is False

    def test_an_unregistered_system_trigger_sends_nothing(self):
        with _callback(registry="") as client:
            assert sp.notify_terminal_transition(_row(), "FAILED") is False
        client.invoke.assert_not_called()

    def test_a_non_terminal_status_sends_nothing(self):
        with _callback() as client:
            assert sp.notify_terminal_transition(_row(), "RUNNING") is False
        client.invoke.assert_not_called()

    def test_a_registered_callback_is_sent_once_asynchronously_with_the_contract(self):
        with _callback() as client:
            assert sp.notify_terminal_transition(
                _row(), "ABORTED", stop_date="2026-11-05T01:00:00Z", writer=sp.WRITER_ABORT) is True
        client.invoke.assert_called_once()
        kwargs = client.invoke.call_args.kwargs
        assert kwargs["FunctionName"] == CALLBACK_FN
        assert kwargs["InvocationType"] == "Event"
        payload = _payloads(client)[0]
        assert payload == {
            "callbackType": "systemPipelineTerminal", "schemaVersion": 1,
            "idempotencyKey": f"{EXEC}:ABORTED", "workflowExecutionId": EXEC,
            "workflowDatabaseId": WF_DB, "workflowId": WF_ID, "triggerType": "Zip-Download",
            "executionStatus": "ABORTED", "executionStopDate": "2026-11-05T01:00:00Z",
            "executionError": "", "writer": "abort", "systemExecutionContext": CONTEXT,
        }

    def test_the_error_text_is_bounded(self):
        with _callback() as client:
            sp.notify_terminal_transition(_row(), "FAILED", execution_error="e" * 10000)
        assert len(_payloads(client)[0]["executionError"]) == sp.MAX_CALLBACK_ERROR_CHARS

    def test_a_raising_invoke_never_raises(self):
        with _callback(invoke_error=ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Invoke")):
            assert sp.notify_terminal_transition(_row(), "SUCCEEDED") is False

    def test_an_unreadable_stored_context_reads_as_empty(self):
        row = _row()
        row["systemExecutionContext"] = "{broken"
        with _callback() as client:
            sp.notify_terminal_transition(row, "SUCCEEDED")
        assert _payloads(client)[0]["systemExecutionContext"] == {}


# ---------------------------------------------------------------------------------------------------
# 2) The execute contract: a public request can never set the trigger or the context
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestPublicRequestModel:
    @pytest.mark.parametrize("trigger", ["zipDownload", "Zip-Download"])
    def test_the_request_model_refuses_a_system_trigger(self, trigger):
        # pydantic's ValidationError subclasses ValueError, which avoids the dual-import identity trap.
        with pytest.raises(ValueError, match="triggerType must be one of"):
            ewv2.ExecuteWorkflowRequestV2Model(triggerType=trigger)

    def test_a_context_in_the_body_is_dropped_by_the_model(self):
        parsed = ewv2.ExecuteWorkflowRequestV2Model(
            systemExecutionContext={"x": 1}, systemExecution={"triggerType": "zipDownload"})
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
            tew._event(body=dict(_BODY, triggerType="zipDownload")))
        assert response["statusCode"] == 400
        assert main is None

    def test_a_public_request_carrying_the_context_launches_as_an_ordinary_run(self):
        tew = _harness()
        body = dict(_BODY, systemExecutionContext=CONTEXT,
                    systemExecution={"triggerType": "zipDownload", "context": CONTEXT},
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
            _system_event({"triggerType": "zipDownload", "context": CONTEXT}),
            tokens=("SYSTEM_USER",))
        assert response["statusCode"] == 200, response["body"]
        assert main["triggerType"] == "Zip-Download"
        assert main["triggeredByUserId"] == "SYSTEM_USER"
        assert json.loads(main["systemExecutionContext"]) == CONTEXT
        assert manifest["systemExecutionContext"] == CONTEXT
        # Never inside systemConfig, and never in the logged Step Functions input.
        assert "systemExecutionContext" not in manifest["systemConfig"]
        assert "requestId" not in json.dumps(sfn_input)
        assert sp.SYSTEM_EXECUTION_EVENT_KEY not in json.dumps(sfn_input)

    def test_a_malformed_marked_call_is_refused_before_any_read(self):
        with patch(f"{EW}._get_workflow") as get_workflow:
            response, main, _manifest, _sfn = _launch_through_handler(
                _system_event({"triggerType": "zipDownload", "context": "nope"}),
                tokens=("SYSTEM_USER",))
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == sp.SYSTEM_EXECUTION_INVALID_MESSAGE
        get_workflow.assert_not_called()
        assert main is None


# ---------------------------------------------------------------------------------------------------
# 3) The five terminal writers: once per transition, only on a won guard, never failing the write
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


@pytest.mark.unit
@pytest.mark.aws
class TestWriter1EndState:
    def test_a_won_completion_sends_one_callback(self):
        with _main_table(_row()) as table, _callback() as client:
            _end_state(_Dynamo(po.workflow_execution_database_v2, table))
            assert _stored(table)["executionStatus"] == "SUCCEEDED"
        assert [p["writer"] for p in _payloads(client)] == ["endState"]
        assert _payloads(client)[0]["systemExecutionContext"] == CONTEXT

    def test_a_lost_race_sends_nothing_and_keeps_the_other_writers_status(self):
        with _main_table(_row(status="ABORTED")) as table, _callback() as client:
            _end_state(_Dynamo(po.workflow_execution_database_v2, table))
            assert _stored(table)["executionStatus"] == "ABORTED"
        client.invoke.assert_not_called()

    def test_a_raising_callback_does_not_change_the_terminal_write(self):
        with _main_table(_row()) as table, _callback(invoke_error=RuntimeError("boom")):
            _end_state(_Dynamo(po.workflow_execution_database_v2, table), status="FAILED",
                       error="x failed")
            stored = _stored(table)
        assert stored["executionStatus"] == "FAILED"
        assert stored["executionError"] == "x failed"

    def test_a_manual_run_writes_exactly_what_it_wrote_before(self):
        with _main_table(_row(trigger="Manual")) as table, \
                patch.dict(os.environ, {sp.CALLBACK_REGISTRY_ENV: REGISTRY}), \
                patch.object(sp, "_default_lambda_client", side_effect=AssertionError("client")):
            _end_state(_Dynamo(po.workflow_execution_database_v2, table))
            stored = _stored(table)
        expected = dict(_row(trigger="Manual"), executionStatus="SUCCEEDED", executionLog="log")
        stored.pop("executionStopDate")
        expected.pop("executionStopDate")
        assert stored == expected


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


@pytest.mark.unit
@pytest.mark.aws
class TestWriter2ErrorHandler:
    def test_a_won_failure_sends_one_callback_with_the_error(self):
        with _main_table(_row()) as table, _callback() as client:
            assert _error_handler(table) == {"handled": True}
            assert _stored(table)["executionStatus"] == "FAILED"
        payloads = _payloads(client)
        assert [p["writer"] for p in payloads] == ["errorHandler"]
        assert payloads[0]["executionStatus"] == "FAILED"
        assert "step failed" in payloads[0]["executionError"]

    def test_an_already_terminal_row_sends_nothing(self):
        with _main_table(_row(status="SUCCEEDED")) as table, _callback() as client:
            _error_handler(table)
            assert _stored(table)["executionStatus"] == "SUCCEEDED"
        client.invoke.assert_not_called()

    def test_the_guard_losing_between_read_and_write_sends_nothing(self):
        # The handler reads RUNNING, then another writer finishes the row before its write lands.
        with _main_table(_row()) as table, _callback() as client:
            real_update = table.update_item

            def _finish_first(**kwargs):
                table.put_item(Item=_row(status="ABORTED"))
                return real_update(**kwargs)

            with patch.object(table, "update_item", side_effect=_finish_first):
                _error_handler(table)
            assert _stored(table)["executionStatus"] == "ABORTED"
        client.invoke.assert_not_called()

    def test_a_raising_callback_still_answers_handled_and_keeps_failed(self):
        with _main_table(_row()) as table, _callback(invoke_error=RuntimeError("boom")):
            assert _error_handler(table) == {"handled": True}
            assert _stored(table)["executionStatus"] == "FAILED"

    def test_a_missing_main_row_is_written_failed_without_a_finalize_error(self):
        # The guarded update creates the row; there is no trigger to send a callback for, and the
        # write that landed is not reported as a failure.
        with _main_table() as table, _callback() as client, \
                patch.object(heh.logger, "exception") as logged_exception:
            assert _error_handler(table) == {"handled": True}
            assert _stored(table)["executionStatus"] == "FAILED"
        client.invoke.assert_not_called()
        logged_exception.assert_not_called()


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


@pytest.mark.unit
@pytest.mark.aws
class TestWriter3And4Abort:
    def test_an_abort_sends_one_aborted_callback(self):
        with _main_table(_row()) as table, _callback() as client:
            with _abort(table, [_row()]) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert _stored(table)["executionStatus"] == "ABORTED"
        payloads = _payloads(client)
        assert [(p["writer"], p["executionStatus"]) for p in payloads] == [("abort", "ABORTED")]

    def test_an_abort_losing_to_the_end_state_sends_nothing(self):
        # The abort read RUNNING; the end state finished the row before the abort's write.
        with _main_table(_row(status="SUCCEEDED")) as table, _callback() as client:
            with _abort(table, [_row(status="RUNNING")]) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert _stored(table)["executionStatus"] == "SUCCEEDED"
        client.invoke.assert_not_called()

    def test_a_raising_callback_does_not_fail_the_abort(self):
        with _main_table(_row()) as table, _callback(invoke_error=RuntimeError("boom")):
            with _abort(table, [_row()]) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_a_group_abort_sends_one_callback_per_member(self):
        rows = [_row(EXEC), _row(EXEC2)]
        with _main_table(*rows) as table, _callback() as client:
            with _abort(table, rows) as drive:
                assert drive["group"]()["statusCode"] == 200
        assert sorted(p["workflowExecutionId"] for p in _payloads(client)) == sorted([EXEC, EXEC2])
        assert {p["writer"] for p in _payloads(client)} == {"abort"}

    def test_a_second_abort_of_the_same_run_sends_nothing_more(self):
        with _main_table(_row()) as table, _callback() as client:
            with _abort(table, [_row()]) as drive:
                drive["single"](EXEC)
            with _abort(table, [_stored(table)]) as drive:
                drive["single"](EXEC)
        assert client.invoke.call_count == 1

    def test_an_unconfirmed_stop_writes_aborted_but_holds_the_callback(self):
        with _main_table(_row()) as table, _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                response = drive["single"](EXEC)
            assert response["statusCode"] == 200
            stored = _stored(table)
        assert stored["executionStatus"] == "ABORTED"
        assert stored[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
        client.invoke.assert_not_called()

    def test_the_repeated_abort_that_confirms_the_stop_sends_the_held_callback_once(self):
        with _main_table(_row()) as table, _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            # Still unconfirmed: nothing yet.
            with _abort(table, [_stored(table)], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            client.invoke.assert_not_called()
            # Confirmed: the held callback goes out and the marker is cleared.
            with _abort(table, [_stored(table)]) as drive:
                drive["single"](EXEC)
            assert sp.CALLBACK_PENDING_ATTRIBUTE not in _stored(table)
            # Any further abort sends nothing more.
            with _abort(table, [_stored(table)]) as drive:
                drive["single"](EXEC)
        payloads = _payloads(client)
        assert [(p["writer"], p["executionStatus"]) for p in payloads] == [("abort", "ABORTED")]

    def test_two_concurrent_confirming_aborts_send_the_held_callback_once(self):
        # Both read the row while it still held the marker; only the first removal wins.
        with _main_table(_row()) as table, _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            held = _stored(table)
            with _abort(table, [held]) as drive:
                drive["single"](EXEC)
            with _abort(table, [held]) as drive:
                drive["single"](EXEC)
        assert client.invoke.call_count == 1

    def test_an_unconfirmed_stop_of_an_ordinary_run_writes_exactly_what_it_wrote_before(self):
        with _main_table(_row(trigger="Manual")) as table, _callback() as client:
            with _abort(table, [_row(trigger="Manual")], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            stored = _stored(table)
        assert stored["executionStatus"] == "ABORTED"
        assert sp.CALLBACK_PENDING_ATTRIBUTE not in stored
        client.invoke.assert_not_called()


@contextlib.contextmanager
def _stop_independent(*triggers):
    """Opt the given stored system triggers in to the unconfirmed-abort hand-over."""
    with patch.object(sp, "STOP_INDEPENDENT_CALLBACK_TRIGGERS", frozenset(triggers)):
        yield


@pytest.mark.unit
@pytest.mark.aws
class TestUnconfirmedAbortHandOver:
    """A trigger whose callback does not depend on the stop (STOP_INDEPENDENT_CALLBACK_TRIGGERS) gets
    the ABORTED status from an abort that could not confirm the stop, and the held callback still goes
    out once on confirmation. Every other trigger keeps the hold."""

    def test_no_trigger_opts_in_by_default(self):
        assert sp.STOP_INDEPENDENT_CALLBACK_TRIGGERS == frozenset()

    def test_an_opted_in_trigger_gets_the_status_at_once_and_the_hold_stays(self):
        with _stop_independent("Zip-Download"), _main_table(_row()) as table, \
                _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            stored = _stored(table)
        assert stored["executionStatus"] == "ABORTED"
        assert stored[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
        payloads = _payloads(client)
        assert [(p["writer"], p["executionStatus"]) for p in payloads] == [
            ("abortUnconfirmed", "ABORTED")]
        assert payloads[0]["systemExecutionContext"] == CONTEXT

    def test_the_confirming_abort_still_sends_the_held_callback_once_with_the_same_key(self):
        with _stop_independent("Zip-Download"), _main_table(_row()) as table, \
                _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            # A repeated unconfirmed abort lost nothing new: the row is already terminal.
            with _abort(table, [_stored(table)], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            with _abort(table, [_stored(table)]) as drive:
                drive["single"](EXEC)
            assert sp.CALLBACK_PENDING_ATTRIBUTE not in _stored(table)
        payloads = _payloads(client)
        assert [p["writer"] for p in payloads] == ["abortUnconfirmed", "abort"]
        assert len({p["idempotencyKey"] for p in payloads}) == 1

    def test_the_status_event_still_confirms_the_hold_after_the_hand_over(self):
        with _stop_independent("Zip-Download"), _main_table(_row()) as table, \
                _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            with _reconcile_env(table):
                le.lambda_handler(_status_event("ABORTED"), MagicMock())
            assert sp.CALLBACK_PENDING_ATTRIBUTE not in _stored(table)
        assert [p["writer"] for p in _payloads(client)] == ["abortUnconfirmed", "abort"]

    def test_a_confirmed_stop_sends_only_the_ordinary_callback(self):
        with _stop_independent("Zip-Download"), _main_table(_row()) as table, \
                _callback() as client:
            with _abort(table, [_row()]) as drive:
                drive["single"](EXEC)
        assert [p["writer"] for p in _payloads(client)] == ["abort"]

    def test_an_abort_losing_the_terminal_guard_hands_nothing_over(self):
        # The abort read RUNNING; the end state finished the row before the abort's write.
        with _stop_independent("Zip-Download"), _main_table(_row(status="SUCCEEDED")) as table, \
                _callback() as client:
            with _abort(table, [_row(status="RUNNING")], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            assert _stored(table)["executionStatus"] == "SUCCEEDED"
        client.invoke.assert_not_called()

    def test_a_raising_hand_over_does_not_fail_the_abort(self):
        with _stop_independent("Zip-Download"), _main_table(_row()) as table, \
                _callback(invoke_error=RuntimeError("boom")):
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200
            assert _stored(table)[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"

    def test_the_helper_ignores_triggers_that_did_not_opt_in(self):
        with _stop_independent("Zip-Download"), \
                patch.object(sp, "_default_lambda_client", side_effect=AssertionError("client")):
            assert sp.notify_unconfirmed_abort(_row(trigger="Manual"), "ABORTED") is False
            assert sp.notify_unconfirmed_abort(None, "ABORTED") is False
        with _stop_independent(), \
                patch.object(sp, "_default_lambda_client", side_effect=AssertionError("client")):
            assert sp.notify_unconfirmed_abort(_row(), "ABORTED") is False

    def test_an_opted_in_value_that_is_not_a_system_trigger_sends_nothing(self):
        with _stop_independent("Manual"), _callback() as client:
            assert sp.notify_unconfirmed_abort(_row(trigger="Manual"), "ABORTED") is False
        client.invoke.assert_not_called()


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
class TestWriter5Reconcile:
    def test_the_details_reconcile_of_a_run_stopped_outside_vams_sends_one_callback(self):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("TIMED_OUT")):
            le._reconcile_main_status(EXEC, _row())
            assert _stored(table)["executionStatus"] == "TIMED_OUT"
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("reconcileDetails", "TIMED_OUT")]

    def test_the_details_reconcile_losing_the_race_sends_nothing(self):
        with _main_table(_row(status="FAILED")) as table, _callback() as client, \
                _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
            le._reconcile_main_status(EXEC, _row())  # the read saw RUNNING
            assert _stored(table)["executionStatus"] == "FAILED"
        client.invoke.assert_not_called()

    def test_a_still_running_poll_sends_nothing(self):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value={"status": "RUNNING"}):
            le._reconcile_main_status(EXEC, _row())
        client.invoke.assert_not_called()

    def test_the_list_reconcile_sends_one_callback_through_build_execution_items(self):
        main = _row()
        with _main_table(main) as table, _callback() as client, _reconcile_env(table):
            le.build_execution_items(
                input_items=[{"workflowExecutionId": EXEC}],
                fetch_main_row=lambda eid: main,
                describe_execution=lambda arn: _stopped("ABORTED"),
                persist_main_row=lambda item: le._persist_list_reconcile(
                    table, item, lambda eid: main),
                workflow_id_filter="", workflow_database_id="")
            assert _stored(table)["executionStatus"] == "ABORTED"
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("reconcileList", "ABORTED")]

    def test_the_list_reconcile_losing_the_race_sends_nothing(self):
        main = _row()
        with _main_table(_row(status="SUCCEEDED")) as table, _callback() as client, \
                _reconcile_env(table):
            le._persist_list_reconcile(
                table, {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}",
                        "executionStatus": "ABORTED", "executionStopDate": "2026-11-05T02:00:00Z"},
                lambda eid: main)
            assert _stored(table)["executionStatus"] == "SUCCEEDED"
        client.invoke.assert_not_called()

    def test_a_sync_stamp_only_write_sends_nothing(self):
        main = _row()
        with _main_table(main) as table, _callback() as client:
            le._persist_list_reconcile(
                table, {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": f"{WF_DB}:{WF_ID}",
                        "lastSfnSyncCheckDate": "2026-11-05T02:00:00Z"}, lambda eid: main)
        client.invoke.assert_not_called()


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


@pytest.mark.unit
@pytest.mark.aws
class TestWriter6StatusEvent:
    """The push: the execution service handling a Step Functions execution status change event."""

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"])
    def test_each_terminal_status_reconciles_and_sends_one_callback(self, status):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped(status)):
            assert le.lambda_handler(_status_event(status), MagicMock()) == {
                "handled": True, "reconciled": True}
            assert _stored(table)["executionStatus"] == status
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("statusEvent", status)]

    def test_the_status_recorded_is_step_functions_own_not_the_events(self):
        with _main_table(_row()) as table, _callback(), _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("TIMED_OUT")):
            le.lambda_handler(_status_event("SUCCEEDED"), MagicMock())
            assert _stored(table)["executionStatus"] == "TIMED_OUT"

    def test_the_poll_interval_does_not_delay_the_push(self):
        recent = dict(_row(), lastSfnSyncCheckDate=le.er.iso_now())
        with _main_table(recent) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
            le.lambda_handler(_status_event(), MagicMock())
            assert _stored(table)["executionStatus"] == "ABORTED"
        assert client.invoke.call_count == 1

    def test_a_duplicate_event_sends_nothing_more(self):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")) as d:
            le.lambda_handler(_status_event(), MagicMock())
            assert le.lambda_handler(_status_event(), MagicMock()) == {
                "handled": True, "reconciled": False}
        assert client.invoke.call_count == 1
        assert d.call_count == 1

    def test_the_read_path_reconcile_winning_first_leaves_the_push_nothing_to_send(self):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
            le._reconcile_main_status(EXEC, _row())
            le.lambda_handler(_status_event(), MagicMock())
        assert [p["writer"] for p in _payloads(client)] == ["reconcileDetails"]

    def test_the_push_racing_a_read_between_its_read_and_write_sends_nothing(self):
        # The push read RUNNING; a read-path reconcile finished the row before the push's write.
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                patch(f"{ES}.get_execution_main_row", return_value=_row()):
            le._reconcile_main_status(EXEC, _row())
            le.lambda_handler(_status_event(), MagicMock())
            assert _stored(table)["executionStatus"] == "ABORTED"
        assert [p["writer"] for p in _payloads(client)] == ["reconcileDetails"]

    def test_a_run_finished_through_the_state_machine_costs_one_read(self):
        with _main_table(_row(status="SUCCEEDED")) as table, _callback() as client, \
                _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution") as describe:
            done = dict(_row(status="SUCCEEDED"), executionStopDate="2026-11-05T02:00:00Z")
            table.put_item(Item=done)
            le.lambda_handler(_status_event("SUCCEEDED"), MagicMock())
        describe.assert_not_called()
        client.invoke.assert_not_called()

    def test_a_missing_row_is_ignored(self):
        with _main_table() as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution") as describe:
            assert le.lambda_handler(_status_event(), MagicMock())["handled"] is False
        describe.assert_not_called()
        client.invoke.assert_not_called()

    def test_another_deployments_execution_with_a_different_arn_is_ignored(self):
        foreign = "arn:aws:states:us-east-1:1:execution:vams-other:" + EXEC
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution") as describe:
            assert le.lambda_handler(_status_event(execution_arn=foreign), MagicMock())[
                "handled"] is False
            assert _stored(table)["executionStatus"] == "RUNNING"
        describe.assert_not_called()
        client.invoke.assert_not_called()

    # The rule matches every VAMS deployment's workflow state machines in the account and Region, so
    # most events it delivers belong to another deployment. Each must end quietly: a raise would make
    # Lambda retry the event and spend this deployment's reads on executions it does not own (in-dev
    # D2, #1135). No Step Functions call, no write, no callback.
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
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution",
                             side_effect=AssertionError("describe")) as describe:
            result = le.lambda_handler(event(status), MagicMock())
            assert result["handled"] is False
            stored = _stored(table)
            assert stored["executionStatus"] == "RUNNING"
            assert not stored.get("executionStopDate")
        describe.assert_not_called()
        client.invoke.assert_not_called()

    @pytest.mark.parametrize("event", [
        {"source": "aws.states", "detail-type": "Step Functions Execution Status Change",
         "detail": {"name": "not-an-execution-id", "status": "ABORTED", "executionArn": "x"}},
        {"source": "aws.states", "detail-type": "Step Functions Execution Status Change",
         "detail": {"name": EXEC, "status": "RUNNING", "executionArn": "x"}},
    ])
    def test_a_non_vams_or_non_terminal_event_is_ignored_before_any_read(self, event):
        with patch(f"{ES}.get_execution_main_row", side_effect=AssertionError("read")):
            assert le.lambda_handler(event, MagicMock())["handled"] is False

    def test_an_ordinary_run_gets_the_same_finalization_and_no_client(self):
        with _main_table(_row(trigger="Manual")) as table, _reconcile_env(table), \
                patch.dict(os.environ, {sp.CALLBACK_REGISTRY_ENV: REGISTRY}), \
                patch.object(sp, "_default_lambda_client", side_effect=AssertionError("client")), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
            le.lambda_handler(_status_event(), MagicMock())
            assert _stored(table)["executionStatus"] == "ABORTED"

    def test_the_event_confirms_a_held_abort_and_sends_its_callback_once(self):
        with _main_table(_row()) as table, _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            client.invoke.assert_not_called()
            with _reconcile_env(table):
                le.lambda_handler(_status_event("ABORTED"), MagicMock())
                le.lambda_handler(_status_event("ABORTED"), MagicMock())
            assert sp.CALLBACK_PENDING_ATTRIBUTE not in _stored(table)
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("abort", "ABORTED")]

    def test_the_event_is_never_logged_and_never_reaches_the_api_dispatch(self):
        with _main_table(_row()) as table, _callback(), _reconcile_env(table), \
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


def _throttled(operation="DescribeExecution"):
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                       operation)


class _FailingRemoveTable:
    """The real moto table, except that the held-callback REMOVE fails with a non-conditional error."""

    def __init__(self, table):
        self._table = table

    def __getattr__(self, name):
        return getattr(self._table, name)

    def update_item(self, **kwargs):
        if kwargs.get("UpdateExpression", "").startswith("REMOVE"):
            raise _throttled("UpdateItem")
        return self._table.update_item(**kwargs)


@pytest.mark.unit
@pytest.mark.aws
class TestStatusEventRetriesOnFailure:
    """The push raises when it could not do its work, so Lambda's asynchronous retries deliver the
    event again; a read path stays best-effort; a lost guard is never a failure."""

    def test_a_throttled_describe_raises_and_the_retry_reconciles_once(self):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table):
            with patch.object(le.sfn, "describe_execution", side_effect=_throttled()):
                with pytest.raises(ClientError):
                    le.lambda_handler(_status_event(), MagicMock())
            assert _stored(table)["executionStatus"] == "RUNNING"
            client.invoke.assert_not_called()
            # Lambda's retry of the same event, now that Step Functions answers.
            with patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
                assert le.lambda_handler(_status_event(), MagicMock()) == {
                    "handled": True, "reconciled": True}
                # A further retry or duplicate finds the row terminal.
                assert le.lambda_handler(_status_event(), MagicMock()) == {
                    "handled": True, "reconciled": False}
            assert _stored(table)["executionStatus"] == "ABORTED"
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("statusEvent", "ABORTED")]

    def test_a_failed_guarded_write_raises(self):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                patch(f"{ES}._persist_reconciled_main_row", side_effect=_throttled("UpdateItem")):
            with pytest.raises(ClientError):
                le.lambda_handler(_status_event(), MagicMock())
        client.invoke.assert_not_called()

    def test_a_failed_main_row_read_raises(self):
        with patch(f"{ES}.get_execution_main_row", side_effect=_throttled("Query")):
            with pytest.raises(ClientError):
                le.lambda_handler(_status_event(), MagicMock())

    def test_a_lost_guard_returns_without_raising(self):
        # The push read RUNNING; another writer finished the row before the push's guarded write.
        with _main_table(_row(status="FAILED")) as table, _callback() as client, \
                _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                patch(f"{ES}.get_execution_main_row", return_value=_row()):
            assert le.lambda_handler(_status_event(), MagicMock()) == {
                "handled": True, "reconciled": True}
            assert _stored(table)["executionStatus"] == "FAILED"
        client.invoke.assert_not_called()

    def test_a_failed_held_callback_removal_raises_and_the_retry_sends_it_once(self):
        with _main_table(_row()) as table, _callback() as client:
            with _abort(table, [_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            failing = _FailingRemoveTable(table)
            with patch.object(le.dynamodb, "Table", return_value=failing):
                with pytest.raises(ClientError):
                    le.lambda_handler(_status_event("ABORTED"), MagicMock())
            client.invoke.assert_not_called()
            assert _stored(table)[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
            with _reconcile_env(table):
                le.lambda_handler(_status_event("ABORTED"), MagicMock())
                le.lambda_handler(_status_event("ABORTED"), MagicMock())
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("abort", "ABORTED")]

    def test_the_details_read_still_swallows_a_throttled_describe(self):
        with _main_table(_row()) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", side_effect=_throttled()):
            le._reconcile_main_status(EXEC, _row())
            assert _stored(table)["executionStatus"] == "RUNNING"
        client.invoke.assert_not_called()

    def test_a_read_path_held_callback_removal_failure_is_swallowed(self):
        held = dict(_row(status="ABORTED"), executionStopDate="2026-11-05T02:00:00Z",
                    **{sp.CALLBACK_PENDING_ATTRIBUTE: "ABORTED"})
        table = MagicMock()
        table.update_item.side_effect = _throttled("UpdateItem")
        with _callback() as client:
            le._send_held_abort_callback(table, held)
            with pytest.raises(ClientError):
                le._send_held_abort_callback(table, held, raise_on_failure=True)
        client.invoke.assert_not_called()


@pytest.mark.unit
@pytest.mark.aws
class TestDetailsReadSendsHeldAbort:
    """A held ABORTED callback does not depend on a second abort when the status event is lost: the
    details read sends it once Step Functions confirms the execution ended."""

    def _held(self, table, client):
        with _abort(table, [_row()], stop_confirmed=False) as drive:
            drive["single"](EXEC)
        client.invoke.assert_not_called()
        return _stored(table)

    def test_a_details_read_after_the_stop_sends_the_held_callback_once(self):
        with _main_table(_row()) as table, _callback() as client:
            held = self._held(table, client)
            with _reconcile_env(table), patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                    patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")):
                le._reconcile_main_status(EXEC, dict(held))
                assert sp.CALLBACK_PENDING_ATTRIBUTE not in _stored(table)
                # Later reads (they still hold the read-time copy) and the late event send nothing.
                le._reconcile_main_status(EXEC, dict(held))
                le.lambda_handler(_status_event("ABORTED"), MagicMock())
            assert _stored(table)["executionStatus"] == "ABORTED"
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("abort", "ABORTED")]

    def test_a_details_read_while_the_execution_still_runs_keeps_it_held(self):
        with _main_table(_row()) as table, _callback() as client:
            held = self._held(table, client)
            with _reconcile_env(table), patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                    patch.object(le.sfn, "describe_execution", return_value={"status": "RUNNING"}):
                le._reconcile_main_status(EXEC, dict(held))
            assert _stored(table)[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
        client.invoke.assert_not_called()

    def test_a_failed_describe_keeps_it_held_and_does_not_raise(self):
        with _main_table(_row()) as table, _callback() as client:
            held = self._held(table, client)
            with _reconcile_env(table), patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                    patch.object(le.sfn, "describe_execution", side_effect=_throttled()):
                le._reconcile_main_status(EXEC, dict(held))
            assert _stored(table)[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
        client.invoke.assert_not_called()

    def test_a_read_inside_the_poll_interval_does_not_describe(self):
        with _main_table(_row()) as table, _callback() as client:
            held = self._held(table, client)  # the abort stamped lastSfnSyncCheckDate just now
            with _reconcile_env(table), \
                    patch.object(le.sfn, "describe_execution") as describe:
                le._reconcile_main_status(EXEC, dict(held))
            describe.assert_not_called()
        client.invoke.assert_not_called()

    def test_a_terminal_row_without_a_held_callback_is_never_polled(self):
        done = dict(_row(status="SUCCEEDED"), executionStopDate="2026-11-05T02:00:00Z")
        with _main_table(done) as table, _callback() as client, _reconcile_env(table), \
                patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                patch.object(le.sfn, "describe_execution") as describe:
            le._reconcile_main_status(EXEC, dict(done))
        describe.assert_not_called()
        client.invoke.assert_not_called()

    def test_the_details_route_sends_it(self):
        with _main_table(_row()) as table, _callback() as client:
            held = self._held(table, client)
            with _reconcile_env(table), patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                    patch.object(le.sfn, "describe_execution", return_value=_stopped("ABORTED")), \
                    patch(f"{ES}.get_execution_main_row", return_value=dict(held)), \
                    patch(f"{ES}.get_workflow_execution_configuration_row", return_value={}), \
                    patch(f"{ES}.authorize_execution_access", return_value=(True, "")), \
                    patch(f"{ES}.assemble_execution_details", return_value={}), \
                    patch(f"{ES}._enforce_detail_payload_ceiling"):
                le.get_execution_details({"requestContext": {"http": {}}}, EXEC)
        assert [(p["writer"], p["executionStatus"]) for p in _payloads(client)] == [
            ("abort", "ABORTED")]


@pytest.mark.unit
class TestWriterReturnValues:
    """The won/lost signal each guarded write now returns (the callback keys on it)."""

    def test_finalize_main_row_reports_win_and_loss(self):
        table = MagicMock()
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        assert le.eo.finalize_main_row(dynamo, "t", EXEC, WF_DB, WF_ID, "FAILED", "now") is True
        table.update_item.side_effect = _ccf()
        assert le.eo.finalize_main_row(dynamo, "t", EXEC, WF_DB, WF_ID, "FAILED", "now") is False

    def test_persist_reconciled_main_row_reports_win_loss_and_nothing_to_write(self):
        table = MagicMock()
        assert le._persist_reconciled_main_row(table, {}, ("executionStatus",)) is False
        item = dict(_row(), executionStatus="ABORTED")
        assert le._persist_reconciled_main_row(
            table, item, ("executionStatus",), only_if_not_terminal=True) is True
        table.update_item.side_effect = _ccf()
        assert le._persist_reconciled_main_row(
            table, item, ("executionStatus",), only_if_not_terminal=True) is False


# ---------------------------------------------------------------------------------------------------
# 4) Re-run of a system run is refused
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

    def test_a_system_run_is_refused_400_and_launches_nothing(self):
        response, invoke = self._rerun(_row())
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
        ("manual", "Manual"), ("fileUpload", "File-Upload"), ("zipDownload", "Zip-Download"),
        ("Zip-Download", "Zip-Download"), ("Other", "Other"),
    ])
    def test_request_spellings_map_to_stored_values(self, value, stored):
        assert le._stored_trigger_type(value) == stored

    def test_the_stored_record_model_accepts_the_system_value(self):
        from models.executions import WorkflowExecutionRecord
        record = WorkflowExecutionRecord(workflowExecutionId=EXEC, workflowId=WF_ID,
                                         workflowDatabaseId=WF_DB, triggerType="Zip-Download")
        assert record.triggerType == "Zip-Download"
