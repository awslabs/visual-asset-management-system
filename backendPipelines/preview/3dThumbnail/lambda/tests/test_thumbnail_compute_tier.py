#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compute-tier selection for the preview/3dThumbnail pipeline.

openPipeline picks the tier before it starts the state machine -- from the run's input configuration,
or the input's size against the large-tier threshold -- passes it in the state machine input, and
registers the container log source of the Batch state that tier routes to. constructPipeline returns
it for the state machine's Choice."""

import datetime
import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:1:stateMachine:Preview3dThumbnail")
os.environ.setdefault("ORCHESTRATION_BUS_NAME", "vams-orchestration")
os.environ.setdefault("STATE_MACHINE_LOG_GROUP_NAME", "/aws/vendedlogs/Preview3dThumbnail")
os.environ.setdefault("STATE_MACHINE_LOG_GROUP_ARN",
                      "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/Preview3dThumbnail:*")

import computeTier  # noqa: E402
from manifestHelper import InputConfigurationError  # noqa: E402

GIB = 1024 ** 3
THRESHOLD = 2 * GIB
INPUT_URI = "s3://abkt/xidM/test/scan.e57"
CONFIG_URI = "s3://abkt/pipelines/run/config.json"


def _s3(size=None, configuration=None, head_error=None):
    """An S3 client double: HeadObject reports `size` (or raises `head_error`); GetObject on the
    configuration location returns `configuration` serialized, or raises when it is None."""
    client = MagicMock()
    if head_error is not None:
        client.head_object.side_effect = head_error
    else:
        client.head_object.return_value = {"ContentLength": size}
    if configuration is None:
        client.get_object.side_effect = Exception("NoSuchKey")
    else:
        body = configuration if isinstance(configuration, str) else json.dumps(configuration)
        client.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body.encode()))}
    return client


def _event(**extra):
    event = {"inputS3AssetFilePath": INPUT_URI, "inputConfigurationS3Location": CONFIG_URI}
    event.update(extra)
    return event


@pytest.mark.unit
class TestThumbnailTierBySize:
    @pytest.mark.parametrize("size, expected", [
        (0, "standard"),
        (THRESHOLD - 1, "standard"),
        (THRESHOLD, "large"),
        (THRESHOLD + 1, "large"),
        (50 * GIB, "large"),
    ])
    def test_threshold_edges(self, size, expected):
        s3 = _s3(size=size, configuration={})
        assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == expected
        s3.head_object.assert_called_once_with(Bucket="abkt", Key="xidM/test/scan.e57")

    def test_auto_in_the_configuration_chooses_by_size(self):
        s3 = _s3(size=THRESHOLD, configuration={"computeTier": "auto", "outputType": "gif"})
        assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == "large"

    def test_head_object_failure_runs_standard_with_a_warning(self):
        s3 = _s3(head_error=Exception("An error occurred (403) when calling the HeadObject operation"),
                 configuration={})
        with patch.object(computeTier, "logger") as logger:
            assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == "standard"
        assert logger.warning.call_count == 1
        assert "standard" in logger.warning.call_args.args[0]

    def test_an_input_path_naming_no_object_runs_standard(self):
        s3 = _s3(size=THRESHOLD * 10, configuration={})
        event = _event(inputS3AssetFilePath="")
        assert computeTier.select_compute_tier(s3, event, THRESHOLD) == "standard"
        s3.head_object.assert_not_called()


@pytest.mark.unit
class TestThumbnailTierOverride:
    def test_configuration_forces_large_for_a_small_input(self):
        s3 = _s3(size=10, configuration={"computeTier": "large"})
        assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == "large"
        s3.head_object.assert_not_called()

    def test_configuration_forces_standard_for_a_large_input(self):
        s3 = _s3(size=THRESHOLD * 4, configuration={"computeTier": "standard"})
        assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == "standard"
        s3.head_object.assert_not_called()

    def test_override_is_case_and_whitespace_insensitive(self):
        s3 = _s3(size=10, configuration={"computeTier": " Large "})
        assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == "large"

    def test_an_unknown_value_falls_back_to_size_with_a_warning(self):
        s3 = _s3(size=10, configuration={"computeTier": "huge"})
        with patch.object(computeTier, "logger") as logger:
            assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == "standard"
        assert logger.warning.call_count == 1
        s3.head_object.assert_called_once()

    @pytest.mark.parametrize("inline", [{"computeTier": "large"}, '{"computeTier": "large"}'])
    def test_a_compute_tier_on_the_invoke_event_does_not_choose_the_tier(self, inline):
        """The input configuration is the one place a run sets computeTier, the same file the container
        reads its parameters from. vamsExecute forwards only that file's location, so an
        inputParameters field on the event is not read."""
        s3 = _s3(size=10, configuration={"computeTier": "auto"})
        event = _event(inputParameters=inline)
        assert computeTier.select_compute_tier(s3, event, THRESHOLD) == "standard"
        s3.head_object.assert_called_once()

    def test_a_run_without_an_input_configuration_chooses_by_size(self):
        s3 = _s3(size=THRESHOLD, configuration=None)
        event = _event(inputConfigurationS3Location="")
        assert computeTier.select_compute_tier(s3, event, THRESHOLD) == "large"
        s3.get_object.assert_not_called()

    def test_a_malformed_configuration_falls_back_to_size(self):
        """The container reports a malformed configuration as the run's failure; tier selection
        must not fail the launch ahead of it."""
        s3 = _s3(size=THRESHOLD, configuration="[1, 2]")
        with pytest.raises(InputConfigurationError):
            import manifestHelper
            manifestHelper.fetch_input_configuration(s3, CONFIG_URI)
        assert computeTier.select_compute_tier(s3, _event(), THRESHOLD) == "large"


