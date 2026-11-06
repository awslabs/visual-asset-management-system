#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""zipArchive pipeline Lambdas: the mode decision from the run manifest (only VAMS's server-only system
execution context makes a run a zip download), the execution id the container conditions its status
writes on, the definition the container receives, and the vamsSchema bundle."""

import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_DIR = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1",
               "OPEN_PIPELINE_FUNCTION_NAME": "openFn", "STATE_MACHINE_ARN": "sm"}.items():
    os.environ.setdefault(_k, _v)

with patch("boto3.client", return_value=MagicMock()):
    vams_execute = importlib.import_module("vamsExecuteZipArchivePipeline")
    construct = importlib.import_module("constructPipeline")
    open_pipeline = importlib.import_module("openPipeline")

pytestmark = pytest.mark.unit

CONTEXT = {"v": 1, "requestId": "r1", "userId": "alice", "databaseId": "db1", "assetId": "asset1",
           "manifestKey": "vams.preparedDownloads/db1/asset1/h/r1/manifest.json"}
PREFIX = "vams.workflow.execution.exec-123.pipeline.pex-9"


def _resolved():
    return {"orchestrationEventPrefix": PREFIX, "outputS3AssetFilesPath": "s3://b/pipelines/out/files/",
            "assetId": "asset1", "databaseId": "db1", "inputConfigurationS3Location": "s3://b/cfg.json",
            "inputBucketRegion": "", "outputBucketRegion": ""}


class TestModeDecision:
    def test_zip_download_from_system_context(self):
        payload = vams_execute.build_open_payload(
            {"inputManifestS3Location": "s3://b/m.json"},
            {"systemExecutionContext": CONTEXT, "auxBucket": "aux"}, _resolved(), "tok")
        assert payload["mode"] == "zipDownload"
        assert payload["zipDownload"]["manifestBucket"] == "aux"
        assert payload["workflowExecutionId"] == "exec-123"
        assert "v" not in payload["zipDownload"]

    def test_ordinary_run_is_an_archive_run(self):
        payload = vams_execute.build_open_payload({}, {"auxBucket": "aux"}, _resolved(), "tok")
        assert payload["mode"] == "archiveRun" and "zipDownload" not in payload

    def test_incomplete_context_refused(self):
        with pytest.raises(ValueError):
            vams_execute.zip_download_context({"systemExecutionContext": dict(CONTEXT, userId="")})

    def test_manifest_outside_prepared_downloads_refused(self):
        with pytest.raises(ValueError):
            vams_execute.zip_download_context(
                {"systemExecutionContext": dict(CONTEXT, manifestKey="db1/secret.json")})

    @pytest.mark.parametrize("prefix,expected", [
        (PREFIX, "exec-123"), ("", ""), ("garbage", ""), ("a.execution.x.y", "")])
    def test_execution_id_from_prefix(self, prefix, expected):
        assert vams_execute.workflow_execution_id_from_event_prefix(prefix) == expected


class TestConstruct:
    def test_zip_definition_is_small_and_complete(self):
        event = {"jobName": "j", "mode": "zipDownload", "workflowExecutionId": "exec-123",
                 "zipDownload": dict(CONTEXT, manifestBucket="aux")}
        definition = construct.build_definition(event)
        assert set(definition["zipDownload"]) == {"manifestBucket", "manifestKey", "requestId", "userId",
                                                  "databaseId", "assetId"}
        assert len(json.dumps(definition)) < 2048

    def test_zip_definition_missing_field_refused(self):
        with pytest.raises(ValueError):
            construct.build_definition({"mode": "zipDownload", "zipDownload": {"manifestBucket": "aux"}})

    def test_archive_run_definition(self):
        with patch.object(construct.manifestHelper, "fetch_input_configuration",
                          return_value={"format": "tgz", "excludePatterns": ["*.tmp"]}):
            definition = construct.build_definition({
                "mode": "archiveRun", "runManifestS3Location": "s3://b/pipelines/run/manifest.json",
                "outputS3AssetFilesPath": "s3://b/pipelines/out/files/", "assetId": "asset1",
                "databaseId": "db1", "inputBucketRegion": "eu-west-1"})
        run = definition["archiveRun"]
        assert run["runManifest"] == {"bucket": "b", "key": "pipelines/run/manifest.json"}
        assert run["outputDir"] == "pipelines/out/files/" and run["config"]["format"] == "tgz"
        assert definition["bucketRegions"] == {"b": "eu-west-1"}

    @pytest.mark.parametrize("config", [
        {"format": "rar"}, {"compression": "lzma"}, {"includePatterns": "*.glb"}, {"typo": 1}])
    def test_bad_archive_config_refused(self, config):
        with pytest.raises(ValueError):
            construct.archive_config(config)

    def test_unknown_mode_refused(self):
        with pytest.raises(ValueError):
            construct.build_definition({"mode": "other"})


class TestOpen:
    def test_unknown_mode_fails_the_task(self):
        with patch.object(open_pipeline, "abort_external_workflow") as abort:
            response = open_pipeline.lambda_handler({"mode": "x", "sfnExternalTaskToken": "t"}, None)
        assert response["statusCode"] == 400
        abort.assert_called_once()

    def test_state_machine_input_carries_only_known_fields(self):
        sfn = MagicMock()
        from datetime import datetime
        sfn.start_execution.return_value = {"executionArn": "arn", "startDate": datetime(2026, 1, 1)}
        with patch.object(open_pipeline, "sfn", sfn):
            open_pipeline.lambda_handler({"mode": "zipDownload", "zipDownload": {"a": 1}, "junk": "x",
                                          "sfnExternalTaskToken": "t"}, None)
        sent = json.loads(sfn.start_execution.call_args.kwargs["input"])
        assert "junk" not in sent and sent["externalSfnTaskToken"] == "t" and sent["mode"] == "zipDownload"


class TestOpenPipelineInvokeIsSentOnce:
    """Each openPipeline call starts a new archive run on the task token, so it is never repeated
    (the #1205 rule, #1125 in-dev D8); only a throttle the function never ran for is retried."""

    def _throttle(self):
        from botocore.exceptions import ClientError
        return ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "Rate"}}, "Invoke")

    def test_client_config(self):
        assert vams_execute.invoke_config.retries["total_max_attempts"] == 1
        # Above openPipeline's 300 s (5 minute) timeout.
        assert vams_execute.invoke_config.read_timeout == vams_execute.OPEN_PIPELINE_READ_TIMEOUT_SECONDS > 300

    def test_a_timed_out_invoke_is_not_repeated(self):
        from botocore.exceptions import ReadTimeoutError
        lam = MagicMock()
        lam.invoke.side_effect = ReadTimeoutError(endpoint_url="https://lambda")
        with patch.object(vams_execute, "lambda_client", lam), pytest.raises(ReadTimeoutError):
            vams_execute.invoke_open_pipeline({"mode": "archiveRun"})
        lam.invoke.assert_called_once()
        assert lam.invoke.call_args.kwargs["InvocationType"] == "RequestResponse"

    def test_a_throttle_is_retried_within_the_bound(self):
        lam = MagicMock()
        lam.invoke.side_effect = [self._throttle(), {"StatusCode": 200}]
        with patch.object(vams_execute, "lambda_client", lam), patch.object(vams_execute.time, "sleep"):
            assert vams_execute.invoke_open_pipeline({}) == {"StatusCode": 200}
        assert lam.invoke.call_count == 2
        lam.invoke.reset_mock()
        lam.invoke.side_effect = self._throttle()
        from botocore.exceptions import ClientError
        with patch.object(vams_execute, "lambda_client", lam), patch.object(vams_execute.time, "sleep"), \
                pytest.raises(ClientError):
            vams_execute.invoke_open_pipeline({})
        assert lam.invoke.call_count == vams_execute.OPEN_PIPELINE_THROTTLE_ATTEMPTS


class TestBundle:
    def _load(self, *parts):
        with open(os.path.join(_SCHEMA_DIR, *parts), encoding="utf-8") as handle:
            return json.load(handle)

    def test_pipeline_and_workflow_are_system_records(self):
        assert self._load("pipeline.json")["isSystem"] is True
        assert self._load("workflow.json")["isSystem"] is True

    def test_execution_settings(self):
        pipeline = self._load("pipeline.json")
        assert pipeline["executionConfig"]["waitForCallback"] == "Enabled"
        assert pipeline["executionConfig"]["taskTimeout"] == "43200"
        assert pipeline["systemConfig"]["inputFileArity"] == "multi"
        assert pipeline["systemConfig"]["assetScope"]["singleAssetOnly"] is True

    def test_templates_parse_as_archive_config(self):
        template_dir = os.path.join(_SCHEMA_DIR, "templates")
        for name in os.listdir(template_dir):
            template = self._load("templates", name)
            construct.archive_config(json.loads(template["configBody"]))

    def test_no_file_upload_trigger(self):
        """A system archive must never fire on every upload."""
        assert not self._load("workflow.json").get("triggers")
