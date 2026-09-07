#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the constructPipeline payload and its failure path.

Two properties are pinned here:

* **Payload shape** — the resolved metadata content is not forwarded (only its S3 location ever
  travels), and the transform configuration appears exactly once, in the one field the container
  reads. The definition is carried in a Batch command override, so every duplicated blob counts
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
_METADATA_ONLY_VALUE = "EPSG:3857"


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
        _LAMBDA_DIR, "..", "container", "coord_transform_pipeline", "utils", "pipeline",
        "objects.py"))
    assert os.path.isfile(path), path
    spec = importlib.util.spec_from_file_location("coordTransformContainerObjects", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(**overrides):
    event = {
        "jobName": "CoordXform_x",
        "inputS3AssetFilePath": "s3://abkt/xidC/scan.e57",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/CJOB/output/E1/files/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/CJOB/output/E1/metadata/",
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
            "sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700", "outputFormats": ["laz"]},
        "pipelines/workflowExecutionInputs/E1/metadata.json": {
            "schemaVersion": 2,
            "assets": [{
                "databaseId": "dbC", "assetId": "xidC",
                "assetData": {"assetName": "Site scan", "description": "x" * 500},
                "files": [
                    {"fileKey": "/", "metadata": {"targetCrs": _METADATA_ONLY_VALUE,
                                                  "surveyNotes": "y" * 500}},
                    {"fileKey": "/scan.e57", "metadata": {"captureDate": "2026-01-01"},
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
        result = _run(mod)
        definition_json = result["definition"][0]
        definition = json.loads(definition_json)
        # The metadata override still reaches the container -- through the parameters.
        assert json.loads(definition["inputParameters"])["targetCrs"] == _METADATA_ONLY_VALUE
        # ...but the metadata document itself does not travel.
        assert definition["inputMetadata"] == ""
        assert "surveyNotes" not in definition_json
        assert "Site scan" not in definition_json

    def test_top_level_payload_does_not_duplicate_the_blobs(self):
        mod = _load()
        result = _run(mod)
        assert "inputMetadata" not in result
        assert "inputParameters" not in result

    def test_transform_configuration_appears_exactly_once(self):
        mod = _load()
        result = _run(mod)
        definition_json = result["definition"][0]
        parameters = json.loads(definition_json)["inputParameters"]
        # sourceCrs comes only from the configuration, so counting it counts the copies of the
        # configuration in the payload the Batch command override carries.
        assert parameters.count("sourceCrs") == 1
        assert definition_json.count("sourceCrs") == 1

    def test_fields_the_state_machine_reads_are_still_emitted(self):
        """This task's outputPath is $.Payload, which REPLACES the state, so anything a later state
        reads has to be re-emitted here: pipelineEnd reads the token, the batch task reads the
        event prefix, jobName and definition."""
        mod = _load()
        result = _run(mod)
        assert result["externalSfnTaskToken"] == "tok-123"
        assert result["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert result["jobName"] == "CoordXform_x"
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
        assert stage.type == "COORD_TRANSFORM"
        assert stage.inputFile["objectKey"] == "xidC/scan.e57"


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


def _s3_with(config_body, asset_metadata=None):
    """An s3 stub serving one template config body and, optionally, asset metadata over it."""
    bodies = {
        "pipelines/workflowExecutionInputs/E1/pipeline1/config.json": config_body,
        "pipelines/workflowExecutionInputs/E1/metadata.json": {
            "schemaVersion": 2,
            "assets": [{
                "databaseId": "dbC", "assetId": "xidC",
                "assetData": {"assetName": "Site scan"},
                "files": [{"fileKey": "/", "metadata": dict(asset_metadata or {})}],
            }],
        } if asset_metadata is not None else {},
    }

    def get_object(Bucket, Key):
        return {"Body": MagicMock(read=lambda: json.dumps(bodies[Key]).encode("utf-8"))}

    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    return s3


def _emitted_transform_params(payload):
    """The transform parameter set the emitted definition carries.

    `inputParameters` is a JSON STRING nested inside the definition, itself a JSON string in a Batch
    command override, so a substring search over the whole payload sees escaped text rather than the
    values. Parsed through both levels here so a type assertion is about the value the container
    reads.
    """
    definition = json.loads(payload["definition"][0])
    return json.loads(definition["inputParameters"])


@pytest.mark.unit
class TestCompressLazMustAgreeWithOutputFormats:
    """S4-PIPELINES-026: `compressLaz` is validated rather than silently discarded.

    LAZ is the compressed LAS format, so `compressLaz: false` with `laz` in `outputFormats` asks for
    a compressed file and for it not to be compressed. The run is refused here -- in the first state
    of the pipeline's state machine -- so it fails in seconds with the cause on the task token,
    rather than after a Batch container start.

    Every FALSE spelling is covered because asset-metadata values arrive as STRINGS: a metadata
    `compressLaz` of `"false"` is truthy in plain Python, so a truthiness check would be vacuous on
    exactly the route the pipeline's recognized-metadata-key table advertises.
    """

    LAZ_CONFIG = {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700",
                  "outputFormats": ["laz"]}

    def _refused(self, mod, s3):
        with patch.object(mod, "s3", s3), \
                patch.object(mod.sfn, "send_task_failure", MagicMock()) as send_failure, \
                pytest.raises(ValueError) as excinfo:
            mod.lambda_handler(_event(), MagicMock())
        return str(excinfo.value), send_failure

    def test_a_template_body_false_is_refused(self):
        mod = _load()
        config = dict(self.LAZ_CONFIG, compressLaz=False)
        message, send_failure = self._refused(mod, _s3_with(config))

        assert "compressLaz" in message and "outputFormats" in message, message
        # The cause reaches the VAMS workflow rather than leaving the task waiting out its timeout.
        assert send_failure.call_count == 1
        assert "compressLaz" in send_failure.call_args.kwargs["cause"]

    @pytest.mark.parametrize("spelling", ["false", "False", "FALSE", "0", "no", "off", " off "])
    def test_every_metadata_false_spelling_is_refused(self, spelling):
        mod = _load()
        message, _ = self._refused(
            mod, _s3_with(self.LAZ_CONFIG, asset_metadata={"compressLaz": spelling}))

        assert "compressLaz" in message, message

    def test_the_metadata_key_is_matched_case_insensitively(self):
        """The merge maps recognized keys case-insensitively, so the check has to see them too."""
        mod = _load()
        message, _ = self._refused(
            mod, _s3_with(self.LAZ_CONFIG, asset_metadata={"compresslaz": "false"}))

        assert "compressLaz" in message, message

    def test_a_comma_separated_metadata_format_list_is_seen(self):
        """`outputFormats` arrives from metadata as a comma-separated STRING, not a list."""
        mod = _load()
        message, _ = self._refused(mod, _s3_with(
            {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700"},
            asset_metadata={"outputFormats": "las,laz", "compressLaz": "false"}))

        assert "compressLaz" in message, message

    def test_a_laz_free_format_list_with_false_is_accepted(self):
        """The control that makes the refusals mean something.

        Only the CONTRADICTION is refused, not every explicit `compressLaz: false` -- otherwise the
        option would have been removed rather than validated, and the uncompressed request would be
        inexpressible.
        """
        mod = _load()
        config = {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700",
                  "outputFormats": ["las"], "compressLaz": False}
        payload = _run(mod, s3=_s3_with(config))

        params = _emitted_transform_params(payload)
        assert params["compressLaz"] is False, params
        assert params["outputFormats"] == ["las"], params

    @pytest.mark.parametrize("value", [True, "true", "True", "1", "yes", "on"])
    def test_a_true_spelling_with_laz_is_accepted(self, value):
        """The asymmetry: compressLaz defaults to true, so true + laz is the ordinary request."""
        mod = _load()
        payload = _run(mod, s3=_s3_with(dict(self.LAZ_CONFIG, compressLaz=value)))

        assert payload is not None

    def test_an_absent_compress_laz_with_laz_is_accepted(self):
        """The shipped template's own shape: outputFormats laz and no compressLaz at all."""
        mod = _load()
        payload = _run(mod, s3=_s3_with(dict(self.LAZ_CONFIG)))

        assert payload is not None

    def test_an_accepted_value_reaches_the_container_as_a_real_boolean(self):
        """A metadata string would otherwise reach `OutputConfig(compress_laz=...)` as a string.

        Pydantic coerces "false" to False there, so the container would silently disagree with the
        check that let the run through; normalizing here keeps the two reading the same value.
        """
        mod = _load()
        payload = _run(mod, s3=_s3_with(
            {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700", "outputFormats": ["las"]},
            asset_metadata={"compressLaz": "false"}))

        params = _emitted_transform_params(payload)

        assert params["compressLaz"] is False, (
            f"a metadata string reached the container unnormalized: "
            f"{params.get('compressLaz')!r}")