REMOTE_REGION = "eu-west-1"
REMOTE_INPUT_URI = "s3://remote-bkt/xidM/test/scan.e57"


def _cross_region_head_error():
    """What a deployment-Region client gets from HeadObject on a bucket in another Region: a HEAD
    response carries no body, so the redirect surfaces as a bare 400."""
    return Exception("An error occurred (400) when calling the HeadObject operation: Bad Request")


def _clients_by_region(clients):
    """A stand-in for manifestHelper.s3_client_for_region that returns the double for each Region."""
    return MagicMock(side_effect=lambda region="": clients[region])


@pytest.mark.unit
class TestThumbnailTierReadsEachBucketInItsRegion:
    """An asset bucket may be in another Region than this function. The event names the input and
    output buckets' Regions (inputBucketRegion / outputBucketRegion, resolved from the manifest by
    vamsExecute); the HeadObject that sizes the input, and the read of the input configuration, are
    signed with manifestHelper's client for the Region of the bucket they address, the way the other
    pipeline lambdas address asset buckets. A deployment-Region client fails on a bucket in another
    Region, which would put every cross-Region input on the standard tier."""

    def test_a_cross_region_input_is_sized_with_a_client_for_its_region(self):
        default = _s3(head_error=_cross_region_head_error(), configuration={})
        remote = _s3(size=THRESHOLD)
        regional = _clients_by_region({REMOTE_REGION: remote})
        event = _event(inputS3AssetFilePath=REMOTE_INPUT_URI, inputBucketRegion=REMOTE_REGION)
        with patch.object(computeTier.manifestHelper, "s3_client_for_region", regional):
            assert computeTier.select_compute_tier(default, event, THRESHOLD) == "large"
        remote.head_object.assert_called_once_with(Bucket="remote-bkt", Key="xidM/test/scan.e57")
        default.head_object.assert_not_called()
        regional.assert_called_once_with(REMOTE_REGION)

    def test_the_configuration_in_a_cross_region_bucket_is_read_in_its_region(self):
        default = _s3(size=10, configuration=None)
        remote = _s3(size=10, configuration={"computeTier": "large"})
        regional = _clients_by_region({REMOTE_REGION: remote})
        event = _event(inputConfigurationS3Location="s3://run-bkt/pipelines/run/config.json",
                       outputS3AssetFilesPath="s3://run-bkt/pipelines/p1/MJOB/output/E1/files/",
                       outputBucketRegion=REMOTE_REGION)
        with patch.object(computeTier.manifestHelper, "s3_client_for_region", regional):
            assert computeTier.select_compute_tier(default, event, THRESHOLD) == "large"
        remote.get_object.assert_called_once_with(Bucket="run-bkt", Key="pipelines/run/config.json")
        default.get_object.assert_not_called()

    def test_deployment_region_buckets_use_the_default_client(self):
        """An empty Region (a deployment-Region bucket, or an event from an older vamsExecute) builds
        no regional client."""
        default = _s3(size=THRESHOLD, configuration={})
        regional = MagicMock()
        for event in (_event(), _event(inputBucketRegion="", outputBucketRegion="")):
            default.reset_mock()
            with patch.object(computeTier.manifestHelper, "s3_client_for_region", regional):
                assert computeTier.select_compute_tier(default, event, THRESHOLD) == "large"
            default.head_object.assert_called_once_with(Bucket="abkt", Key="xidM/test/scan.e57")
            default.get_object.assert_called_once()
        regional.assert_not_called()

    def test_a_regional_client_that_cannot_be_built_runs_standard(self):
        default = _s3(size=THRESHOLD, configuration={})
        regional = MagicMock(side_effect=Exception("Provided region_name 'x y' doesn't match"))
        event = _event(inputS3AssetFilePath=REMOTE_INPUT_URI, inputBucketRegion="x y")
        with patch.object(computeTier.manifestHelper, "s3_client_for_region", regional):
            assert computeTier.select_compute_tier(default, event, THRESHOLD) == "standard"
        default.head_object.assert_not_called()

    def test_bucket_regions_maps_the_input_and_output_buckets(self):
        event = _event(inputS3AssetFilePath=REMOTE_INPUT_URI, inputBucketRegion=REMOTE_REGION,
                       outputS3AssetFilesPath="s3://out-bkt/pipelines/p1/files/",
                       outputBucketRegion="us-west-2")
        assert computeTier.bucket_regions(event) == {"remote-bkt": REMOTE_REGION, "out-bkt": "us-west-2"}
        assert computeTier.bucket_regions(_event(outputBucketRegion="us-west-2")) == {}
        assert computeTier.bucket_regions(_event(
            inputBucketRegion=None, outputS3AssetFilesPath="s3://out-bkt/files/", outputBucketRegion=7)) == {}
        assert computeTier.bucket_regions(None) == {}


