#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every Lambda of this pipeline puts the run's execution ids on its log lines, and openPipeline registers
those Lambdas' log groups as the run's log sources.

VAMS reads a registered log group that every run shares for the lines that carry BOTH the workflow
execution id and the pipeline execution id (executionService, pipeline-scoped full mode). The two halves
are only useful together: an unregistered group is never read, and a registered group whose lines carry
no ids reads empty. The handler checks walk each handler's AST, because the media and Blender images
cannot be imported here; the behaviour checks run the helper on the pipeline's real logger.
"""

import ast
import datetime
import importlib
import importlib.util
import io
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

_REPO_PIPELINES = os.path.join(h.REPO_ROOT, "backendPipelines")
# Every handler of this pipeline, with the expression its first binding call passes as the state.
_HANDLERS = {
    os.path.join(h.LAMBDA_DIR, "vamsExecuteSystemGenAiMetadataPipeline.py"): "None",
    os.path.join(h.LAMBDA_DIR, "openPipeline.py"): "event",
    os.path.join(h.LAMBDA_DIR, "constructPipeline.py"): "event",
    os.path.join(h.LAMBDA_DIR, "generateMetadata.py"): "event",
    os.path.join(h.LAMBDA_DIR, "generateEmbedding.py"): "event",
    os.path.join(h.LAMBDA_DIR, "pipelineEnd.py"): "event",
    os.path.join(h.PIPELINE_ROOT, "containers", "media", "handler.py"): "payload",
    os.path.join(h.PIPELINE_ROOT, "containers", "media", "segment_handler.py"): "state",
    os.path.join(h.PIPELINE_ROOT, "containers", "blender", "handler.py"): "event",
}
_RENDER3D_HANDLER = os.path.join(_REPO_PIPELINES, "preview", "3dThumbnail", "container", "lambda_handler.py")

_ACCOUNT_ENV = {
    "STATE_MACHINE_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:123456789012:log-group:/aws/vendedlogs/VAMSStateMachine-SystemGenAiMetadataTest:*",
    "AWS_LAMBDA_FUNCTION_NAME": "vams-core-sbx-SystemGenAiMetadataOpenPipeline-AbC123",
}
_FUNCTION_NAMES = {
    "ConstructPipelineTask": "vams-core-sbx-SystemGenAiMetadataConstructPipe-Aa1",
    "BlenderRenderTask": "vams-core-sbx-SystemGenAiMetadataBlenderRender-Bb2",
    "Render3dTask": "vams-core-sbx-SystemGenAiMetadataRender3d-Cc3",
    "MediaExtractTask": "vams-core-sbx-SystemGenAiMetadataMediaExtract-Dd4",
    "GenerateMetadataTask": "vams-core-sbx-SystemGenAiMetadataGenerateMeta-Ee5",
    "GenerateEmbeddingTask": "vams-core-sbx-SystemGenAiMetadataGenerateEmbe-Ff6",
    "VideoSegmentMap": "vams-core-sbx-SystemGenAiMetadataSegmentAnaly-Gg7",
    "PipelineEndTask": "vams-core-sbx-SystemGenAiMetadataPipelineEnd-Hh8",
}


def _backend_validators():
    backend_pkg = os.path.join(h.REPO_ROOT, "backend", "backend")
    if backend_pkg not in sys.path:
        sys.path.append(backend_pkg)
    return importlib.import_module("common.validators")


def _keys_module():
    return h.load_local("executionLogKeys")


def _real_logger_module():
    """This pipeline's customLogging/logger.py, loaded by path: the harness stubs the package."""
    pytest.importorskip("aws_lambda_powertools")
    path = os.path.join(h.LAMBDA_DIR, "customLogging", "logger.py")
    spec = importlib.util.spec_from_file_location("sysgenai_custom_logger_undertest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def _lambda_handler(path):
    tree = ast.parse(open(path).read())
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "lambda_handler")


def _ordered_calls(function):
    """Every call in the function body, in source order (nested blocks included)."""
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
    return sorted(calls, key=lambda node: (node.lineno, node.col_offset))


def _is_bind(call):
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr == "bind_execution_ids"


def _is_log(call):
    func = call.func
    return (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "logger"
            and func.attr in {"debug", "info", "warning", "error", "exception", "critical"})


