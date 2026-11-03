#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/pcPotreeViewer: the input's launch version travels from the manifest to the Batch container (#994).

``vamsExecute`` resolves ``inputVersionId`` from the manifest's first input file and passes it to
``openPipeline``, which forwards it in the state-machine input; ``constructPipeline`` puts it on the
``inputFile.versionId`` of the stage that reads the asset input (PDAL for E57/PLY, POTREE for LAZ/LAS),
not on the POTREE stage that reads the PDAL stage's intermediate ``.laz``. An event or
definition without it reads the current object, as before. Reuses the fixtures of
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

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
# The definition origin/development's constructPipeline writes for the no-version test event
DEVELOPMENT_DEFINITIONS = {
    'scan.e57': (
        '{"jobName": "PipelineJob_x", "stages": [{"type": "PDAL", "inputFile": {"bucketName": "in-b'
        'kt", "objectKey": "xidM/scan.e57", "fileExtension": ".e57"}, "outputFiles": {"bucketName":'
        ' "aux-bkt", "objectDir": "xidM/scan.e57/preview/PotreeViewer/"}, "outputMetadata": {"bucke'
        'tName": "", "objectDir": ""}, "temporaryFiles": {"bucketName": "", "objectDir": ""}}, {"ty'
        'pe": "POTREE", "inputFile": {"bucketName": "aux-bkt", "objectKey": "xidM/scan.e57/preview/'
        'PotreeViewer/scan.laz", "fileExtension": ".laz"}, "outputFiles": {"bucketName": "aux-bkt",'
        ' "objectDir": "xidM/scan.e57/preview/PotreeViewer/"}, "outputMetadata": {"bucketName": "",'
        ' "objectDir": ""}, "temporaryFiles": {"bucketName": "", "objectDir": ""}}], "bucketRegions'
        '": {}, "inputMetadataS3Location": "", "inputConfigurationS3Location": "", "externalSfnTask'
        'Token": "tok"}'
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


@pytest.mark.unit
class TestConstructPipelinePutsTheVersionOnTheAssetStage:
    def _definition(self, key, **overrides):
        mod = refactor.TestConstructAndContainerContract()._construct()
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": f"s3://in-bkt/xidM/{key}",
            "outputS3AssetFilesPath": "s3://out-bkt/pipelines/p1/JOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": f"s3://aux-bkt/xidM/{key}/preview/PotreeViewer",
            "externalSfnTaskToken": "tok",
        }
        event.update(overrides)
        return json.loads(mod.lambda_handler(event, MagicMock())["definition"][0])

    def test_the_pdal_stage_reads_the_version_and_the_potree_stage_reads_its_intermediate(self):
        stages = self._definition("scan.e57", inputVersionId=VERSION)["stages"]
        assert [stage["type"] for stage in stages] == ["PDAL", "POTREE"]
        assert stages[0]["inputFile"]["versionId"] == VERSION
        assert "versionId" not in stages[1]["inputFile"]

    def test_a_laz_input_is_read_at_its_version_by_the_potree_stage(self):
        stages = self._definition("scan.laz", inputVersionId=VERSION)["stages"]
        assert [stage["type"] for stage in stages] == ["POTREE"]
        assert stages[0]["inputFile"]["versionId"] == VERSION

    @pytest.mark.parametrize("key", ["scan.e57", "scan.laz"])
    def test_an_event_without_one_writes_no_version_field(self, key):
        assert all("versionId" not in stage["inputFile"] for stage in self._definition(key)["stages"])

    @pytest.mark.parametrize("key", ["scan.e57", "scan.laz"])
    def test_without_a_version_the_definition_is_the_one_development_writes(self, key):
        # Captured from origin/development's constructPipeline for the same event. An image built before
        # the version field parses this definition during an upgrade, so a no-version run must not change it.
        assert json.dumps(self._definition(key)) == DEVELOPMENT_DEFINITIONS[key]
