# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip mode 1 (a zip download job), end to end across its cross-calls, for the GLOBAL
zipArchive system workflow (#1125; the #1123 D2 lesson).

The zipArchive system workflow and pipeline are registered under the GLOBAL keyword
(GLOBAL/utility-zip-archive). The other zip tests stub each cross-call with a Lambda double that
answers whatever the test chose, so none of them sees the body the zip service actually sends being
parsed by the handler that actually receives it. #1123 D2 slipped through review that way: a model
refused GLOBAL and every GLOBAL workflow's write-back failed after the upgrade.

Here the zip service's REAL cross-calls are served by the REAL receiving handlers:

1. Launch: the zip service's execute body -> executeWorkflow.lambda_handler (path validation with the
   GLOBAL keyword, the request model, the system-trigger contract), and the execute response back
   into the zip service.
2. Abort (the user's cancel, the executions view): the zip service's abort body ->
   executionService.lambda_handler (route, executionId validation, Tier-1, the guarded main-row write
   on the GLOBAL sort key) -> the system-pipeline terminal callback -> the REAL zip download callback.
3. Run check (lost status change event backstop): the zip service's details body ->
   executionService.lambda_handler details route -> the read-path reconcile of the GLOBAL main row ->
   the terminal callback -> the zip download callback.

Identity, Casbin and the Step Functions / S3 calls are stubbed; routing, validation, models and the
guarded writes are real. Mode 2 (archive into the asset) is in
tests/handlers/workflows/test_zip_archive_write_back_end_to_end.py."""

import io
import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.workflows import test_system_pipeline_hooks as hooks
from backend.tests.handlers.zipDownloads import zipBundle as zb
from backend.tests.handlers.zipDownloads import zipHarness as h

ewv2, le, sp = hooks.ewv2, hooks.le, hooks.sp
EW, ES = hooks.EW, hooks.ES
tew = hooks._harness()

GLOBAL = "GLOBAL"
WORKFLOW_ID = "utility-zip-archive"
SORT_KEY = f"{GLOBAL}:{WORKFLOW_ID}"
CALLBACK_FN = "vams-zip-callback"

# The zipArchive workflow and pipeline AS THE DEPLOYMENT REGISTERS THEM: the shipped bundle through
# the real importer and create request models (zipBundle). Before #1125 D9 these were written here by
# hand with the systemConfig the zip service needs, so this test passed while the shipped bundle's
# workflow declared none and every folder, whole-asset and multi-file job was refused live.
ZIP_PIPELINE, ZIP_WORKFLOW = zb.registered_records()
ASSET = {"databaseId": "db1", "assetId": "asset1", "assetName": "Engine", "bucketId": "b1",
         "assetLocation": {"Key": "xasset1/"}}

SELECTIONS = {
    "file": {"selection": [{"type": "file", "relativePath": "/a.glb"}]},
    "files": {"selection": [{"type": "file", "relativePath": "/a.glb"},
                            {"type": "file", "relativePath": "/textures/t1.png"}]},
    "folder": {"selection": [{"type": "folder", "relativePath": "/textures/"}]},
    "file-and-folder": {"selection": [{"type": "file", "relativePath": "/a.glb"},
                                      {"type": "folder", "relativePath": "/textures/"}]},
    "whole-asset": {"selection": [{"type": "asset", "relativePath": "/"}]},
    "pinned-version": {"selection": [{"type": "file", "relativePath": "/a.glb", "versionId": "va1"}]},
}


class _ExecuteLambda:
    """The zip service's Lambda client: the execute invoke runs the REAL executeWorkflow handler.
    Records each event, the handler's response, and the main execution row the launch wrote."""

    def __init__(self, workflow=None):
        self.events, self.responses, self.main_rows = [], [], []
        self.workflow = workflow or ZIP_WORKFLOW

    def invoke(self, FunctionName, InvocationType, Payload):  # noqa: N803 - mirrors boto3
        assert FunctionName == "executeFn" and InvocationType == "RequestResponse"
        event = json.loads(Payload.decode("utf-8"))
        self.events.append(event)
        p = tew.TestExecuteOrchestration()._patches(workflow=self.workflow, pipeline=ZIP_PIPELINE)
        tables = {}
        with ExitStack() as stack:
            for name in ("get_workflow", "get_pipeline", "default_bucket", "asset_bucket", "exists",
                         "enforcer"):
                stack.enter_context(p[name])
            stack.enter_context(patch(f"{EW}._get_asset", return_value=dict(ASSET)))
            stack.enter_context(patch(f"{EW}.request_to_claims",
                                      return_value={"tokens": ["SYSTEM_USER"]}))
            stack.enter_context(patch(f"{EW}.s3c"))
            sfn = stack.enter_context(patch(f"{EW}.sfn_client"))
            sfn.start_execution.return_value = {"executionArn": "arn:aws:states:us-east-1:1:execution:wf:x"}
            dynamo = stack.enter_context(patch(f"{EW}.dynamodb"))
            dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            response = ewv2.lambda_handler(json.loads(json.dumps(event)), MagicMock())
        self.responses.append(response)
        main = tables.get(ewv2.workflow_execution_database_v2)
        if main is not None:
            self.main_rows.extend(c.kwargs["Item"] for c in main.put_item.call_args_list)
        return {"Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    e.s3.add("xasset1/textures/t1.png", size=20, version="vt1", content_type="image/png")
    e.lam = _ExecuteLambda()
    e.svc.lambda_client = e.lam
    return e


def _create(env, body):
    h.AUTH.user = "alice"
    return env.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=body), None)