@pytest.mark.unit
class TestThumbnailTierThresholdEnv:
    @pytest.mark.parametrize("raw, expected", [
        ("1073741824", GIB),
        ("", computeTier.DEFAULT_LARGE_TIER_THRESHOLD_BYTES),
        ("abc", computeTier.DEFAULT_LARGE_TIER_THRESHOLD_BYTES),
        ("0", computeTier.DEFAULT_LARGE_TIER_THRESHOLD_BYTES),
        ("-5", computeTier.DEFAULT_LARGE_TIER_THRESHOLD_BYTES),
    ])
    def test_threshold_from_value(self, raw, expected):
        assert computeTier.large_tier_threshold_bytes(raw) == expected

    def test_threshold_from_environment(self):
        with patch.dict(os.environ, {"LARGE_TIER_THRESHOLD_BYTES": str(5 * GIB)}):
            assert computeTier.large_tier_threshold_bytes() == 5 * GIB

    def test_default_is_two_gib(self):
        assert computeTier.DEFAULT_LARGE_TIER_THRESHOLD_BYTES == 2 * GIB


@pytest.mark.unit
class TestThumbnailOpenPipelineTier:
    # Passed on every load rather than relied on from the module-level setdefault: another suite in the
    # same process may already have set these, and openPipeline reads them at import.
    _ENV = {
        "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.e57",
        "BATCH_JOB_LOG_GROUP_NAME": "/aws/vendedlogs/Pipelines/Preview3dThumbnailabc",
        "BATCH_JOB_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:123456789012:log-group:"
                                   "/aws/vendedlogs/Pipelines/Preview3dThumbnailabc",
        "BATCH_JOB_DEFINITION_NAME": "Preview3dThumbnailJobDefabc1234567",
    }

    def _load_module(self, threshold=None):
        env = dict(self._ENV)
        if threshold is not None:
            env["LARGE_TIER_THRESHOLD_BYTES"] = str(threshold)
        with patch.dict(os.environ, env):
            if "openPipeline" in sys.modules:
                return importlib.reload(sys.modules["openPipeline"])
            return importlib.import_module("openPipeline")

    def _event(self):
        return {
            "inputS3AssetFilePath": INPUT_URI,
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
            "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/scan.e57/preview/p1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": CONFIG_URI,
            "sfnExternalTaskToken": "tok-123",
            "assetId": "xidM",
            "databaseId": "dbM",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _run(self, mod, s3, event=None):
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:Preview3dThumbnail:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        put_events = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            resp = mod.lambda_handler(event or self._event(), MagicMock())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
        return sfn_input, detail

    def test_both_batch_states_are_declared_module_level_literals(self):
        mod = self._load_module()
        assert mod.STANDARD_BATCH_STATE_NAME == "Preview3dThumbnailBatchJob"
        assert mod.LARGE_BATCH_STATE_NAME == "Preview3dThumbnailLargeBatchJob"
        assert set(mod.BATCH_STATE_NAME_BY_TIER) == set(computeTier.TIERS)

    def test_threshold_is_read_from_the_environment(self):
        mod = self._load_module(threshold=5 * GIB)
        assert mod.LARGE_TIER_THRESHOLD_BYTES == 5 * GIB

    def test_a_small_input_runs_standard_and_registers_the_standard_state(self):
        mod = self._load_module(threshold=THRESHOLD)
        sfn_input, detail = self._run(mod, _s3(size=THRESHOLD - 1, configuration={}))
        assert sfn_input["computeTier"] == "standard"
        batch_logs = [log for log in detail["logs"] if log["sourceType"] == "batch"]
        assert [log["stageName"] for log in batch_logs] == ["Preview3dThumbnailBatchJob"]

    def test_a_large_input_runs_large_and_registers_the_large_state_only(self):
        """Both states submit one job definition, so their containers share one stream prefix and the
        registration handler stores one entry per location: registering both would keep only the first
        stage name, and a large run's container log would be joined to the wrong state."""
        mod = self._load_module(threshold=THRESHOLD)
        sfn_input, detail = self._run(mod, _s3(size=THRESHOLD, configuration={}))
        assert sfn_input["computeTier"] == "large"
        batch_logs = [log for log in detail["logs"] if log["sourceType"] == "batch"]
        assert batch_logs == [{
            "logGroupArn": self._ENV["BATCH_JOB_LOG_GROUP_ARN"],
            "logGroupName": self._ENV["BATCH_JOB_LOG_GROUP_NAME"],
            "logStreamName": "",
            "logStreamPrefix": "Preview3dThumbnailJobDefabc1234567/default/",
            "stageName": "Preview3dThumbnailLargeBatchJob",
            "sourceType": "batch",
            "label": "Preview3dThumbnailLargeBatchJob container",
        }]

    def test_template_override_reaches_the_state_machine_input(self):
        mod = self._load_module(threshold=THRESHOLD)
        sfn_input, detail = self._run(mod, _s3(size=10, configuration={"computeTier": "large"}))
        assert sfn_input["computeTier"] == "large"
        assert [log["stageName"] for log in detail["logs"] if log["sourceType"] == "batch"] == [
            "Preview3dThumbnailLargeBatchJob"]

    def test_head_object_failure_still_starts_on_standard(self):
        mod = self._load_module(threshold=THRESHOLD)
        sfn_input, _ = self._run(mod, _s3(head_error=Exception("AccessDenied"), configuration={}))
        assert sfn_input["computeTier"] == "standard"

    def test_a_large_input_in_a_cross_region_bucket_runs_large(self):
        mod = self._load_module(threshold=THRESHOLD)
        remote = _s3(size=THRESHOLD)
        regional = _clients_by_region({REMOTE_REGION: remote})
        event = dict(self._event(), inputS3AssetFilePath=REMOTE_INPUT_URI, inputBucketRegion=REMOTE_REGION)
        with patch.object(mod.computeTier.manifestHelper, "s3_client_for_region", regional):
            sfn_input, detail = self._run(
                mod, _s3(head_error=_cross_region_head_error(), configuration={}), event)
        assert sfn_input["computeTier"] == "large"
        assert sfn_input["inputBucketRegion"] == REMOTE_REGION
        remote.head_object.assert_called_once_with(Bucket="remote-bkt", Key="xidM/test/scan.e57")
        assert [log["stageName"] for log in detail["logs"] if log["sourceType"] == "batch"] == [
            "Preview3dThumbnailLargeBatchJob"]

    def test_an_unsupported_extension_is_rejected_before_any_s3_call(self):
        mod = self._load_module(threshold=THRESHOLD)
        s3 = _s3(size=10, configuration={})
        event = self._event()
        event["inputS3AssetFilePath"] = "s3://abkt/xidM/test/readme.txt"
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn, "start_execution", MagicMock()) as start, \
                patch.object(mod.sfn, "send_task_failure", MagicMock()):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 400
        start.assert_not_called()
        s3.head_object.assert_not_called()


