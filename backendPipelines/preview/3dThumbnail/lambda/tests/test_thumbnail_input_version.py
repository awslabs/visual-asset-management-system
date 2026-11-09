#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail: the input's launch version travels from the manifest to the Batch container (#994).

``vamsExecute`` resolves ``inputVersionId`` from the manifest's first input file and passes it to
``openPipeline``, which forwards it in the state-machine input and heads that version to pick the
compute tier; ``constructPipeline`` names it in a second Batch command argument beside the
definition, which stays the one development writes. An event without it reads the current object, as
before. Reuses the fixtures of
``test_manifest_refactor`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402
import computeTier  # noqa: E402

# Development's image-side parsing, for the upgrade window (backendPipelines/tests/fixtures/pre994)
_PRE994_DIR = os.path.join(_LAMBDA_DIR, "..", "..", "..", "tests", "fixtures", "pre994")
if _PRE994_DIR not in sys.path:
    sys.path.insert(0, _PRE994_DIR)
import legacy_parse as legacy  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
# The definition origin/development's constructPipeline writes for the no-version test event
DEVELOPMENT_DEFINITION = (
    '{"jobName": "PipelineJob_x", "stages": [{"type": "PREVIEW_3D_THUMBNAIL", "inputFile": {"bu'
    'cketName": "in-bkt", "objectKey": "xidM/test/pump.glb", "fileExtension": ".glb"}, "outputF'
    'iles": {"bucketName": "out-bkt", "objectDir": "pipelines/p1/MJOB/output/E1/files/"}, "outp'
    'utMetadata": {"bucketName": "", "objectDir": ""}, "temporaryFiles": {"bucketName": "", "ob'
    'jectDir": ""}}], "bucketRegions": {}, "inputMetadataS3Location": "", "inputConfigurationS3'
    'Location": "", "externalSfnTaskToken": "", "assetId": "xidM"}'
)


def _manifest_s3(manifest):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    return s3


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputVersion:
    def _run(self, manifest):
        base = refactor.TestVamsExecuteUsesManifest()
        mod = base._load_module()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._manifest_body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_payload_carries_the_manifest_version(self, version_id):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert self._run(manifest)["inputVersionId"] == version_id

    @pytest.mark.parametrize("version_id", ["", None])
    def test_no_version_sends_an_empty_value(self, version_id):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert self._run(manifest)["inputVersionId"] == ""


@pytest.mark.unit
class TestOpenPipelineForwardsTheInputVersion:
    def _run(self, event):
        base = refactor.TestOpenPipelineRegistration()
        mod = base._load_module()
        start = base._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()), \
                patch.object(mod.computeTier, "select_compute_tier", MagicMock(return_value="standard")):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(start.call_args.kwargs["input"])

    def test_the_state_machine_input_carries_the_version(self):
        event = dict(refactor.TestOpenPipelineRegistration()._event(), inputVersionId=VERSION)
        assert self._run(event)["inputVersionId"] == VERSION

    def test_an_event_from_an_older_lambda_forwards_an_empty_version(self):
        assert self._run(refactor.TestOpenPipelineRegistration()._event())["inputVersionId"] == ""


@pytest.mark.unit
class TestComputeTierHeadsTheSelectedVersion:
    def _s3(self):
        client = MagicMock()
        client.head_object.return_value = {"ContentLength": 10}
        client.get_object.side_effect = Exception("NoSuchKey")
        return client

    def test_a_version_is_headed(self):
        client = self._s3()
        computeTier.select_compute_tier(client, {"inputS3AssetFilePath": "s3://abkt/xid/a.glb",
                                                 "inputVersionId": VERSION}, 100)
        client.head_object.assert_called_once_with(Bucket="abkt", Key="xid/a.glb", VersionId=VERSION)

    def test_no_version_heads_the_current_object(self):
        client = self._s3()
        computeTier.select_compute_tier(client, {"inputS3AssetFilePath": "s3://abkt/xid/a.glb"}, 100)
        client.head_object.assert_called_once_with(Bucket="abkt", Key="xid/a.glb")


@pytest.mark.unit
class TestConstructPipelinePutsTheVersionBesideTheDefinition:
    def _command(self, **overrides):
        mod = refactor.TestContainerContractAcceptsConstructOutput()._construct_module()
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://in-bkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://out-bkt/pipelines/p1/MJOB/output/E1/files/",
            "assetId": "xidM",
        }
        event.update(overrides)
        return mod.lambda_handler(event, MagicMock())["definition"]

    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_second_argument_names_the_stage_version(self, version_id):
        command = self._command(inputVersionId=version_id)
        assert len(command) == 2
        assert json.loads(command[1]) == {"inputVersions": {"PREVIEW_3D_THUMBNAIL": version_id}}

    def test_an_event_without_one_writes_only_the_definition(self):
        assert len(self._command()) == 1

    @pytest.mark.parametrize("overrides", [{}, {"inputVersionId": VERSION}])
    def test_the_definition_is_the_one_development_writes_with_or_without_a_version(self, overrides):
        # Captured from origin/development's constructPipeline for the same event. An image built before
        # the version field parses this definition during an upgrade, so no run may change it.
        assert self._command(**overrides)[0] == DEVELOPMENT_DEFINITION


@pytest.mark.unit
class TestTheDevelopmentImageParsesTheNewCommand:
    """D4 on PR #1022: a run launched during an upgrade can reach the image built from development, whose
    strict dataclasses rejected ``inputFile.versionId``. The command now keeps the version out of the
    definition; this feeds it to development's own parsing (``fixtures/pre994``)."""

    def _command(self, version_id):
        return TestConstructPipelinePutsTheVersionBesideTheDefinition()._command(inputVersionId=version_id)

    @pytest.mark.parametrize("version_id", [VERSION, "null", ""])
    def test_a_launch_with_or_without_a_version_parses(self, version_id):
        _, stage, stage_input = legacy.parse_like_development("3dThumbnail", self._command(version_id))
        assert stage.type == "PREVIEW_3D_THUMBNAIL"
        assert stage_input.objectKey == "xidM/test/pump.glb"

    def test_the_version_inside_the_definition_is_what_development_rejected(self):
        # The negative control: the definition this PR wrote before the fix.
        command = self._command(VERSION)
        definition = json.loads(command[0])
        definition["stages"][0]["inputFile"]["versionId"] = VERSION
        with pytest.raises(TypeError, match="versionId"):
            legacy.parse_like_development("3dThumbnail", [json.dumps(definition)])
