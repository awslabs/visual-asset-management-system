# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Handler behaviour for the GenAI CAD STEP agent pipeline Lambdas."""

import datetime
import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOKEN = "AQCEAAAAKgAAAAMAAAAAAAAAA-cad-step-agent-test-token"

_ENV = {
    "AWS_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "OPEN_PIPELINE_FUNCTION_NAME": "openPipeline-cad",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:111111111111:stateMachine:cad",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".stp,.step",
    "AGENT_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/cad_step_agent-abc",
    "WARM_SESSION_SLOTS": "0",
    "BATCH_JOB_QUEUE": "queue",
    "BATCH_JOB_DEFINITION": "jobdef",
    "MAX_RUN_SECONDS": "3600",
    "ALLOW_INTERNET_RESEARCH": "true",
    "OPENAI_ENABLED": "false",
}


def _load(module_file, env_overrides=None):
    """Load a handler module by path under a suite-private name, with the env it reads at import."""
    env = dict(_ENV)
    env.update(env_overrides or {})
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.modules.pop("manifestHelper", None)
    sys.modules.pop("cad_step_naming", None)
    sys.path.insert(0, _LAMBDA_DIR)
    try:
        name = f"cad_step_agent_{module_file[:-3]}_undertest"
        spec = importlib.util.spec_from_file_location(name, os.path.join(_LAMBDA_DIR, module_file))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(_LAMBDA_DIR)
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _resolved(with_input=True):
    return {
        "manifestUsed": True,
        "inputFiles": [{"fileKey": "/part.stp"}] if with_input else [],
        "inputS3AssetFilePath": "s3://abkt/xasset1/part.stp" if with_input else "",
        "outputS3AssetFilesPath": "s3://abkt/xasset1/",
        "outputS3AssetPreviewPath": "s3://abkt/xasset1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/xasset1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xasset1/pipeline/",
        "assetId": "xasset1",
        "databaseId": "db1",
        "inputMetadataS3Location": "s3://run/meta.json",
        "inputConfigurationS3Location": "s3://run/config.json",
        "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1",
    }


# ---------------------------------------------------------------------------------------------------
# vamsExecute
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestVamsExecute:
    def _run(self, mod, resolved, invoke_response):
        send_failure = MagicMock()
        invoke = MagicMock(return_value=invoke_response)
        with patch.object(mod.manifestHelper, "resolve_pipeline_inputs", return_value=resolved), \
                patch.object(mod.lambda_client, "invoke", invoke), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps({"TaskToken": _TOKEN})}, MagicMock())
        return resp, invoke, send_failure

    def test_forwards_every_path_and_the_asset_identity(self):
        mod = _load("vamsExecuteCadStepAgentPipeline.py")
        resp, invoke, send_failure = self._run(mod, _resolved(), {"StatusCode": 200})
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"])
        for key in ("inputS3AssetFilePath", "outputS3AssetFilesPath", "outputS3AssetPreviewPath",
                    "outputS3AssetMetadataPath", "inputOutputS3AssetAuxiliaryFilesPath"):
            assert payload[key] == _resolved()[key], key
        assert payload["assetId"] == "xasset1" and payload["databaseId"] == "db1"
        assert payload["sfnExternalTaskToken"] == _TOKEN
        send_failure.assert_not_called()

    def test_a_generate_run_forwards_an_empty_input_path(self):
        mod = _load("vamsExecuteCadStepAgentPipeline.py")
        resp, invoke, _ = self._run(mod, _resolved(with_input=False), {"StatusCode": 200})
        assert resp["statusCode"] == 200
        assert json.loads(invoke.call_args.kwargs["Payload"])["inputS3AssetFilePath"] == ""

    def test_function_error_from_open_pipeline_fails_the_token(self):
        mod = _load("vamsExecuteCadStepAgentPipeline.py")
        resp, _, send_failure = self._run(
            mod, _resolved(), {"StatusCode": 200, "FunctionError": "Unhandled"})
        assert resp["statusCode"] == 500
        assert send_failure.call_count == 1
        assert send_failure.call_args.kwargs["taskToken"] == _TOKEN
        assert len(send_failure.call_args.kwargs["cause"]) <= 256

    def test_two_input_files_are_refused_before_invoking(self):
        mod = _load("vamsExecuteCadStepAgentPipeline.py")
        resolved = _resolved()
        resolved["inputFiles"] = [{"fileKey": "/a.stp"}, {"fileKey": "/b.stp"}]
        resp, invoke, send_failure = self._run(mod, resolved, {"StatusCode": 200})
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        send_failure.assert_called_once()

    def test_missing_task_token_is_a_500_without_a_callback(self):
        mod = _load("vamsExecuteCadStepAgentPipeline.py")
        send_failure = MagicMock()
        with patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps({"no": "token"})}, MagicMock())
        assert resp["statusCode"] == 500
        send_failure.assert_not_called()