@pytest.mark.unit
class TestThumbnailConstructPipelineTier:
    def _load_module(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def _event(self, **extra):
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": INPUT_URI,
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "inputConfigurationS3Location": CONFIG_URI,
            "externalSfnTaskToken": "tok",
            "assetId": "xidM",
        }
        event.update(extra)
        return event

    @pytest.mark.parametrize("tier", ["standard", "large"])
    def test_returns_the_tier_the_state_machine_input_carries(self, tier):
        out = self._load_module().lambda_handler(self._event(computeTier=tier), MagicMock())
        assert out["computeTier"] == tier

    @pytest.mark.parametrize("value", [None, "", "auto", "huge", 7])
    def test_a_missing_or_unknown_tier_routes_to_standard(self, value):
        event = self._event() if value is None else self._event(computeTier=value)
        out = self._load_module().lambda_handler(event, MagicMock())
        assert out["computeTier"] == "standard"

    def test_the_tier_stays_out_of_the_container_definition(self):
        """The definition is the container's PipelineDefinition(**kwargs); a key it does not declare
        fails the job at container start. The tier reaches the container as VAMS_COMPUTE_TIER."""
        out = self._load_module().lambda_handler(self._event(computeTier="large"), MagicMock())
        assert "computeTier" not in json.loads(out["definition"][0])
