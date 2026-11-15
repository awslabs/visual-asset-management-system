#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/pcPotreeViewer: the input's launch version travels from the manifest to the Batch container (#994).

``vamsExecute`` resolves ``inputVersionId`` from the manifest's first input file and passes it to
``openPipeline``, which forwards it in the state-machine input; ``constructPipeline`` names it, in a
second Batch command argument beside the definition, for the stage that reads the asset input (PDAL for
E57/PLY, POTREE for LAZ/LAS), not for the POTREE stage that reads the PDAL stage's intermediate
``.laz``. The definition stays the one development writes. An event without a version reads the
current object, as before. Reuses the fixtures of
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

# Development's image-side parsing, for the upgrade window (backendPipelines/tests/fixtures/pre994)
_PRE994_DIR = os.path.join(_LAMBDA_DIR, "..", "..", "..", "tests", "fixtures", "pre994")
if _PRE994_DIR not in sys.path:
    sys.path.insert(0, _PRE994_DIR)
import legacy_parse as legacy  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
# The definition origin/development's constructPipeline writes for the no-version test event (E57
# recaptured at 570c0f9ba, after #1054 moved the PDAL intermediate into the run's own working folder)
DEVELOPMENT_DEFINITIONS = {
    'scan.e57': (
        '{"jobName": "PipelineJob_x", "stages": [{"type": "PDAL", "inputFile": {"bucketName": "in-b'
        'kt", "objectKey": "xidM/scan.e57", "fileExtension": ".e57"}, "outputFiles": {"bucketName":'
        ' "aux-bkt", "objectDir": "xidM/scan.e57/preview/PotreeViewerRuns/PipelineJob_x/"}, "output'
        'Metadata": {"bucketName": "", "objectDir": ""}, "temporaryFiles": {"bucketName": "aux-bkt"'
        ', "objectDir": "xidM/scan.e57/preview/PotreeViewerRuns/PipelineJob_x/"}}, {"type": "POTREE'
        '", "inputFile": {"bucketName": "aux-bkt", "objectKey": "xidM/scan.e57/preview/PotreeViewer'
        'Runs/PipelineJob_x/scan.laz", "fileExtension": ".laz"}, "outputFiles": {"bucketName": "aux'
        '-bkt", "objectDir": "xidM/scan.e57/preview/PotreeViewer/"}, "outputMetadata": {"bucketName'
        '": "", "objectDir": ""}, "temporaryFiles": {"bucketName": "aux-bkt", "objectDir": "xidM/sc'
        'an.e57/preview/PotreeViewerRuns/PipelineJob_x/"}}], "bucketRegions": {}, "inputMetadataS3L'
        'ocation": "", "inputConfigurationS3Location": "", "externalSfnTaskToken": "tok"}'
    ),
    'scan.laz': (
        '{"jobName": "PipelineJob_x", "stages": [{"type": "POTREE", "inputFile": {"bucketName": "in'
        '-bkt", "objectKey": "xidM/scan.laz", "fileExtension": ".laz"}, "outputFiles": {"bucketName'
        '": "aux-bkt", "objectDir": "xidM/scan.laz/preview/PotreeViewer/"}, "outputMetadata": {"buc'
        'ketName": "", "objectDir": ""}, "temporaryFiles": {"bucketName": "", "objectDir": ""}}], "'
        'bucketRegions": {}, "inputMetadataS3Location": "", "inputConfigurationS3Location": "", "ex'
        'ternalSfnTaskToken": "tok"}'
    ),
}


