#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the constructPipeline payload and its failure path.

Two properties are pinned here:

* **Payload shape** — no asset metadata content is forwarded (this pipeline reads none), and the
  conversion configuration appears exactly once, in the one field the container reads. The definition is carried in a Batch command override, so every duplicated blob counts
  twice against a size limit. The container's own dataclasses are loaded and fed the emitted
  definition, so the reduction cannot silently break the lambda -> container contract.
* **Failure path** — this lambda runs the FIRST state of the pipeline's state machine, so a failure
  here is reported against the external VAMS task token; otherwise the workflow task stays RUNNING
  for its whole 4-hour taskTimeout.
"""

import os
import sys
import json
import types
import importlib
import importlib.util
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

for k, v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}.items():
    os.environ.setdefault(k, v)

# A value that appears only inside the asset metadata, so its presence anywhere in the emitted
# payload means the metadata content was forwarded.
_METADATA_ONLY_VALUE = "Site scan"


def _load():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


def _container_objects():
    """The container's pipeline dataclasses, loaded from their file.

    Loaded by path rather than imported as a package: the container is a separate Docker build
    context and is not on the lambda's import path, but its dataclasses are the consumer of the
    definition this lambda emits."""
    path = os.path.normpath(os.path.join(
        _LAMBDA_DIR, "..", "container", "cog_convert_pipeline", "utils", "pipeline",
        "objects.py"))
    assert os.path.isfile(path), path
    spec = importlib.util.spec_from_file_location("cogConvertContainerObjects", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(**overrides):
    event = {
        "jobName": "CogConvert_x",
        "inputS3AssetFilePath": "s3://abkt/xidC/maps/scan.tif",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/CJOB/output/E1/files/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/CJOB/output/E1/metadata/",
        "outputS3AssetResultsPath": "s3://abkt/pipelines/p1/CJOB/output/E1/results/",
        "assetId": "xidC",
        "databaseId": "dbC",
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
        "externalSfnTaskToken": "tok-123",
        "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
    }
    event.update(overrides)
    return event


def _s3_with_metadata():
    """An s3 stub returning a config file and a grouped-by-asset metadata envelope whose asset
    metadata overrides targetCrs and carries some bulk of its own."""
    bodies = {
        "pipelines/workflowExecutionInputs/E1/pipeline1/config.json": {
            "compression": "deflate"},
        "pipelines/workflowExecutionInputs/E1/metadata.json": {
            "schemaVersion": 2,
            "assets": [{
                "databaseId": "dbC", "assetId": "xidC",
                "assetData": {"assetName": "Site scan", "description": "x" * 500},
                "files": [
                    {"fileKey": "/", "metadata": {"compression": "JPEG",
                                                  "surveyNotes": "y" * 500}},
                    {"fileKey": "/maps/scan.tif", "metadata": {"captureDate": "2026-01-01"},
                     "attributes": {}},
                ],
            }],
        },
    }

    def get_object(Bucket, Key):
        return {"Body": MagicMock(read=lambda: json.dumps(bodies[Key]).encode("utf-8"))}

    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    return s3


def _run(mod, event=None, s3=None):
    with patch.object(mod, "s3", s3 or _s3_with_metadata()):
        return mod.lambda_handler(event or _event(), MagicMock())


@pytest.mark.unit
class TestPayloadCarriesNoMetadataContent:
    def test_definition_carries_no_metadata_content(self):
        mod = _load()
        definition_json = _run(mod)["definition"][0]
        definition = json.loads(definition_json)
        assert definition["inputMetadata"] == ""
        assert "surveyNotes" not in definition_json
        assert _METADATA_ONLY_VALUE not in definition_json

    def test_asset_metadata_does_not_change_the_conversion(self):
        """A metadata key named like a setting is ignored: only the template's configuration feeds it."""
        mod = _load()
        params = _emitted_params(_run(mod))
        assert params == {"compression": "DEFLATE"}

    def test_top_level_payload_does_not_duplicate_the_blobs(self):
        mod = _load()
        result = _run(mod)
        assert "inputMetadata" not in result
        assert "inputParameters" not in result

    def test_the_conversion_configuration_appears_exactly_once(self):
        mod = _load()
        definition_json = _run(mod)["definition"][0]
        assert definition_json.count("compression") == 1

    def test_fields_the_state_machine_reads_are_still_emitted(self):
        """This task's outputPath is $.Payload, which REPLACES the state, so anything a later state
        reads has to be re-emitted here: pipelineEnd reads the token, the batch task reads the
        event prefix, jobName and definition."""
        mod = _load()
        result = _run(mod)
        assert result["externalSfnTaskToken"] == "tok-123"
        assert result["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert result["jobName"] == "CogConvert_x"
        assert isinstance(result["definition"], list) and len(result["definition"]) == 1

    def test_definition_satisfies_the_container_dataclasses(self):
        """The emitted definition is what the container instantiates. PipelineDefinition and
        PipelineStage are plain dataclasses, so a dropped required key or an added unknown key is a
        TypeError at container start.

        assetId is asserted rather than merely accepted: the container locates the input file's
        subdirectory within the asset by finding that id in the object key, so it decides the output
        S3 key layout. An empty value here silently collapses outputs to the asset root."""
        objects = _container_objects()
        mod = _load()
        definition = json.loads(_run(mod)["definition"][0])
        stages = definition["stages"]
        container_definition = objects.PipelineDefinition(**definition)
        assert container_definition.assetId == "xidC"
        assert container_definition.databaseId == "dbC"
        stage = objects.PipelineStage(**stages[0])
        assert stage.type == "COG_CONVERT"
        assert stage.inputFile["objectKey"] == "xidC/maps/scan.tif"
        # The results folder travels to the container, so a skipped run can leave its note there.
        assert stage.outputResults["objectDir"] == "pipelines/p1/CJOB/output/E1/results/"


@pytest.mark.unit
class TestFailurePathReportsTheExternalToken:
    def _s3_unused(self):
        s3 = MagicMock()
        s3.get_object.side_effect = AssertionError("should fail before reading S3")
        return s3

    @pytest.mark.parametrize("field", ["inputS3AssetFilePath", "outputS3AssetFilesPath"])
    def test_empty_required_path_fails_the_task_token(self, field):
        mod = _load()
        send_failure = MagicMock()
        with patch.object(mod, "s3", self._s3_unused()), \
                patch.object(mod.sfn, "send_task_failure", send_failure), \
                pytest.raises(ValueError):
            mod.lambda_handler(_event(**{field: ""}), MagicMock())
        assert send_failure.call_count == 1
        kwargs = send_failure.call_args.kwargs
        assert kwargs["taskToken"] == "tok-123"
        # The cause names the field, so the execution record says which path was empty.
        assert field in kwargs["cause"]
        assert len(kwargs["cause"]) <= 256

    @pytest.mark.parametrize("field", ["inputS3AssetFilePath", "outputS3AssetFilesPath"])
    def test_absent_required_path_fails_the_task_token(self, field):
        mod = _load()
        event = _event()
        del event[field]
        send_failure = MagicMock()
        with patch.object(mod, "s3", self._s3_unused()), \
                patch.object(mod.sfn, "send_task_failure", send_failure), \
                pytest.raises(ValueError):
            mod.lambda_handler(event, MagicMock())
        assert send_failure.call_count == 1

    def test_error_still_propagates_so_the_state_machine_fails(self):
        """The token is reported BEFORE the error is re-raised, so the sub-state-machine execution
        still records the failure rather than reporting a success it did not have."""
        mod = _load()
        with patch.object(mod, "s3", self._s3_unused()), \
                patch.object(mod.sfn, "send_task_failure", MagicMock()), \
                pytest.raises(ValueError):
            mod.lambda_handler(_event(outputS3AssetFilesPath=""), MagicMock())

    def test_no_token_means_no_callback_attempted(self):
        """A direct invoke carries no token; the failure must not turn into a botocore
        ParamValidationError inside the callback helper."""
        mod = _load()
        send_failure = MagicMock()
        with patch.object(mod, "s3", self._s3_unused()), \
                patch.object(mod.sfn, "send_task_failure", send_failure), \
                pytest.raises(ValueError):
            mod.lambda_handler(_event(outputS3AssetFilesPath="", externalSfnTaskToken=""),
                               MagicMock())
        send_failure.assert_not_called()

    def test_callback_denial_does_not_mask_the_original_error(self):
        """Until the builder grants states:SendTaskFailure on this function the callback raises
        AccessDeniedException; the original cause must still be what surfaces."""
        mod = _load()
        denied = MagicMock(side_effect=Exception("AccessDeniedException"))
        with patch.object(mod, "s3", self._s3_unused()), \
                patch.object(mod.sfn, "send_task_failure", denied), \
                pytest.raises(ValueError) as excinfo:
            mod.lambda_handler(_event(outputS3AssetFilesPath=""), MagicMock())
        assert "outputS3AssetFilesPath" in str(excinfo.value)

    def test_a_downstream_failure_also_fails_the_task_token(self):
        """Not only the path validation: any exception raised while building the definition is
        reported, because nothing later in this state machine can report on the token."""
        mod = _load()
        send_failure = MagicMock()
        boom = MagicMock(side_effect=RuntimeError("kms key disabled"))
        with patch.object(mod, "s3", MagicMock()), \
                patch.object(mod.manifestHelper, "fetch_input_configuration", boom), \
                patch.object(mod.sfn, "send_task_failure", send_failure), \
                pytest.raises(RuntimeError):
            mod.lambda_handler(_event(), MagicMock())
        assert send_failure.call_count == 1
        assert "kms key disabled" in send_failure.call_args.kwargs["cause"]


def _s3_with(config_body):
    """An s3 stub serving one template config body."""
    bodies = {"pipelines/workflowExecutionInputs/E1/pipeline1/config.json": config_body}

    def get_object(Bucket, Key):
        return {"Body": MagicMock(read=lambda: json.dumps(bodies[Key]).encode("utf-8"))}

    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    return s3


def _emitted_params(payload):
    """The parameter set the emitted definition carries (a JSON string nested in the definition)."""
    return json.loads(json.loads(payload["definition"][0])["inputParameters"])


@pytest.mark.unit
class TestConversionParameters:
    def test_no_configuration_gives_the_defaults(self):
        mod = _load()
        assert _emitted_params(_run(mod, s3=_s3_with({}))) == {"compression": "DEFLATE"}

    def test_an_allowed_compression_is_forwarded_upper_cased(self):
        mod = _load()
        assert _emitted_params(_run(mod, s3=_s3_with({"compression": "deflate"})))["compression"] == "DEFLATE"

    @pytest.mark.parametrize("value", ["JPEG", "ZSTD", "lzw", ""])
    def test_a_compression_outside_the_allowed_set_is_refused_and_the_token_failed(self, value):
        mod = _load()
        send_failure = MagicMock()
        with patch.object(mod.sfn, "send_task_failure", send_failure), pytest.raises(ValueError):
            _run(mod, s3=_s3_with({"compression": value}))
        assert send_failure.call_count == 1
        assert "compression" in send_failure.call_args.kwargs["cause"]

    def test_unknown_keys_are_not_forwarded(self):
        mod = _load()
        params = _emitted_params(_run(mod, s3=_s3_with({"compression": "DEFLATE", "extraArgs": "-rm"})))
        assert params == {"compression": "DEFLATE"}
