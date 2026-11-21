#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The state machine's three Lambdas are log sources of the run, and every line they log carries its ids.

VAMS reads a registered log group that every run shares for the lines that carry BOTH the workflow
execution id and the pipeline execution id. The two halves only work together: openPipeline registers
the /aws/lambda/<function> group of ConstructPipelineTask, CogConvertBatchJob and PipelineEndTask, and
each of those handlers binds both ids before it logs. The workflow execution id travels vamsExecute ->
openPipeline's state machine input -> constructPipeline's re-emitted payload -> the batch task's payload;
the pipeline execution id is the end of orchestrationEventPrefix. Reuses the fixtures of
``test_cog_manifest_refactor`` and ``test_cog_batch_job_registration`` (same directory; the suite runs
alone)."""

import ast
import importlib
import importlib.util
import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_cog_manifest_refactor as refactor  # noqa: E402
import test_cog_batch_job_registration as registration  # noqa: E402

PREFIX = "vams.prod.execution.E1.pipeline.P1"
IDS = {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}
STATE_MACHINE_GROUP_ARN = (
    "arn:aws:logs:us-east-1:123456789012:log-group:/aws/vendedlogs/VAMSStateMachine-CogConvertTest:*")
FUNCTION_NAMES = {
    "ConstructPipelineTask": "vams-core-sbx-CogConvertconstructPipeline-Aa1",
    "CogConvertBatchJob": "vams-core-sbx-CogConvertexecuteBatchJob-Bb2",
    "PipelineEndTask": "vams-core-sbx-CogConvertpipelineEnd-Cc3",
}
# Each handler whose group openPipeline registers, by the state that runs it.
HANDLERS = {
    "ConstructPipelineTask": "constructPipeline.py",
    "CogConvertBatchJob": "executeBatchJob.py",
    "PipelineEndTask": "pipelineEnd.py",
}


def _load_open_pipeline(**env):
    """openPipeline read with ``env`` over the suite's defaults (its settings are read at import)."""
    with patch.dict(os.environ, env):
        return importlib.reload(importlib.import_module("openPipeline"))


def _run_open_pipeline(mod, event_over=None):
    """Run openPipeline once; returns (the state machine input, the registration detail)."""
    case = refactor.TestOpenPipeline()
    event = dict(case._event(), workflowExecutionId="E1", orchestrationEventPrefix=PREFIX)
    event.update(event_over or {})
    start = case._mock_start()
    put_events = MagicMock()
    with patch.object(mod.sfn, "start_execution", start), \
            patch.object(mod.events_client, "put_events", put_events):
        assert mod.lambda_handler(event, MagicMock())["statusCode"] == 200
    detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
    return json.loads(start.call_args.kwargs["input"]), detail


def _lambda_handler(path):
    tree = ast.parse(open(path).read())
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "lambda_handler")


def _ordered_calls(function):
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
    return sorted(calls, key=lambda node: (node.lineno, node.col_offset))


def _is_log(call):
    func = call.func
    return (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "logger"
            and func.attr in {"debug", "info", "warning", "error", "exception", "critical"})


def _bound_ids(mod):
    """The ids the handler's (stub) logger was given, after it removed the previous run's."""
    names = [name for name, _args, _kwargs in mod.logger.mock_calls]
    assert names[:2] == ["remove_keys", "append_keys"], names
    assert mod.logger.remove_keys.call_args.args[0] == ["workflowExecutionId", "pipelineExecutionId"]
    return mod.logger.append_keys.call_args.kwargs