# ---------------------------------------------------------------------------------------------------
# openPipeline
# ---------------------------------------------------------------------------------------------------
def _open_event(file_uri):
    return {
        "inputS3AssetFilePath": file_uri,
        "outputS3AssetFilesPath": "s3://abkt/xasset1/",
        "outputS3AssetPreviewPath": "s3://abkt/xasset1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/xasset1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xasset1/pipeline/",
        "inputMetadataS3Location": "s3://run/meta.json",
        "inputConfigurationS3Location": "s3://run/config.json",
        "assetId": "xasset1",
        "databaseId": "db1",
        "sfnExternalTaskToken": _TOKEN,
        "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1",
    }


@pytest.mark.unit
class TestOpenPipeline:
    def _invoke(self, mod, file_uri):
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:cad:CadStepAgent_x",
            "startDate": datetime.datetime(2026, 1, 1),
        })
        send_failure = MagicMock()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_failure", send_failure), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(_open_event(file_uri), MagicMock())
        return resp, start, send_failure

    def test_a_generate_run_with_no_input_is_not_gated(self):
        mod = _load("openPipeline.py")
        resp, start, send_failure = self._invoke(mod, "")
        assert resp["statusCode"] == 200
        start.assert_called_once()
        send_failure.assert_not_called()
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["inputS3AssetFilePath"] == ""
        assert sfn_input["jobName"].startswith("CadStepAgent_")

    def test_the_sub_execution_is_registered_with_the_state_machine_log_source(self):
        mod = _load("openPipeline.py", {
            "ORCHESTRATION_BUS_NAME": "bus",
            "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/VAMSStateMachine-CadStepAgentX",
            "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:111111111111:log-group:/aws/vendedlogs/VAMSStateMachine-CadStepAgentX"})
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:cad:CadStepAgent_x",
            "startDate": datetime.datetime(2026, 1, 1),
        })
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(_open_event("s3://abkt/xasset1/part.stp"), MagicMock())
        detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert detail["subExecution"]["label"] == "CAD STEP agent processing"
        assert detail["logs"][0]["sourceType"] == "stateMachine"
        assert detail["logs"][0]["label"] == "CAD STEP agent state machine"

    @pytest.mark.parametrize("ext", [".stp", ".STEP"])
    def test_step_inputs_pass_the_gate(self, ext):
        mod = _load("openPipeline.py")
        resp, start, _ = self._invoke(mod, f"s3://abkt/xasset1/part{ext}")
        assert resp["statusCode"] == 200
        start.assert_called_once()

    @pytest.mark.parametrize("ext", [".st", ".ste", ".stl", ".glb", ""])
    def test_non_step_inputs_are_rejected_and_the_token_failed(self, ext):
        mod = _load("openPipeline.py")
        resp, start, send_failure = self._invoke(mod, f"s3://abkt/xasset1/part{ext}")
        assert resp["statusCode"] == 400
        start.assert_not_called()
        assert send_failure.call_args.kwargs["taskToken"] == _TOKEN

    def test_job_names_are_unique_across_back_to_back_runs(self):
        mod = _load("openPipeline.py")
        names = set()
        for _ in range(5):
            _, start, _ = self._invoke(mod, "s3://abkt/xasset1/part.stp")
            names.add(start.call_args.kwargs["name"])
        assert len(names) == 5
        assert all(len(n) <= 80 and ":" not in n and "/" not in n for n in names)