def _launched(env, body):
    """Create a job through the zip route; returns (job row, the run's main row)."""
    response = _create(env, body)
    assert response["statusCode"] == 200, response["body"]
    assert len(env.lam.responses) == 1
    assert env.lam.responses[0]["statusCode"] == 200, env.lam.responses[0]["body"]
    row = next(iter(env.zip.items.values()))
    return row, env.lam.main_rows[0]


# ------------------------------------------------------------------------------------------------ #
# 1) Launch
# ------------------------------------------------------------------------------------------------ #

@pytest.mark.unit
@pytest.mark.parametrize("selection", list(SELECTIONS))
class TestLaunchThroughTheExecuteHandler:
    def test_the_execute_handler_accepts_the_launch_and_the_job_records_its_run(self, env, selection):
        row, main = _launched(env, SELECTIONS[selection])
        assert env.lam.events[0]["pathParameters"] == {"workflowDatabaseId": GLOBAL,
                                                       "workflowId": WORKFLOW_ID}
        launched = json.loads(env.lam.responses[0]["body"])["message"]["executionId"]
        # The job row records the run the execute handler started (before: the zip service read the
        # execution id from the top level of the response, never found it, and failed every job).
        assert row["workflowExecutionId"] == launched == main["workflowExecutionId"]
        assert row["status"] == "QUEUED"

    def test_the_run_is_a_global_system_run_the_zip_service_finds_again(self, env, selection):
        row, main = _launched(env, SELECTIONS[selection])
        assert main["workflowDatabaseId"] == GLOBAL and main["workflowId"] == WORKFLOW_ID
        assert main["workflowDatabaseId:workflowId"] == SORT_KEY
        assert main["triggerType"] == "Zip-Download"
        assert main["isSystem"] is True
        context = json.loads(main["systemExecutionContext"])
        assert context["requestId"] == row["requestId"] and context["databaseId"] == "db1"
        # The zip service reads the run's main row by the same GLOBAL sort key (_main_row).
        env.executions.items[(main["workflowExecutionId"], main["workflowDatabaseId:workflowId"])] = main
        assert env.svc._main_row(main["workflowExecutionId"])["triggerType"] == "Zip-Download"


@pytest.mark.unit
def test_a_launch_the_execute_handler_refuses_answers_400_and_leaves_no_job(env):
    """#1125 D9: a refusal is a clean 4xx with a generic reason, and the refused job leaves no row or
    working objects behind (before: 500 and a FAILED row)."""
    env.lam.workflow = dict(ZIP_WORKFLOW, enabled=False)
    response = _create(env, SELECTIONS["file"])
    assert 400 <= env.lam.responses[0]["statusCode"] < 500
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["message"] == env.zd.MSG_LAUNCH_REFUSED
    assert env.zip.items == {}
    assert not [k for (b, k) in env.s3.objects if b == h.AUX]


@pytest.mark.unit
def test_a_selection_the_registered_workflow_refuses_answers_400(env):
    """The REAL validation refusal (executionValidationErrors) maps to the same clean 400: the
    registered workflow with its scope narrowed, as the importer stored it before D9."""
    narrowed = dict(ZIP_WORKFLOW, systemConfig=dict(ZIP_WORKFLOW["systemConfig"],
                                                    assetScope=dict(ZIP_WORKFLOW["systemConfig"]["assetScope"],
                                                                    folderAllowed=False)))
    env.lam.workflow = narrowed
    response = _create(env, SELECTIONS["folder"])
    refusal = json.loads(env.lam.responses[0]["body"])["message"]
    assert "executionValidationErrors" in json.dumps(refusal)
    assert response["statusCode"] == 400 and env.zip.items == {}


# ------------------------------------------------------------------------------------------------ #
# 2) and 3) Abort and run check through the execution service, callback into the zip callback
# ------------------------------------------------------------------------------------------------ #