@pytest.mark.unit
class TestExecutionIds:
    def test_both_ids_from_the_state(self):
        keys = _keys_module()
        assert keys.execution_ids({"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}) == {
            "workflowExecutionId": "E1", "pipelineExecutionId": "P1"}

    def test_the_pipeline_execution_id_falls_back_to_the_event_prefix(self):
        keys = _keys_module()
        state = {"workflowExecutionId": "E1", "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"}
        assert keys.execution_ids(state) == {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}

    def test_an_explicit_pipeline_execution_id_wins_over_the_prefix(self):
        keys = _keys_module()
        state = {"pipelineExecutionId": "P2", "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"}
        assert keys.execution_ids(state) == {"pipelineExecutionId": "P2"}

    @pytest.mark.parametrize("state", [None, "E1", [], {}, {"workflowExecutionId": ""},
                                       {"workflowExecutionId": 7, "pipelineExecutionId": None},
                                       {"orchestrationEventPrefix": "vams.prod.execution.E1"}])
    def test_no_ids_when_the_state_carries_none(self, state):
        assert _keys_module().execution_ids(state) == {}


@pytest.mark.unit
class TestBindingOnTheRealLogger:
    def test_every_line_carries_both_ids(self):
        keys = _keys_module()
        stream = io.StringIO()
        logger = _real_logger_module().safeLogger(service="sysgenaiKeysEveryLine", stream=stream)
        keys.bind_execution_ids(logger, {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"})
        logger.info("branch started")
        logger.warning("a section of the model could not be read")
        logger.error({"message": "structured", "assetId": "xidM"})
        lines = _lines(stream)
        assert len(lines) == 3
        for line in lines:
            assert line["workflowExecutionId"] == "E1" and line["pipelineExecutionId"] == "P1"

    def test_a_reused_environment_does_not_carry_the_previous_run(self):
        keys = _keys_module()
        stream = io.StringIO()
        logger = _real_logger_module().safeLogger(service="sysgenaiKeysReuse", stream=stream)
        keys.bind_execution_ids(logger, {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"})
        logger.info("first run")
        keys.bind_execution_ids(logger, {"workflowExecutionId": "E2"})
        logger.info("second run")
        keys.bind_execution_ids(logger, None)
        logger.info("direct invocation")
        first, second, third = _lines(stream)
        assert (first["workflowExecutionId"], first["pipelineExecutionId"]) == ("E1", "P1")
        assert second["workflowExecutionId"] == "E2" and "pipelineExecutionId" not in second
        assert "workflowExecutionId" not in third and "pipelineExecutionId" not in third

    def test_the_task_token_is_not_bound(self):
        keys = _keys_module()
        stream = io.StringIO()
        logger = _real_logger_module().safeLogger(service="sysgenaiKeysToken", stream=stream)
        keys.bind_execution_ids(logger, {"workflowExecutionId": "E1", "externalSfnTaskToken": h.TASK_TOKEN})
        logger.info("bound")
        assert h.TASK_TOKEN not in stream.getvalue()


@pytest.mark.unit
class TestEveryHandlerBindsFirst:
    @pytest.mark.parametrize("path", sorted(_HANDLERS), ids=lambda p: os.path.relpath(p, h.PIPELINE_ROOT))
    def test_the_ids_are_bound_before_the_first_log_line(self, path):
        calls = _ordered_calls(_lambda_handler(path))
        binds = [call for call in calls if _is_bind(call)]
        assert binds, f"{path}: lambda_handler never binds the execution ids"
        first_log = next((call for call in calls if _is_log(call)), None)
        first_bind_at = (binds[0].lineno, binds[0].col_offset)
        assert first_log is None or first_bind_at < (first_log.lineno, first_log.col_offset)
        first = binds[0]
        assert ast.unparse(first.args[0]) == "logger"
        assert ast.unparse(first.args[1]) == _HANDLERS[path]

    def test_vams_execute_binds_the_parsed_body(self):
        path = os.path.join(h.LAMBDA_DIR, "vamsExecuteSystemGenAiMetadataPipeline.py")
        binds = [call for call in _ordered_calls(_lambda_handler(path)) if _is_bind(call)]
        assert [ast.unparse(call.args[1]) for call in binds] == ["None", "data"]

    def test_render3d_binds_the_state_before_anything_else(self):
        function = _lambda_handler(_RENDER3D_HANDLER)
        statements = function.body
        assert ast.unparse(statements[1]) == "bind_execution_ids(body)"

    def test_the_media_and_blender_images_copy_the_helper(self):
        """The images import executionLogKeys by bare name; each Dockerfile copies it beside the handler."""
        for image in ("media", "blender"):
            dockerfile = open(os.path.join(h.PIPELINE_ROOT, "containers", image, "Dockerfile")).read()
            copies = [line for line in dockerfile.splitlines() if line.startswith("COPY ")
                      and "executionLogKeys.py" in line]
            assert copies, image


@pytest.mark.unit
class TestHandlersBindAtRuntime:
    def test_open_pipeline_binds_the_event_ids_before_it_logs(self):
        mod = h.load_handler("openPipeline", {"ALLOWED_INPUT_FILEEXTENSIONS": "*"})
        mod.sfn = MagicMock()
        mod.sfn.start_execution = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:SystemGenAiMetadata:x",
            "startDate": datetime.datetime(2026, 1, 1)})
        mod.events_client = MagicMock()
        mod.lambda_handler({"inputS3AssetFilePath": "s3://abkt/xidM/models/pump.glb",
                            "workflowExecutionId": "E1",
                            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
                            "sfnExternalTaskToken": h.TASK_TOKEN}, MagicMock())
        names = [name for name, _args, _kwargs in mod.logger.mock_calls]
        assert names[:2] == ["remove_keys", "append_keys"]
        assert mod.logger.remove_keys.call_args.args[0] == ["workflowExecutionId", "pipelineExecutionId"]
        expected = {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}
        assert mod.logger.append_keys.call_args.kwargs == expected

    def test_pipeline_end_binds_the_state_ids(self):
        mod = h.load_handler("pipelineEnd")
        mod.sfn_client = MagicMock()
        mod.sfn = MagicMock()
        mod.lambda_handler({"workflowExecutionId": "E1", "pipelineExecutionId": "P1",
                            "externalSfnTaskToken": h.TASK_TOKEN}, MagicMock())
        names = [name for name, _args, _kwargs in mod.logger.mock_calls]
        assert names[:2] == ["remove_keys", "append_keys"]
        expected = {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}
        assert mod.logger.append_keys.call_args.kwargs == expected


def _run_open_pipeline(env, event_over=None):
    mod = h.load_handler("openPipeline", dict({"ALLOWED_INPUT_FILEEXTENSIONS": "*"}, **env))
    mod.sfn = MagicMock()
    mod.sfn.start_execution = MagicMock(return_value={
        "executionArn": "arn:aws:states:us-east-1:123456789012:execution:SystemGenAiMetadata:x",
        "startDate": datetime.datetime(2026, 1, 1)})
    mod.events_client = MagicMock()
    event = {"inputS3AssetFilePath": "s3://abkt/xidM/models/pump.glb", "workflowExecutionId": "E1",
             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1", "sfnExternalTaskToken": h.TASK_TOKEN}
    event.update(event_over or {})
    mod.lambda_handler(event, MagicMock())
    entry = mod.events_client.put_events.call_args.kwargs["Entries"][0]
    return mod, json.loads(entry["Detail"])


@pytest.mark.unit
class TestLambdaLogSourceRegistration:
    def test_each_state_lambda_and_open_pipeline_are_registered(self):
        mod, detail = _run_open_pipeline(dict(_ACCOUNT_ENV, ANALYSIS_FUNCTION_NAMES=json.dumps(_FUNCTION_NAMES)))
        lambda_logs = [log for log in detail["logs"] if log["sourceType"] == "lambda"]
        assert [log["logGroupName"] for log in lambda_logs] == (
            ["/aws/lambda/" + _ACCOUNT_ENV["AWS_LAMBDA_FUNCTION_NAME"]]
            + ["/aws/lambda/" + _FUNCTION_NAMES[state] for state in mod.LAMBDA_STATE_NAMES])
        assert [log.get("stageName") for log in lambda_logs] == [None] + list(mod.LAMBDA_STATE_NAMES)
        assert lambda_logs[0]["label"] == "openPipeline Lambda"
        assert lambda_logs[1] == {
            "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/"
                           + _FUNCTION_NAMES["ConstructPipelineTask"],
            "logGroupName": "/aws/lambda/" + _FUNCTION_NAMES["ConstructPipelineTask"],
            "logStreamName": "",
            "logStreamPrefix": "",
            "stageName": "ConstructPipelineTask",
            "sourceType": "lambda",
            "label": "ConstructPipelineTask Lambda",
        }

    def test_every_registered_lambda_value_passes_the_backend_validators(self):
        """registerPipelineExecution drops a value its validator rejects, silently, so a shape mismatch
        would surface only as a missing log source."""
        _mod, detail = _run_open_pipeline(dict(_ACCOUNT_ENV, ANALYSIS_FUNCTION_NAMES=json.dumps(_FUNCTION_NAMES)))
        validators = _backend_validators()
        for log in detail["logs"]:
            if log["sourceType"] != "lambda":
                continue
            assert validators.validate_cloudwatch_log_group_arn("logGroupArn", log["logGroupArn"])[0], log
            assert validators.validate_cloudwatch_log_group_name("logGroupName", log["logGroupName"])[0], log
            assert validators.validate_display_label("label", log["label"])[0], log
            assert validators.validate_log_source_type("sourceType", log["sourceType"])[0], log
            if "stageName" in log:
                assert validators.validate_sfn_state_name("stageName", log["stageName"])[0], log

    def test_the_registration_stays_inside_the_read_caps(self):
        """At most 50 entries are stored and 20 registered sources are read per logs request."""
        _mod, detail = _run_open_pipeline(dict(_ACCOUNT_ENV, ANALYSIS_FUNCTION_NAMES=json.dumps(_FUNCTION_NAMES),
                                               BATCH_JOB_LOG_GROUP_NAME="/aws/vendedlogs/Pipelines/RenderX",
                                               BATCH_JOB_LOG_GROUP_ARN="arn:aws:logs:us-east-1:123456789012:"
                                                                       "log-group:/aws/vendedlogs/Pipelines/RenderX:*",
                                               BATCH_JOB_DEFINITION_NAME="RenderJobX"))
        assert [log["sourceType"] for log in detail["logs"][:2]] == ["stateMachine", "batch"]
        assert len(detail["logs"]) == 11 <= 20

    def test_the_state_names_are_the_construct_ids(self):
        mod = h.load_handler("openPipeline", {"ALLOWED_INPUT_FILEEXTENSIONS": "*"})
        assert set(mod.LAMBDA_STATE_NAMES) == set(_FUNCTION_NAMES)
        assert len(mod.LAMBDA_STATE_NAMES) == len(set(mod.LAMBDA_STATE_NAMES))

    @pytest.mark.parametrize("value", ["", "not json", "[]", "\"x\""])
    def test_a_missing_or_malformed_function_map_registers_open_pipeline_only(self, value):
        _mod, detail = _run_open_pipeline(dict(_ACCOUNT_ENV, ANALYSIS_FUNCTION_NAMES=value))
        assert [log["sourceType"] for log in detail["logs"]] == ["stateMachine", "lambda"]

    def test_a_state_without_a_function_is_skipped(self):
        names = dict(_FUNCTION_NAMES)
        names.pop("BlenderRenderTask")
        names["MediaExtractTask"] = ""
        _mod, detail = _run_open_pipeline(dict(_ACCOUNT_ENV, ANALYSIS_FUNCTION_NAMES=json.dumps(names)))
        stages = [log.get("stageName") for log in detail["logs"] if log["sourceType"] == "lambda"]
        assert "BlenderRenderTask" not in stages and "MediaExtractTask" not in stages
        assert len(stages) == 1 + len(_FUNCTION_NAMES) - 2

    def test_without_the_state_machine_group_arn_the_entries_carry_the_name_only(self):
        """The backend derives the ARN from the run's invocation group when only the name is given."""
        env = dict(_ACCOUNT_ENV, ANALYSIS_FUNCTION_NAMES=json.dumps(_FUNCTION_NAMES), STATE_MACHINE_LOG_GROUP_ARN="")
        _mod, detail = _run_open_pipeline(env)
        for log in detail["logs"]:
            if log["sourceType"] == "lambda":
                assert log["logGroupArn"] == "" and log["logGroupName"].startswith("/aws/lambda/")