# ---------------------------------------------------------------------------------------------------
# constructPipeline
# ---------------------------------------------------------------------------------------------------
def _construct_event(with_input=True):
    event = {
        "jobName": "CadStepAgent_20260922_185500_123_ab12cd34",
        "inputS3AssetFilePath": "s3://abkt/xasset1/sub/dir/part.stp" if with_input else "",
        "outputS3AssetFilesPath": "s3://abkt/xasset1/",
        "outputS3AssetMetadataPath": "s3://abkt/xasset1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xasset1/pipeline/",
        "inputConfigurationS3Location": "s3://run/config.json",
        "assetId": "xasset1",
        "databaseId": "db1",
        "externalSfnTaskToken": _TOKEN,
        "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1",
    }
    return event


def _config(mode="modify", **overrides):
    cfg = {"mode": mode, "prompt": "Add four M3 holes", "outputFilename": "", "outputFilenamePrefix": "",
           "allowInternetResearch": True, "modelProvider": "bedrock", "modelId": "", "maxAttempts": 4}
    cfg.update(overrides)
    return cfg


@pytest.mark.unit
class TestConstructPipeline:
    def _run(self, mod, event, config):
        send_failure = MagicMock()
        with patch.object(mod.manifestHelper, "fetch_input_configuration", return_value=config), \
                patch.object(mod.sfn, "send_task_failure", send_failure):
            try:
                return mod.lambda_handler(event, MagicMock()), send_failure, None
            except Exception as exc:  # the handler re-raises after reporting the token
                return None, send_failure, exc

    def test_modify_definition_keeps_the_input_name_and_relative_subdir(self):
        mod = _load("constructPipeline.py")
        resp, send_failure, exc = self._run(mod, _construct_event(), _config())
        assert exc is None and send_failure.call_count == 0
        definition = json.loads(resp["definition"][0])
        assert definition["mode"] == "modify"
        assert definition["inputFile"]["objectKey"] == "xasset1/sub/dir/part.stp"
        assert definition["outputFiles"] == {
            "bucketName": "abkt", "objectDir": "xasset1/", "relativeSubdir": "sub/dir/", "fileName": "part.stp"}
        assert definition["outputMetadata"] == {"bucketName": "abkt", "objectDir": "xasset1/metadata/"}
        assert definition["agent"]["maxRunSeconds"] == 3600
        assert resp["externalSfnTaskToken"] == _TOKEN
        assert resp["orchestrationEventPrefix"] == "vams.test.execution.E1.pipeline.P1"

    def test_the_outer_token_stays_out_of_the_container_bound_definition(self):
        mod = _load("constructPipeline.py")
        resp, _, exc = self._run(mod, _construct_event(), _config())
        assert exc is None
        assert _TOKEN not in resp["definition"][0]
        assert "externalSfnTaskToken" not in json.loads(resp["definition"][0])

    def test_generate_definition_has_no_input_and_a_generated_name(self):
        mod = _load("constructPipeline.py")
        resp, _, exc = self._run(mod, _construct_event(with_input=False),
                                 _config("generate", designName="Jetson Nano board"))
        assert exc is None
        definition = json.loads(resp["definition"][0])
        assert definition["inputFile"] is None
        assert definition["outputFiles"]["fileName"].startswith("jetson-nano-board-")
        assert definition["outputFiles"]["fileName"].endswith(".step")
        assert definition["outputFiles"]["relativeSubdir"] == ""

    def test_prefix_and_override_stack(self):
        mod = _load("constructPipeline.py")
        resp, _, exc = self._run(mod, _construct_event(),
                                 _config(outputFilename="mount.step", outputFilenamePrefix="v2-"))
        assert exc is None
        assert json.loads(resp["definition"][0])["outputFiles"]["fileName"] == "v2-mount.stp"

    def test_a_bad_override_is_a_pre_invoke_rejection(self):
        mod = _load("constructPipeline.py")
        resp, send_failure, exc = self._run(mod, _construct_event(), _config(outputFilename="bad<name>"))
        assert resp is None and exc is not None
        assert send_failure.call_args.kwargs["taskToken"] == _TOKEN
        assert send_failure.call_args.kwargs["error"] == "CadStepAgentPipelineError"

    def test_modify_without_an_input_file_is_refused(self):
        mod = _load("constructPipeline.py")
        _, send_failure, exc = self._run(mod, _construct_event(with_input=False), _config("modify"))
        assert exc is not None and send_failure.call_count == 1

    def test_generate_with_an_input_file_is_refused(self):
        mod = _load("constructPipeline.py")
        _, send_failure, exc = self._run(mod, _construct_event(), _config("generate"))
        assert exc is not None and send_failure.call_count == 1

    def test_empty_prompt_is_refused(self):
        mod = _load("constructPipeline.py")
        _, send_failure, exc = self._run(mod, _construct_event(), _config(prompt="  "))
        assert exc is not None and send_failure.call_count == 1

    def test_openai_is_refused_when_the_deployment_did_not_configure_it(self):
        mod = _load("constructPipeline.py")
        _, send_failure, exc = self._run(mod, _construct_event(), _config(modelProvider="openai"))
        assert exc is not None and "not configured" in str(exc)
        mod2 = _load("constructPipeline.py", {"OPENAI_ENABLED": "true"})
        resp, _, exc2 = self._run(mod2, _construct_event(), _config(modelProvider="openai", modelId="gpt-x"))
        assert exc2 is None
        assert json.loads(resp["definition"][0])["agent"] == {
            "prompt": "Add four M3 holes", "allowInternetResearch": True, "modelProvider": "openai",
            "modelId": "gpt-x", "maxAttempts": 4, "maxRunSeconds": 3600, "scriptTimeoutSeconds": 300}

    def test_unknown_provider_and_mode_are_refused(self):
        mod = _load("constructPipeline.py")
        for cfg in (_config(modelProvider="anthropic-direct"), _config(mode="transmute")):
            _, send_failure, exc = self._run(mod, _construct_event(), cfg)
            assert exc is not None and send_failure.call_count == 1

    def test_deployment_master_switch_wins_over_the_run_tag(self):
        mod = _load("constructPipeline.py", {"ALLOW_INTERNET_RESEARCH": "false"})
        resp, _, exc = self._run(mod, _construct_event(), _config(allowInternetResearch=True))
        assert exc is None
        assert json.loads(resp["definition"][0])["agent"]["allowInternetResearch"] is False

    def test_max_attempts_is_clamped(self):
        mod = _load("constructPipeline.py")
        resp, _, _ = self._run(mod, _construct_event(), _config(maxAttempts=99))
        assert json.loads(resp["definition"][0])["agent"]["maxAttempts"] == 10
        resp, _, _ = self._run(mod, _construct_event(), _config(maxAttempts="0"))
        assert json.loads(resp["definition"][0])["agent"]["maxAttempts"] == 1