class _ExecutionServiceLambda:
    """The zip service's Lambda client for the execution service: runs the REAL handler."""

    def __init__(self):
        self.responses = []

    def invoke(self, FunctionName, InvocationType, Payload):  # noqa: N803 - mirrors boto3
        assert FunctionName == "executionServiceFn" and InvocationType == "RequestResponse"
        event = json.loads(Payload.decode("utf-8"))
        response = le.lambda_handler(json.loads(json.dumps(event)), MagicMock())
        self.responses.append(response)
        return {"Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}


class _CallbackLambda:
    """The terminal callback's Lambda client: an asynchronous invoke runs the REAL zip callback."""

    def __init__(self, env):
        self.env, self.payloads = env, []

    def invoke(self, FunctionName, InvocationType, Payload):  # noqa: N803 - mirrors boto3
        assert FunctionName == CALLBACK_FN and InvocationType == "Event"
        payload = json.loads(Payload.decode("utf-8"))
        self.payloads.append(payload)
        self.env.cb.lambda_handler(payload, None)
        return {"StatusCode": 202}


def _running(env):
    """A launched job whose run is RUNNING, with its main row in a moto table."""
    row, main = _launched(env, SELECTIONS["folder"])
    row["status"] = "RUNNING"
    main = dict(main, executionStatus="RUNNING", executionStopDate="")
    return row, main


def _execution_service(stack, env, table):
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    stack.enter_context(patch(f"{ES}.request_to_claims", return_value={"tokens": ["SYSTEM_USER"],
                                                                       "roles": []}))
    stack.enter_context(patch(f"{ES}.CasbinEnforcer", enforcer))
    stack.enter_context(patch.object(le.dynamodb, "Table", side_effect=lambda name: table
                                     if name == le.workflow_execution_database_v2 else MagicMock()))
    stack.enter_context(patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: json.dumps(
        {"Zip-Download": CALLBACK_FN})}))
    callbacks = _CallbackLambda(env)
    stack.enter_context(patch.object(sp, "_default_lambda_client", return_value=callbacks))
    service = _ExecutionServiceLambda()
    env.svc.lambda_client = service
    return service, callbacks


def _stored(table, execution_id):
    return table.get_item(Key={"workflowExecutionId": execution_id,
                               "workflowDatabaseId:workflowId": SORT_KEY})["Item"]


@pytest.mark.unit
@pytest.mark.aws
class TestAbortThroughTheExecutionService:
    def test_the_abort_route_aborts_the_global_run_and_the_callback_finalizes_the_job(self, env):
        row, main = _running(env)
        execution_id = main["workflowExecutionId"]
        with hooks._main_table(main) as table, ExitStack() as stack:
            service, callbacks = _execution_service(stack, env, table)
            stack.enter_context(patch(f"{ES}.authorize_abort", return_value=(True, "")))
            stack.enter_context(patch(f"{ES}.get_pipeline_execution_rows", return_value=[]))
            stack.enter_context(patch.object(le.sfn, "stop_execution", return_value={}))
            stack.enter_context(patch.object(le.el, "sfn_execution_has_ended", return_value=False))
            stack.enter_context(patch.object(le.el, "release_locks_for_execution"))
            stack.enter_context(patch(f"{ES}.log_actions"))
            assert env.svc._abort_execution(execution_id) is True
            assert service.responses[0]["statusCode"] == 200, service.responses[0]["body"]
            assert _stored(table, execution_id)["executionStatus"] == "ABORTED"
        assert [p["workflowDatabaseId"] for p in callbacks.payloads] == [GLOBAL]
        job = env.zip.items[(row["userId"], row["requestId"])]
        assert job["status"] == "FAILED"
        assert job["errorMessage"] == "Cancelled from workflow executions"


@pytest.mark.unit
@pytest.mark.aws
class TestRunCheckThroughTheDetailsRoute:
    def test_the_details_read_reconciles_the_global_run_and_the_callback_finalizes_the_job(self, env):
        row, main = _running(env)
        execution_id = main["workflowExecutionId"]
        with hooks._main_table(main) as table, ExitStack() as stack:
            service, callbacks = _execution_service(stack, env, table)
            stack.enter_context(patch(f"{ES}.get_workflow_execution_configuration_row", return_value={}))
            stack.enter_context(patch(f"{ES}.authorize_execution_access", return_value=(True, "")))
            stack.enter_context(patch(f"{ES}.assemble_execution_details",
                                      return_value={"workflowExecutionId": execution_id}))
            stack.enter_context(patch(f"{ES}._settle_reconciled_terminal"))
            stack.enter_context(patch(f"{ES}._terminal_log_and_error", return_value=("", "")))
            stack.enter_context(patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0))
            stack.enter_context(patch.object(le.sfn, "describe_execution",
                                             return_value=hooks._stopped("TIMED_OUT")))
            assert env.svc._check_run_ended(execution_id) is True
            assert service.responses[0]["statusCode"] == 200, service.responses[0]["body"]
            assert _stored(table, execution_id)["executionStatus"] == "TIMED_OUT"
        assert [(p["workflowDatabaseId"], p["writer"]) for p in callbacks.payloads] == [
            (GLOBAL, "reconcileDetails")]
        job = env.zip.items[(row["userId"], row["requestId"])]
        assert job["status"] == "FAILED" and job["errorMessage"] == "The archive job timed out"
