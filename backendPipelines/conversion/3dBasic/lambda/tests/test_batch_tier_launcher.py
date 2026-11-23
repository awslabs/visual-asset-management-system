#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/3dBasic launcher lambdas (#1732): the tier selector's job-size choice and its refusal
message, the state machine input openPipeline builds, the Batch job request constructPipeline builds,
and the task-token handling of vamsExecute and pipelineEnd. The container side is covered by
lambdaContainer/tests/test_3dbasic_batch_tier.py; these Lambdas are code only until the infrastructure
that deploys them (a later change) exists."""

import importlib
import json
import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONTAINER_DIR = os.path.join(os.path.dirname(_LAMBDA_DIR), "lambdaContainer")
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _pkg = types.ModuleType("customLogging")
    _logger = types.ModuleType("customLogging.logger")
    _logger.safeLogger = lambda **kw: MagicMock()
    _pkg.logger = _logger
    sys.modules["customLogging"] = _pkg
    sys.modules["customLogging.logger"] = _logger

os.environ.setdefault("OPEN_PIPELINE_FUNCTION_NAME", "test-open-pipeline")
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:111111111111:stateMachine:test")
os.environ.setdefault("ALLOWED_INPUT_FILEEXTENSIONS", ".stl,.step,.glb,.usdc")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

import computeTier  # noqa: E402

MIB = 1024 ** 2
GIB_MIB = 1024
TOKEN = "AAAA-task-token-secret"


def _load(name):
    with patch("boto3.client", MagicMock()):
        if name in sys.modules:
            return importlib.reload(sys.modules[name])
        return importlib.import_module(name)


def _escalation(input_mb, pair=".step to .usdc", min_mib=10240, limits_mb=(250, 504, 1011), name="model.step",
                resource="function memory", reason="size", required=None):
    return {"escalate": True, "reason": reason, "message": "m", "inputName": name,
            "inputBytes": input_mb * MIB, "formatPair": pair, "resource": resource,
            "requiredMemoryMiB": required, "minTaskMemoryMiB": min_mib,
            "limitBytesByTaskMemoryMiB": {str(mib): limit * MIB for mib, limit in
                                          zip((30720, 61440, 122880), limits_mb)}}


@pytest.mark.unit
class TestJobSizeChoice:
    @pytest.mark.parametrize("input_mb,expected", [(120, 30720), (250, 30720), (251, 61440), (504, 61440),
                                                   (505, 122880), (1011, 122880)])
    def test_the_smallest_size_that_admits_the_input_runs_it(self, input_mb, expected):
        assert computeTier.choose_job_size(_escalation(input_mb))["memoryMiB"] == expected

    def test_the_size_record_names_vcpu_and_memory(self):
        assert computeTier.choose_job_size(_escalation(120)) == {"vcpu": 4, "memoryMiB": 30720, "key": "30"}
        assert computeTier.choose_job_size(_escalation(1000)) == {"vcpu": 16, "memoryMiB": 122880, "key": "120"}

    def test_a_run_that_ran_out_of_memory_needs_a_larger_size_than_it_had(self):
        # Killed on a 30 GiB task: 30 GiB is no answer even though the input is under its limit.
        escalation = _escalation(100, min_mib=30720, reason="memory")
        assert computeTier.choose_job_size(escalation)["memoryMiB"] == 61440

    def test_the_cap_removes_the_larger_sizes(self):
        assert computeTier.choose_job_size(_escalation(120), 60)["memoryMiB"] == 30720
        with pytest.raises(computeTier.JobSizeRefused):
            computeTier.choose_job_size(_escalation(300), 30)

    def test_an_input_over_the_largest_size_is_refused_with_the_limit_the_pair_and_the_size(self):
        with pytest.raises(computeTier.JobSizeRefused) as excinfo:
            computeTier.choose_job_size(_escalation(1500))
        message = str(excinfo.value)
        assert message == (
            "model.step is 1,500.0 MB, over the 1,011.0 MB limit for converting .step to .usdc on the largest "
            "job size this deployment allows (16 vCPU and 120 GiB). The limit is set by the job memory. "
            "Reduce the model's size and run the conversion again.")

    def test_a_capped_deployment_says_so_and_names_the_key_and_the_largest_available(self):
        with pytest.raises(computeTier.JobSizeRefused) as excinfo:
            computeTier.choose_job_size(_escalation(600), 60)
        message = str(excinfo.value)
        assert "over the 504.0 MB limit" in message and "(8 vCPU and 60 GiB)" in message
        assert "app.pipelines.useConversion3dBasic.batchTier.maxMemoryGiB" in message
        assert "largest job available is 16 vCPU and 120 GiB" in message

    def test_a_memory_kill_on_the_largest_size_is_refused_by_the_memory_it_had(self):
        escalation = _escalation(100, min_mib=122880, reason="memory")
        with pytest.raises(computeTier.JobSizeRefused, match="did not convert in the 120 GiB of memory it had"):
            computeTier.choose_job_size(escalation)

    def test_a_request_without_limits_takes_the_smallest_size_above_the_floor(self):
        assert computeTier.choose_job_size({"escalate": True, "minTaskMemoryMiB": 10240})["memoryMiB"] == 30720
        assert computeTier.choose_job_size({"escalate": True})["memoryMiB"] == 30720

    def test_unusable_limit_values_are_ignored(self):
        escalation = _escalation(100)
        escalation["limitBytesByTaskMemoryMiB"] = {"30720": "x", "bad": 1, "61440": None}
        assert computeTier.choose_job_size(escalation)["memoryMiB"] == 30720

    @pytest.mark.parametrize("value,expected", [(30, 30), (60, 60), (120, 120), (None, 120), ("x", 120),
                                                (45, 120), (True, 120), ("60", 60)])
    def test_the_cap(self, value, expected):
        assert computeTier.max_memory_gib(value) == expected

    def test_the_job_sizes_are_the_ones_the_container_knows(self):
        with open(os.path.join(_CONTAINER_DIR, "size_check.py"), encoding="utf-8") as handle:
            source = handle.read()
        match = re.search(r"(?m)^BATCH_TASK_SIZES = \((.*)\)$", source)
        container = [tuple(int(x) if "*" not in x else int(x.split("*")[0]) * int(x.split("*")[1])
                           for x in re.split(r",\s*", pair.strip(" ()"))) for pair in
                     re.findall(r"\([^()]*\)", match.group(1))]
        assert container == list(computeTier.JOB_SIZES)

    def test_the_selector_answers_the_size_and_the_reason(self):
        mod = _load("selectJobSize")
        result = mod.lambda_handler({"attempt": {"Payload": _escalation(300, reason="size", required=9000)},
                                     "maxMemoryGiB": 120}, MagicMock())
        assert result == {"jobSize": {"vcpu": 8, "memoryMiB": 61440, "key": "60"},
                          "escalationReason": "size", "requiredMemoryMiB": 9000}

    def test_the_selector_fails_the_run_with_the_refusal(self):
        mod = _load("selectJobSize")
        with pytest.raises(computeTier.JobSizeRefused, match="over the 1,011.0 MB limit"):
            mod.lambda_handler({"attempt": {"Payload": _escalation(2000)}, "maxMemoryGiB": 120}, MagicMock())

    @pytest.mark.parametrize("attempt", [None, {}, {"Payload": {"body": "Success"}}, {"Payload": {"escalate": "true"}}])
    def test_the_selector_refuses_a_run_that_asked_for_nothing(self, attempt):
        mod = _load("selectJobSize")
        with pytest.raises(ValueError, match="did not ask for a larger job"):
            mod.lambda_handler({"attempt": attempt}, MagicMock())


@pytest.mark.unit
class TestForcedTier:
    @pytest.mark.parametrize("parameters,tier", [
        ({"computeTier": "batch"}, "batch"), ({"computeTier": " LAMBDA "}, "lambda"),
        ({"computeTier": "auto"}, "auto"), ({"computeTier": "gpu"}, "auto"), ({"computeTier": 3}, "auto"),
        ({}, "auto"), (None, "auto"), ("batch", "auto")])
    def test_requested_tier(self, parameters, tier):
        assert computeTier.requested_tier(parameters) == tier

    @pytest.mark.parametrize("parameters,cap,memory", [
        ({}, 120, 30720), ({"batchMemoryGiB": 60}, 120, 61440), ({"batchMemoryGiB": 120}, 120, 122880),
        ({"batchMemoryGiB": 120}, 60, 30720), ({"batchMemoryGiB": 45}, 120, 30720),
        ({"batchMemoryGiB": True}, 120, 30720), ({"batchMemoryGiB": "60"}, 120, 30720)])
    def test_forced_batch_size(self, parameters, cap, memory):
        assert computeTier.forced_batch_size(parameters, cap)["memoryMiB"] == memory


def _s3_with_configuration(configuration):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(configuration).encode("utf-8"))}
    return s3


def _open_event(**extra):
    event = {
        "sfnExternalTaskToken": TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xid/m.step",
        "inputManifestS3Location": "s3://aux/manifests/E1/m.json",
        "inputConfigurationS3Location": "s3://aux/configs/E1/c.json",
        "workflowExecutionId": "E1",
        "outputS3AssetResultsPath": "s3://abkt/pipelines/p/J/output/E1/results/",
        "inputVersionId": "v1", "assetId": "xid", "databaseId": "db",
        "orchestrationEventPrefix": "vams.pipeline.E1.p",
    }
    event.update(extra)
    return event


@pytest.mark.unit
class TestOpenPipeline:
    def _run(self, event, configuration=None):
        mod = _load("openPipeline")
        sfn = MagicMock()
        sfn.start_execution.return_value = {"executionArn": "arn:x", "startDate": __import__("datetime").datetime(2026, 1, 1)}
        with patch.object(mod, "sfn", sfn), patch.object(mod, "events_client", MagicMock()), \
                patch.object(mod, "s3_client", _s3_with_configuration(configuration or {})):
            response = mod.lambda_handler(event, MagicMock())
        return response, sfn, mod

    def test_the_state_machine_input_carries_the_request_and_never_the_token_inside_it(self):
        response, sfn, _ = self._run(_open_event())
        assert response["statusCode"] == 200
        sent = json.loads(sfn.start_execution.call_args.kwargs["input"])
        assert sent["conversionRequest"] == {
            "inputManifestS3Location": "s3://aux/manifests/E1/m.json",
            "inputConfigurationS3Location": "s3://aux/configs/E1/c.json",
            "workflowExecutionId": "E1", "pipelineExecutionId": "E1.p"}
        assert sent["externalSfnTaskToken"] == TOKEN
        assert TOKEN not in json.dumps(sent["conversionRequest"])
        assert sent["computeTier"] == "auto" and sent["maxMemoryGiB"] == 120
        assert sent["inputVersionId"] == "v1"
        assert sent["jobName"].startswith("PipelineJob_")

    def test_a_forced_batch_run_carries_its_job_size(self):
        _, sfn, _ = self._run(_open_event(), {"computeTier": "batch", "batchMemoryGiB": 60})
        sent = json.loads(sfn.start_execution.call_args.kwargs["input"])
        assert sent["computeTier"] == "batch"
        assert sent["forcedJobSize"]["memoryMiB"] == 61440

    def test_a_forced_lambda_run_has_no_job_size(self):
        _, sfn, _ = self._run(_open_event(), {"computeTier": "lambda"})
        sent = json.loads(sfn.start_execution.call_args.kwargs["input"])
        assert sent["computeTier"] == "lambda" and sent["forcedJobSize"] == {}

    def test_an_unreadable_configuration_leaves_the_choice_to_the_run(self):
        mod = _load("openPipeline")
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("denied")
        sfn = MagicMock()
        sfn.start_execution.return_value = {"executionArn": "arn:x", "startDate": __import__("datetime").datetime(2026, 1, 1)}
        with patch.object(mod, "sfn", sfn), patch.object(mod, "events_client", MagicMock()), patch.object(mod, "s3_client", s3):
            mod.lambda_handler(_open_event(), MagicMock())
        assert json.loads(sfn.start_execution.call_args.kwargs["input"])["computeTier"] == "auto"

    @pytest.mark.parametrize("path,message", [("s3://abkt/xid/", "folder"), ("s3://abkt/xid/m.exe", "file type"),
                                              ("s3://abkt/xid/m.st", "file type"), ("s3://abkt/xid/m", "file type")])
    def test_a_folder_or_an_unlisted_extension_fails_the_task_before_any_run(self, path, message):
        mod = _load("openPipeline")
        sfn = MagicMock()
        with patch.object(mod, "sfn", sfn):
            response = mod.lambda_handler(_open_event(inputS3AssetFilePath=path), MagicMock())
        assert response["statusCode"] == 400
        sfn.start_execution.assert_not_called()
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TOKEN

    def test_a_failed_start_fails_the_task_and_answers_500(self):
        mod = _load("openPipeline")
        sfn = MagicMock()
        sfn.start_execution.side_effect = Exception("throttled")
        with patch.object(mod, "sfn", sfn), patch.object(mod, "events_client", MagicMock()), \
                patch.object(mod, "s3_client", _s3_with_configuration({})):
            response = mod.lambda_handler(_open_event(), MagicMock())
        assert response["statusCode"] == 500
        assert sfn.send_task_failure.call_count == 1

    def test_the_state_machine_and_lambda_attempt_log_sources_are_registered(self):
        mod = _load("openPipeline")
        events = MagicMock()
        with patch.object(mod, "events_client", events), \
                patch.object(mod, "ORCHESTRATION_BUS_NAME", "bus"), \
                patch.object(mod, "STATE_MACHINE_LOG_GROUP_NAME", "/sm"), \
                patch.object(mod, "CONVERTER_LOG_GROUP_NAME", "/aws/lambda/converter"):
            mod.register_sub_execution("bus", "vams.execution.E1.pipeline.P1", "arn:exec", "arn:sm")
        detail = json.loads(events.put_events.call_args.kwargs["Entries"][0]["Detail"])
        sources = {(log["sourceType"], log.get("stageName", "")) for log in detail["logs"]}
        # The Batch container's source is registered by constructPipeline, under the state the run takes.
        assert sources == {("stateMachine", ""), ("lambda", "ConversionLambdaAttempt")}

    def test_the_converter_request_and_the_state_machine_carry_the_pipeline_execution_id(self):
        # The converter's group is read for lines carrying BOTH execution ids, so the converter needs this one.
        _, sfn, _ = self._run(_open_event(orchestrationEventPrefix="vams.execution.E1.pipeline.P1"))
        sent = json.loads(sfn.start_execution.call_args.kwargs["input"])
        assert sent["conversionRequest"]["pipelineExecutionId"] == "P1"
        assert sent["conversionRequest"]["workflowExecutionId"] == "E1"
        assert sent["orchestrationEventPrefix"] == "vams.execution.E1.pipeline.P1"

    def test_without_an_orchestration_prefix_the_request_has_no_pipeline_execution_id(self):
        _, sfn, _ = self._run(_open_event(orchestrationEventPrefix=""))
        assert "pipelineExecutionId" not in json.loads(sfn.start_execution.call_args.kwargs["input"])[
            "conversionRequest"]


@pytest.mark.unit
class TestConstructPipeline:
    def test_the_job_request_holds_locations_and_the_reason_but_never_the_token(self):
        mod = _load("constructPipeline")
        event = {"jobName": "J", "externalSfnTaskToken": TOKEN,
                 "conversionRequest": {"inputManifestS3Location": "s3://aux/m.json",
                                       "inputConfigurationS3Location": "s3://aux/c.json",
                                       "workflowExecutionId": "E1", "TaskToken": TOKEN, "other": "x"},
                 "selection": {"escalationReason": "memory", "requiredMemoryMiB": 40000}}
        result = mod.lambda_handler(event, MagicMock())
        request = json.loads(result["conversionEvent"])
        assert request == {"inputManifestS3Location": "s3://aux/m.json",
                           "inputConfigurationS3Location": "s3://aux/c.json", "workflowExecutionId": "E1",
                           "escalationReason": "memory", "requiredMemoryMiB": 40000}
        assert TOKEN not in result["conversionEvent"]
        assert "allowEscalation" not in request

    def test_an_unknown_reason_is_not_passed_on(self):
        mod = _load("constructPipeline")
        request = mod.conversion_event({"workflowExecutionId": "E1"}, "<x>", True)
        assert request == {"workflowExecutionId": "E1"}

    def test_a_forced_run_has_no_reason(self):
        mod = _load("constructPipeline")
        result = mod.lambda_handler({"conversionRequest": {"inputManifestS3Location": "s3://aux/m.json"}}, MagicMock())
        assert json.loads(result["conversionEvent"]) == {"inputManifestS3Location": "s3://aux/m.json"}

    def test_a_request_too_large_for_the_job_environment_is_refused(self):
        mod = _load("constructPipeline")
        with pytest.raises(ValueError, match="too large"):
            mod.lambda_handler({"conversionRequest": {"inputManifestS3Location": "s3://aux/" + "k" * 7000}}, MagicMock())

    def test_the_job_request_carries_the_pipeline_execution_id(self):
        mod = _load("constructPipeline")
        request = mod.conversion_event({"workflowExecutionId": "E1", "pipelineExecutionId": "P1"})
        assert request == {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}

    STATE_NAMES = {"30720": "ConversionBatchJob", "61440": "ConversionBatchJob8Vcpu",
                   "122880": "ConversionBatchJob16Vcpu"}

    def _register(self, selection, prefix="vams.execution.E1.pipeline.P1", bus="bus"):
        mod = _load("constructPipeline")
        events = MagicMock()
        with patch.object(mod, "events_client", events), \
                patch.object(mod, "ORCHESTRATION_BUS_NAME", bus), \
                patch.object(mod, "BATCH_JOB_LOG_GROUP_NAME", "/aws/vendedlogs/Pipelines/Conversion3dBasic"), \
                patch.object(mod, "BATCH_JOB_LOG_GROUP_ARN", "arn:aws:logs:us-east-1:1:log-group:/x:*"), \
                patch.object(mod, "BATCH_JOB_DEFINITION_NAME", "Conversion3dBasicJob"), \
                patch.object(mod, "BATCH_STATE_NAMES", self.STATE_NAMES):
            mod.lambda_handler({"jobName": "J", "orchestrationEventPrefix": prefix,
                                "conversionRequest": {"workflowExecutionId": "E1"}, "selection": selection},
                               MagicMock())
        return events

    @pytest.mark.parametrize("memory,state", [(30720, "ConversionBatchJob"), (61440, "ConversionBatchJob8Vcpu"),
                                              (122880, "ConversionBatchJob16Vcpu")])
    def test_the_container_log_is_registered_under_the_state_each_job_size_runs(self, memory, state):
        events = self._register({"jobSize": {"vcpu": 0, "memoryMiB": memory}, "escalationReason": "size"})
        entry = events.put_events.call_args.kwargs["Entries"][0]
        assert entry["Source"] == "vams.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["logs"] == [{
            "logGroupArn": "arn:aws:logs:us-east-1:1:log-group:/x:*",
            "logGroupName": "/aws/vendedlogs/Pipelines/Conversion3dBasic",
            "logStreamName": "",
            "logStreamPrefix": "Conversion3dBasicJob/default/",
            "stageName": state,
            "sourceType": "batch",
            "label": "Conversion job container (AWS Batch)",
        }]
        assert "subExecution" not in detail

    def test_an_unmapped_size_registers_the_state_the_choice_defaults_to(self):
        events = self._register({})
        assert json.loads(events.put_events.call_args.kwargs["Entries"][0]["Detail"])["logs"][0]["stageName"] == \
            "ConversionBatchJob"

    @pytest.mark.parametrize("prefix,bus", [("", "bus"), ("vams.no-pipeline-marker", "bus"),
                                            ("vams.execution.E1.pipeline.P1", "")])
    def test_without_a_pipeline_execution_or_a_bus_nothing_is_registered(self, prefix, bus):
        events = self._register({"jobSize": {"memoryMiB": 61440}}, prefix=prefix, bus=bus)
        events.put_events.assert_not_called()

    def test_a_failed_registration_does_not_fail_the_run(self):
        mod = _load("constructPipeline")
        events = MagicMock()
        events.put_events.side_effect = Exception("throttled")
        with patch.object(mod, "events_client", events), patch.object(mod, "ORCHESTRATION_BUS_NAME", "bus"), \
                patch.object(mod, "BATCH_JOB_LOG_GROUP_NAME", "/g"), \
                patch.object(mod, "BATCH_JOB_DEFINITION_NAME", "Conversion3dBasicJob"):
            result = mod.lambda_handler({"orchestrationEventPrefix": "vams.execution.E1.pipeline.P1",
                                         "conversionRequest": {"workflowExecutionId": "E1"}}, MagicMock())
        assert result["status"] == "STARTING"


@pytest.mark.unit
class TestPipelineEnd:
    def _run(self, event):
        mod = _load("pipelineEnd")
        sfn = MagicMock()
        with patch.object(mod, "sfn", sfn):
            mod.lambda_handler(event, MagicMock())
        return sfn

    def test_a_success_reports_the_status(self):
        sfn = self._run({"externalSfnTaskToken": TOKEN})
        assert json.loads(sfn.send_task_success.call_args.kwargs["output"]) == {"status": "Pipeline Success"}

    def test_an_incomplete_message_from_the_lambda_attempt_reaches_the_task_output(self):
        sfn = self._run({"externalSfnTaskToken": TOKEN,
                         "attempt": {"Payload": {"statusCode": 200, "body": "Converted m.gltf, but 1 file ..."}}})
        output = json.loads(sfn.send_task_success.call_args.kwargs["output"])
        assert output == {"status": "Pipeline Success", "message": "Converted m.gltf, but 1 file ..."}

    @pytest.mark.parametrize("payload", [{"body": "Success"}, {"escalate": True, "body": "x"}, {}])
    def test_no_message_when_there_is_none_to_report(self, payload):
        sfn = self._run({"externalSfnTaskToken": TOKEN, "attempt": {"Payload": payload}})
        assert "message" not in json.loads(sfn.send_task_success.call_args.kwargs["output"])

    def _batch_success(self, document):
        """A run that went through constructPipeline and a Batch state, after an escalated Lambda attempt."""
        mod = _load("pipelineEnd")
        sfn = MagicMock()
        s3 = MagicMock()
        if document is None:
            s3.get_object.side_effect = RuntimeError("NoSuchKey")
        else:
            s3.get_object.return_value = {"Body": MagicMock(read=lambda n=None: json.dumps(document).encode())}
        event = {"externalSfnTaskToken": TOKEN,
                 "outputS3AssetResultsPath": "s3://abkt/pipelines/p/J/output/E1/results/",
                 "attempt": {"Payload": {"escalate": True, "body": "too large"}},
                 "construct": {"conversionEvent": "{}"}}
        with patch.object(mod, "sfn", sfn), patch.object(mod.boto3, "client", return_value=s3):
            mod.lambda_handler(event, MagicMock())
        return json.loads(sfn.send_task_success.call_args.kwargs["output"]), s3

    def test_an_incomplete_message_from_the_batch_job_reaches_the_task_output(self):
        output, s3 = self._batch_success({"tier": "batch", "message": "Converted m.gltf, but 1 file ..."})
        assert output == {"status": "Pipeline Success", "message": "Converted m.gltf, but 1 file ..."}
        assert s3.get_object.call_args.kwargs["Key"] == "pipelines/p/J/output/E1/results/conversion-run.json"

    @pytest.mark.parametrize("document", [{"tier": "batch"}, {"message": "Success"}, None, [],
                                          {"failed": True, "message": "x"}])
    def test_a_complete_batch_run_or_no_document_reports_no_message(self, document):
        output, _ = self._batch_success(document)
        assert output == {"status": "Pipeline Success"}

    def test_a_failure_reports_the_error_text_not_a_pointer_to_the_log(self):
        cause = json.dumps({"errorMessage": "model.step is 1,500.0 MB, over the 1,011.0 MB limit for converting"})
        sfn = self._run({"externalSfnTaskToken": TOKEN, "error": {"Error": "JobSizeRefused", "Cause": cause}})
        arguments = sfn.send_task_failure.call_args.kwargs
        assert arguments["error"] == "Pipeline Failure: JobSizeRefused"
        assert arguments["cause"].startswith("model.step is 1,500.0 MB")
        sfn.send_task_success.assert_not_called()

    def test_a_failure_without_a_cause_points_to_the_log(self):
        sfn = self._run({"externalSfnTaskToken": TOKEN, "error": {"Error": "States.TaskFailed"}})
        assert sfn.send_task_failure.call_args.kwargs["cause"] == "See AWS cloudwatch logs for error cause."

    def test_the_cause_is_cut_to_what_step_functions_accepts(self):
        mod = _load("pipelineEnd")
        assert len(mod.failure_cause({"Cause": "x" * 1000})) == 256

    # --- D1: a failed Batch job reports the container's own message, not Step Functions' raw text -----------
    JOB_CAUSE = json.dumps({
        "JobId": "j-1", "JobName": "PipelineJob_x", "StatusReason": "Essential container in task exited",
        "Attempts": [{"Container": {"ExitCode": 1, "Reason": "OutOfMemoryError: Container killed due to memory usage",
                                    "LogStreamName": "Conversion3dBasicJob/default/abc"},
                      "StatusReason": "Essential container in task exited"}]})

    def _run_with_document(self, document, error=None, results="s3://abkt/pipelines/p/J/output/E1/results/"):
        mod = _load("pipelineEnd")
        sfn = MagicMock()
        s3 = MagicMock()
        if document is None:
            s3.get_object.side_effect = RuntimeError("NoSuchKey")
        else:
            s3.get_object.return_value = {"Body": MagicMock(read=lambda n=None: json.dumps(document).encode())}
        event = {"externalSfnTaskToken": TOKEN, "outputS3AssetResultsPath": results, "outputBucketRegion": "us-east-1",
                 "error": error or {"Error": "States.TaskFailed", "Cause": self.JOB_CAUSE}}
        with patch.object(mod, "sfn", sfn), patch.object(mod.boto3, "client", return_value=s3):
            mod.lambda_handler(event, MagicMock())
        return sfn, s3

    def test_a_failed_job_reports_the_message_the_container_wrote(self):
        sfn, s3 = self._run_with_document({"failed": True, "message": "XMLSyntaxError: Comment too big"})
        arguments = sfn.send_task_failure.call_args.kwargs
        assert arguments["cause"] == "XMLSyntaxError: Comment too big"
        assert arguments["error"] == "Pipeline Failure: States.TaskFailed"
        assert s3.get_object.call_args.kwargs == {"Bucket": "abkt",
                                                  "Key": "pipelines/p/J/output/E1/results/conversion-run.json"}

    @pytest.mark.parametrize("name", ["ValueError", "RuntimeError", "InputTooLargeError", "WorkerKilledForMemory",
                                      "WorkerOutOfTime"])
    def test_the_type_name_of_a_user_facing_error_is_dropped(self, name):
        sfn, _ = self._run_with_document({"failed": True, "message": f"{name}: m.stl needs more memory."})
        assert sfn.send_task_failure.call_args.kwargs["cause"] == "m.stl needs more memory."

    def test_without_the_document_the_job_status_is_summarised_briefly(self):
        sfn, _ = self._run_with_document(None)
        cause = sfn.send_task_failure.call_args.kwargs["cause"]
        assert cause.startswith("The conversion job failed (OutOfMemoryError: Container killed due to memory usage, "
                                "exit code 1).")
        assert "container log" in cause
        assert "Attempts" not in cause and "LogStreamName" not in cause and "{" not in cause

    @pytest.mark.parametrize("document", [{"failed": False, "message": "ok"}, {"failed": True}, {"message": "x"}, []])
    def test_a_document_that_does_not_describe_a_failure_is_not_used(self, document):
        sfn, _ = self._run_with_document(document)
        assert sfn.send_task_failure.call_args.kwargs["cause"].startswith("The conversion job failed")

    def test_a_long_container_message_is_cut(self):
        sfn, _ = self._run_with_document({"failed": True, "message": "x" * 5000})
        assert len(sfn.send_task_failure.call_args.kwargs["cause"]) == 512

    def test_a_lambda_failure_does_not_read_the_document(self):
        cause = json.dumps({"errorMessage": "model.step is 1,500.0 MB, over the limit"})
        sfn, s3 = self._run_with_document({"failed": True, "message": "other"},
                                          error={"Error": "RuntimeError", "Cause": cause})
        s3.get_object.assert_not_called()
        assert sfn.send_task_failure.call_args.kwargs["cause"].startswith("model.step is 1,500.0 MB")

    def test_a_state_timeout_has_a_plain_message(self):
        mod = _load("pipelineEnd")
        assert "did not finish within the time allowed" in mod.failure_cause({"Error": "States.Timeout"})

    def test_the_summary_without_an_exit_code_or_reason_still_points_to_the_log(self):
        mod = _load("pipelineEnd")
        text = mod.failure_cause({"Error": "States.TaskFailed", "Cause": json.dumps({"JobId": "j"})})
        assert text == ("The conversion job failed. See the conversion job container log of this execution for "
                        "the cause.")

    def test_no_token_means_no_callback(self):
        sfn = self._run({"error": {"Error": "x"}})
        sfn.send_task_failure.assert_not_called()
        sfn.send_task_success.assert_not_called()


@pytest.mark.unit
class TestVamsExecute:
    def _body(self, **extra):
        body = {"TaskToken": TOKEN, "inputManifestS3Location": "s3://aux/manifests/E1/m.json",
                "workflowExecutionId": "E1", "inputConfigurationS3Location": "s3://aux/configs/E1/c.json",
                "inputS3AssetFilePath": "s3://abkt/xid/m.step",
                "outputS3AssetFilesPath": "s3://abkt/pipelines/p/J/output/E1/files/",
                "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xid/m.step/preview/p/"}
        body.update(extra)
        return body

    MANIFEST = {
        "schemaVersion": 1,
        "inputFiles": [{"relativePath": "/m.step", "databaseId": "db", "assetId": "xid", "assetRootS3Key": "xid/",
                        "auxPreviewPrefix": "db/xid/m.step/preview", "bucket": "abkt", "key": "xid/m.step",
                        "versionId": "v3"}],
        "outputs": {"bucket": "abkt", "files": "pipelines/p/J/output/E1/files/",
                    "previews": "pipelines/p/J/output/E1/previews/", "metadata": "pipelines/p/J/output/E1/metadata/",
                    "results": "pipelines/p/J/output/E1/results/"},
        "auxBucket": "aux", "auxTempPrefix": "pipelines/p/E1/", "auxPreviewPipelineSuffix": "", "systemConfig": {},
    }

    def _s3(self):
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(self.MANIFEST).encode("utf-8"))}
        return s3

    def _run(self, body):
        mod = _load("vamsExecuteConversion3dBasicPipeline")
        s3 = self._s3()
        invoke = MagicMock(return_value={"StatusCode": 200})
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke):
            response = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        return response, invoke, sfn

    def test_it_forwards_the_manifest_location_and_the_execution_id(self):
        response, invoke, _ = self._run(self._body())
        assert response["statusCode"] == 200
        sent = json.loads(invoke.call_args.kwargs["Payload"])
        assert sent["inputManifestS3Location"] == "s3://aux/manifests/E1/m.json"
        assert sent["workflowExecutionId"] == "E1"
        assert sent["sfnExternalTaskToken"] == TOKEN
        assert sent["inputVersionId"] == "v3"
        assert "TaskToken" not in sent

    def test_a_run_without_a_task_token_is_refused(self):
        body = self._body()
        del body["TaskToken"]
        response, invoke, _ = self._run(body)
        assert response["statusCode"] == 500
        invoke.assert_not_called()

    def test_a_raised_open_pipeline_fails_the_task_token(self):
        mod = _load("vamsExecuteConversion3dBasicPipeline")
        s3 = self._s3()
        sfn = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke",
                             MagicMock(return_value={"StatusCode": 200, "FunctionError": "Unhandled"})):
            response = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert response["statusCode"] == 500
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TOKEN


@pytest.mark.unit
class TestDirectoryContract:
    def test_the_function_names_the_pipeline_registers_start_with_vams(self):
        names = [n for n in os.listdir(_LAMBDA_DIR) if n.startswith("vamsExecute")]
        assert names == ["vamsExecuteConversion3dBasicPipeline.py"]

    @pytest.mark.parametrize("name", ["__init__.py", "customLogging/logger.py", "manifestHelper.py", "openPipeline.py",
                                      "constructPipeline.py", "pipelineEnd.py", "selectJobSize.py", "computeTier.py"])
    def test_the_files_of_the_contract_exist(self, name):
        assert os.path.isfile(os.path.join(_LAMBDA_DIR, name))

    def test_no_launcher_holds_a_states_permission_in_code_beyond_the_callback(self):
        # The Batch job role has no states:SendTask*; only pipelineEnd and the entry/open lambdas answer the token.
        for name in os.listdir(_LAMBDA_DIR):
            if name.endswith(".py") and name not in ("pipelineEnd.py", "openPipeline.py",
                                                      "vamsExecuteConversion3dBasicPipeline.py", "manifestHelper.py"):
                with open(os.path.join(_LAMBDA_DIR, name), encoding="utf-8") as handle:
                    assert "send_task" not in handle.read(), name