@pytest.mark.unit
class TestLambdaLogSourceRegistration:
    def test_each_lambda_state_is_registered_after_the_state_machine(self):
        mod = _load_open_pipeline(STATE_MACHINE_LOG_GROUP_ARN=STATE_MACHINE_GROUP_ARN,
                                  STATE_FUNCTION_NAMES=json.dumps(FUNCTION_NAMES))
        _input, detail = _run_open_pipeline(mod)
        assert [log["sourceType"] for log in detail["logs"]] == ["stateMachine", "lambda", "lambda", "lambda"]
        lambda_logs = detail["logs"][1:]
        assert [log["stageName"] for log in lambda_logs] == list(HANDLERS)
        assert lambda_logs[0] == {
            "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/"
                           + FUNCTION_NAMES["ConstructPipelineTask"],
            "logGroupName": "/aws/lambda/" + FUNCTION_NAMES["ConstructPipelineTask"],
            "logStreamName": "",
            "logStreamPrefix": "",
            "stageName": "ConstructPipelineTask",
            "sourceType": "lambda",
            "label": "ConstructPipelineTask Lambda",
        }

    def test_every_registered_lambda_value_passes_the_backend_validators(self):
        """registerPipelineExecution drops a value its validator rejects, silently, so a shape mismatch
        would surface only as a missing log source."""
        mod = _load_open_pipeline(STATE_MACHINE_LOG_GROUP_ARN=STATE_MACHINE_GROUP_ARN,
                                  STATE_FUNCTION_NAMES=json.dumps(FUNCTION_NAMES))
        _input, detail = _run_open_pipeline(mod)
        validators = registration._backend_validators()
        # Control: with no Lambda entries the loop below would check nothing.
        assert len(detail["logs"]) == 1 + len(FUNCTION_NAMES)
        for log in detail["logs"][1:]:
            assert validators.validate_cloudwatch_log_group_arn("logGroupArn", log["logGroupArn"])[0], log
            assert validators.validate_cloudwatch_log_group_name("logGroupName", log["logGroupName"])[0], log
            assert validators.validate_display_label("label", log["label"])[0], log
            assert validators.validate_log_source_type("sourceType", log["sourceType"])[0], log
            assert validators.validate_sfn_state_name("stageName", log["stageName"])[0], log

    def test_the_state_names_are_the_handlers_states(self):
        mod = _load_open_pipeline()
        assert mod.LAMBDA_STATE_NAMES == tuple(HANDLERS)

    @pytest.mark.parametrize("value", ["", "not json", "[]", "\"x\""])
    def test_a_missing_or_malformed_function_map_registers_the_state_machine_only(self, value):
        mod = _load_open_pipeline(STATE_MACHINE_LOG_GROUP_ARN=STATE_MACHINE_GROUP_ARN, STATE_FUNCTION_NAMES=value)
        _input, detail = _run_open_pipeline(mod)
        assert [log["sourceType"] for log in detail["logs"]] == ["stateMachine"]

    def test_a_state_without_a_function_is_skipped(self):
        names = dict(FUNCTION_NAMES, CogConvertBatchJob="")
        names.pop("PipelineEndTask")
        mod = _load_open_pipeline(STATE_MACHINE_LOG_GROUP_ARN=STATE_MACHINE_GROUP_ARN,
                                  STATE_FUNCTION_NAMES=json.dumps(names))
        _input, detail = _run_open_pipeline(mod)
        assert [log.get("stageName") for log in detail["logs"][1:]] == ["ConstructPipelineTask"]

    def test_without_the_state_machine_group_arn_the_entries_carry_the_name_only(self):
        """The backend derives the ARN from the run's invocation group when only the name is given."""
        mod = _load_open_pipeline(STATE_MACHINE_LOG_GROUP_ARN="", STATE_FUNCTION_NAMES=json.dumps(FUNCTION_NAMES))
        _input, detail = _run_open_pipeline(mod)
        lambda_logs = [log for log in detail["logs"] if log["sourceType"] == "lambda"]
        assert len(lambda_logs) == 3
        for log in lambda_logs:
            assert log["logGroupArn"] == "" and log["logGroupName"].startswith("/aws/lambda/")