# ---------------------------------------------------------------------------------------------------
# executeBatchJob
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestExecuteBatchJob:
    def _event(self):
        return {"jobName": "CadStepAgent_x", "definition": [json.dumps({"mode": "modify", "agent": {}})],
                "taskToken": _TOKEN, "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1"}

    def test_the_definition_travels_in_the_environment_not_the_command_line(self):
        mod = _load("executeBatchJob.py", {"ORCHESTRATION_BUS_NAME": "bus",
                                          "BATCH_JOB_LOG_GROUP_NAME": "/aws/vendedlogs/Pipelines/CadStepAgentX",
                                          "BATCH_JOB_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:111111111111:log-group:/aws/vendedlogs/Pipelines/CadStepAgentX"})
        submit = MagicMock(return_value={"jobId": "job-1"})
        put_events = MagicMock()
        with patch.object(mod.batch, "submit_job", submit), patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp == {"jobId": "job-1", "jobName": "CadStepAgent_x", "status": "SUBMITTED"}
        overrides = submit.call_args.kwargs["containerOverrides"]
        assert overrides["command"] == ["python3", "-m", "cad_step_agent.batch_main"]
        env = {e["name"]: e["value"] for e in overrides["environment"]}
        assert json.loads(env["CAD_AGENT_DEFINITION"]) == {"mode": "modify", "agent": {}}
        assert env["TASK_TOKEN"] == _TOKEN
        assert "mode" not in " ".join(overrides["command"])

    def test_the_job_is_registered_with_its_container_log_source(self):
        mod = _load("executeBatchJob.py", {"ORCHESTRATION_BUS_NAME": "bus",
                                          "BATCH_JOB_LOG_GROUP_NAME": "/aws/vendedlogs/Pipelines/CadStepAgentX",
                                          "BATCH_JOB_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:111111111111:log-group:/aws/vendedlogs/Pipelines/CadStepAgentX"})
        put_events = MagicMock()
        with patch.object(mod.batch, "submit_job", MagicMock(return_value={"jobId": "job-1"})), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), MagicMock())
        detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert detail["subExecution"]["jobId"] == "job-1"
        assert detail["subExecution"]["stageName"] == "CadStepAgentBatchJob"
        assert detail["logs"][0]["logStreamPrefix"] == "jobdef/default/"
        assert detail["logs"][0]["sourceType"] == "batch"