def _manifest_s3(manifest):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    return s3


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputVersion:
    def _run(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_payload_carries_the_manifest_version(self, version_id):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert self._run(manifest)["inputVersionId"] == version_id

    @pytest.mark.parametrize("version_id", ["", None])
    def test_no_version_sends_an_empty_value(self, version_id):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert self._run(manifest)["inputVersionId"] == ""


@pytest.mark.unit
class TestOpenPipelineForwardsTheInputVersion:
    def _run(self, event):
        base = refactor.TestOpenPipeline()
        mod = base._load()
        start = base._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(start.call_args.kwargs["input"])

    def test_the_state_machine_input_carries_the_version(self):
        event = dict(refactor.TestOpenPipeline()._event(), inputVersionId=VERSION)
        assert self._run(event)["inputVersionId"] == VERSION

    def test_an_event_from_an_older_lambda_forwards_an_empty_version(self):
        assert self._run(refactor.TestOpenPipeline()._event())["inputVersionId"] == ""


def _command(key, **overrides):
    mod = refactor.TestConstructAndContainerContract()._construct()
    event = {
        "jobName": "PipelineJob_x",
        "inputS3AssetFilePath": f"s3://in-bkt/xidM/{key}",
        "outputS3AssetFilesPath": "s3://out-bkt/pipelines/p1/JOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://aux-bkt/xidM/{key}/preview/PotreeViewer",
        "externalSfnTaskToken": "tok",
    }
    event.update(overrides)
    return mod.lambda_handler(event, MagicMock())["definition"]


@pytest.mark.unit
class TestConstructPipelinePutsTheVersionBesideTheDefinition:
    def test_the_pdal_stage_is_named_and_the_potree_stage_reads_its_intermediate(self):
        command = _command("scan.e57", inputVersionId=VERSION)
        assert [stage["type"] for stage in json.loads(command[0])["stages"]] == ["PDAL", "POTREE"]
        # Only the stage that reads the selected input is named; POTREE after PDAL reads PDAL's output.
        assert json.loads(command[1]) == {"inputVersions": {"PDAL": VERSION}}

    def test_a_laz_input_is_named_for_the_potree_stage(self):
        command = _command("scan.laz", inputVersionId=VERSION)
        assert [stage["type"] for stage in json.loads(command[0])["stages"]] == ["POTREE"]
        assert json.loads(command[1]) == {"inputVersions": {"POTREE": VERSION}}

    @pytest.mark.parametrize("key", ["scan.e57", "scan.laz"])
    def test_an_event_without_one_writes_only_the_definition(self, key):
        assert len(_command(key)) == 1

    @pytest.mark.parametrize("key", ["scan.e57", "scan.laz"])
    @pytest.mark.parametrize("overrides", [{}, {"inputVersionId": VERSION}])
    def test_the_definition_is_the_one_development_writes_with_or_without_a_version(self, key, overrides):
        # Captured from origin/development's constructPipeline for the same event. An image built before
        # the version field parses this definition during an upgrade, so no run may change it.
        assert _command(key, **overrides)[0] == DEVELOPMENT_DEFINITIONS[key]


@pytest.mark.unit
class TestTheDevelopmentImageParsesTheNewCommand:
    """D4 on PR #1022: a run launched during an upgrade can reach the PDAL or Potree image built from
    development, whose strict dataclasses rejected ``inputFile.versionId``. The command now keeps the
    version out of the definition; this feeds it to development's own parsing (``fixtures/pre994``)."""

    @pytest.mark.parametrize("key, stage_type", [("scan.e57", "PDAL"), ("scan.laz", "POTREE")])
    @pytest.mark.parametrize("version_id", [VERSION, "null", ""])
    def test_a_launch_with_or_without_a_version_parses(self, key, stage_type, version_id):
        _, stage, stage_input = legacy.parse_like_development(
            "pcPotreeViewer", _command(key, inputVersionId=version_id))
        assert stage.type == stage_type
        assert stage_input.objectKey == f"xidM/{key}"

    @pytest.mark.parametrize("key", ["scan.e57", "scan.laz"])
    def test_the_version_inside_the_definition_is_what_development_rejected(self, key):
        # The negative control: the definition this PR wrote before the fix.
        definition = json.loads(_command(key, inputVersionId=VERSION)[0])
        definition["stages"][0]["inputFile"]["versionId"] = VERSION
        with pytest.raises(TypeError, match="versionId"):
            legacy.parse_like_development("pcPotreeViewer", [json.dumps(definition)])