@pytest.mark.unit
class TestTheWorkflowExecutionIdTravels:
    def test_vams_execute_hands_it_to_open_pipeline(self):
        case = refactor.TestVamsExecute()
        mod = case._load()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(case._manifest()).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        body = dict(case._body(), workflowExecutionId="E1")
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            assert mod.lambda_handler({"body": json.dumps(body)}, MagicMock())["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["workflowExecutionId"] == "E1"

    def test_open_pipeline_puts_it_in_the_state_machine_input(self):
        sfn_input, _detail = _run_open_pipeline(_load_open_pipeline())
        assert sfn_input["workflowExecutionId"] == "E1"
        assert sfn_input["orchestrationEventPrefix"] == PREFIX

    def test_construct_pipeline_re_emits_it_for_the_batch_task_and_pipeline_end(self):
        """Its outputPath is $.Payload, which replaces the state, and the batch task's payload reads
        $.workflowExecutionId, which fails the state when the field is absent: it is always emitted."""
        mod = refactor.TestConstructPipelineReadsFromS3()._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("missing")
        event = refactor.TestConstructPipelineReadsFromS3()._event()
        with patch.object(mod, "s3", s3):
            with_id = mod.lambda_handler(dict(event, workflowExecutionId="E1", orchestrationEventPrefix=PREFIX),
                                         MagicMock())
            without_id = mod.lambda_handler(event, MagicMock())
        assert with_id["workflowExecutionId"] == "E1"
        assert with_id["orchestrationEventPrefix"] == PREFIX
        assert without_id["workflowExecutionId"] == ""


@pytest.mark.unit
class TestEveryRegisteredHandlerBindsFirst:
    @pytest.mark.parametrize("file_name", sorted(HANDLERS.values()))
    def test_the_ids_are_bound_before_the_first_log_line(self, file_name):
        calls = _ordered_calls(_lambda_handler(os.path.join(_LAMBDA_DIR, file_name)))
        binds = [call for call in calls if getattr(call.func, "attr", "") == "bind_execution_ids"]
        assert binds, f"{file_name}: lambda_handler never binds the execution ids"
        first_log = next(call for call in calls if _is_log(call))
        assert (binds[0].lineno, binds[0].col_offset) < (first_log.lineno, first_log.col_offset)
        assert [ast.unparse(arg) for arg in binds[0].args] == ["logger", "event"]

    def test_construct_pipeline_binds_the_state_machine_input_ids(self):
        mod = refactor.TestConstructPipelineReadsFromS3()._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("missing")
        event = dict(refactor.TestConstructPipelineReadsFromS3()._event(),
                     workflowExecutionId="E1", orchestrationEventPrefix=PREFIX)
        with patch.object(mod, "s3", s3):
            mod.lambda_handler(event, MagicMock())
        assert _bound_ids(mod) == IDS

    def test_execute_batch_job_binds_the_batch_task_payload_ids(self):
        mod = registration._load_execute_batch_job()
        # The batch task's payload (cogConvert-construct.ts CogConvertBatchJob).
        mod.lambda_handler({"taskToken": "tok", "jobName": "CogConvert_1", "definition": ["{}"],
                            "orchestrationEventPrefix": PREFIX, "workflowExecutionId": "E1"}, MagicMock())
        assert _bound_ids(mod) == IDS

    def test_pipeline_end_binds_the_ids_of_the_state_it_closes(self):
        mod = importlib.reload(importlib.import_module("pipelineEnd"))
        mod.sfn = MagicMock()
        # constructPipeline's payload with the batch task's result (the success route).
        mod.lambda_handler({"jobName": "CogConvert_1", "externalSfnTaskToken": "ext",
                            "orchestrationEventPrefix": PREFIX, "workflowExecutionId": "E1",
                            "batchResult": {"status": "ok"}}, MagicMock())
        assert _bound_ids(mod) == IDS


@pytest.mark.unit
class TestTheHelper:
    def test_it_is_the_system_genai_metadata_copy(self):
        """One helper, copied: the SYSTEM GenAI metadata Lambdas and images carry the same bytes."""
        mine = os.path.join(_LAMBDA_DIR, "executionLogKeys.py")
        theirs = os.path.join(registration._repo_root(), "backendPipelines", "system", "genAiMetadata", "lambda",
                              "executionLogKeys.py")
        assert open(mine, "rb").read() == open(theirs, "rb").read()

    def test_every_line_of_the_real_logger_carries_both_ids_and_a_reused_environment_drops_them(self):
        pytest.importorskip("aws_lambda_powertools")
        spec = importlib.util.spec_from_file_location(
            "cog_custom_logger_undertest", os.path.join(_LAMBDA_DIR, "customLogging", "logger.py"))
        logger_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(logger_module)
        spec = importlib.util.spec_from_file_location(
            "cog_execution_log_keys_undertest", os.path.join(_LAMBDA_DIR, "executionLogKeys.py"))
        keys = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(keys)
        stream = io.StringIO()
        logger = logger_module.safeLogger(service="cogKeysEveryLine", stream=stream)
        keys.bind_execution_ids(logger, {"workflowExecutionId": "E1", "orchestrationEventPrefix": PREFIX,
                                         "externalSfnTaskToken": "x" * 300})
        logger.info("Submitting Batch job")
        logger.error({"message": "structured", "assetId": "xidC"})
        keys.bind_execution_ids(logger, {"jobName": "direct"})
        logger.info("direct invocation")
        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 3
        for line in lines[:2]:
            assert line["workflowExecutionId"] == "E1" and line["pipelineExecutionId"] == "P1"
        assert "workflowExecutionId" not in lines[2] and "pipelineExecutionId" not in lines[2]
        assert "x" * 300 not in stream.getvalue()