# ---------------------------------------------------------------------------------------------------
# invokeAgentRuntime
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestInvokeAgentRuntime:
    def _event(self):
        return {"jobName": "CadStepAgent_x", "definition": [json.dumps({"mode": "modify"})], "taskToken": _TOKEN}

    def _response(self, body):
        stream = MagicMock()
        stream.read.return_value = json.dumps(body).encode("utf-8")
        return {"response": stream}

    def test_accepted_run_passes_the_definition_and_token(self):
        mod = _load("invokeAgentRuntime.py")
        invoke = MagicMock(return_value=self._response({"accepted": True}))
        with patch.object(mod.agentcore, "invoke_agent_runtime", invoke), \
                patch.object(mod.sfn, "send_task_failure", MagicMock()) as send_failure:
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["status"] == "ACCEPTED"
        kwargs = invoke.call_args.kwargs
        assert kwargs["agentRuntimeArn"] == _ENV["AGENT_RUNTIME_ARN"]
        payload = json.loads(kwargs["payload"])
        assert payload["taskToken"] == _TOKEN and payload["definition"] == {"mode": "modify"}
        assert len(kwargs["runtimeSessionId"]) >= 33
        send_failure.assert_not_called()

    def test_a_rejected_run_fails_the_inner_token_and_raises(self):
        mod = _load("invokeAgentRuntime.py")
        invoke = MagicMock(return_value=self._response({"accepted": False, "error": "busy"}))
        with patch.object(mod.agentcore, "invoke_agent_runtime", invoke), \
                patch.object(mod.sfn, "send_task_failure", MagicMock()) as send_failure, \
                pytest.raises(RuntimeError):
            mod.lambda_handler(self._event(), MagicMock())
        assert send_failure.call_args.kwargs["taskToken"] == _TOKEN

    def test_an_invoke_error_fails_the_inner_token_and_raises(self):
        mod = _load("invokeAgentRuntime.py")
        with patch.object(mod.agentcore, "invoke_agent_runtime", MagicMock(side_effect=Exception("boom"))), \
                patch.object(mod.sfn, "send_task_failure", MagicMock()) as send_failure, \
                pytest.raises(Exception):
            mod.lambda_handler(self._event(), MagicMock())
        assert send_failure.call_count == 1

    def test_warm_slots_reuse_a_bounded_pool_of_session_ids(self):
        mod = _load("invokeAgentRuntime.py")
        ids = {mod.session_id_for(f"job-{i}", slots=3, namespace="ns") for i in range(50)}
        assert len(ids) == 3
        assert all(len(i) >= 33 and i.startswith("ns-warm-slot-") for i in ids)
        assert mod.session_id_for("job-7", slots=3, namespace="ns") == mod.session_id_for("job-7", slots=3, namespace="ns")

    def test_no_slots_gives_a_fresh_session_per_run(self):
        mod = _load("invokeAgentRuntime.py")
        ids = {mod.session_id_for("job", slots=0, namespace="ns") for _ in range(5)}
        assert len(ids) == 5


# ---------------------------------------------------------------------------------------------------
# pipelineEnd
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestPipelineEnd:
    def test_success_reports_the_outer_token(self):
        mod = _load("pipelineEnd.py")
        with patch.object(mod.sfn, "send_task_success", MagicMock()) as ok, \
                patch.object(mod.sfn, "send_task_failure", MagicMock()) as fail:
            mod.lambda_handler({"externalSfnTaskToken": _TOKEN}, MagicMock())
        ok.assert_called_once()
        fail.assert_not_called()

    def test_error_reports_a_failure_on_the_outer_token(self):
        mod = _load("pipelineEnd.py")
        with patch.object(mod.sfn, "send_task_success", MagicMock()) as ok, \
                patch.object(mod.sfn, "send_task_failure", MagicMock()) as fail:
            mod.lambda_handler({"externalSfnTaskToken": _TOKEN, "error": {"Error": "X"}}, MagicMock())
        ok.assert_not_called()
        assert fail.call_args.kwargs["taskToken"] == _TOKEN
