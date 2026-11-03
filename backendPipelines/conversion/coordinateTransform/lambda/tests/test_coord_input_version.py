#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/coordinateTransform: the input's launch version travels from the manifest to the Batch container (#994).

``vamsExecute`` resolves ``inputVersionId`` from the manifest's first input file and passes it to
``openPipeline``, which forwards it in the state-machine input; ``constructPipeline`` names it in a
second Batch command argument beside the definition, which stays the one development writes, and
``executeBatchJob`` submits both. An event without it reads the current object, as before. Reuses the
fixtures of ``test_manifest_refactor`` and ``test_coord_bucket_regions`` (same directory; the suite
runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402
import test_coord_bucket_regions as regions  # noqa: E402
import test_batch_job_registration as registration  # noqa: E402

# Development's image-side parsing, for the upgrade window (backendPipelines/tests/fixtures/pre994)
_PRE994_DIR = os.path.join(_LAMBDA_DIR, "..", "..", "..", "tests", "fixtures", "pre994")
if _PRE994_DIR not in sys.path:
    sys.path.insert(0, _PRE994_DIR)
import legacy_parse as legacy  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
# The definition origin/development's constructPipeline writes for the no-version test event
DEVELOPMENT_DEFINITION = (
    '{"jobName": "CoordXform_x", "stages": [{"type": "COORD_TRANSFORM", "inputFile": {"bucketNa'
    'me": "in-bkt", "objectKey": "xidC/scan.e57", "fileExtension": ".e57"}, "outputFiles": {"bu'
    'cketName": "out-bkt", "objectDir": "pipelines/p1/CJOB/output/E1/files/"}, "outputMetadata"'
    ': {"bucketName": "out-bkt", "objectDir": "pipelines/p1/CJOB/output/E1/metadata/"}}], "buck'
    'etRegions": {}, "assetId": "xidC", "databaseId": "dbC", "inputMetadata": "", "inputParamet'
    'ers": "{\\"sourceCrs\\": \\"EPSG:4326\\", \\"targetCrs\\": \\"EPSG:27700\\", \\"outputFormats\\": [\\'
    '"laz\\"]}", "externalSfnTaskToken": "tok-123"}'
)


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputVersion:
    def _run(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", regions._manifest_s3(manifest)), \
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
        return regions.TestOpenPipelineForwardsBucketRegions()._run(event)

    def test_the_state_machine_input_carries_the_version(self):
        event = dict(refactor.TestOpenPipeline()._event(), inputVersionId=VERSION)
        assert self._run(event)["inputVersionId"] == VERSION

    def test_an_event_from_an_older_lambda_forwards_an_empty_version(self):
        assert self._run(refactor.TestOpenPipeline()._event())["inputVersionId"] == ""


def _command(**overrides):
    construct = regions.TestConstructPipelineMapsBucketsToRegions()
    return construct._command(construct._event(**overrides))


@pytest.mark.unit
class TestConstructPipelinePutsTheVersionBesideTheDefinition:
    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_second_argument_names_the_stage_version(self, version_id):
        command = _command(inputVersionId=version_id)
        assert len(command) == 2
        assert json.loads(command[1]) == {"inputVersions": {"COORD_TRANSFORM": version_id}}

    def test_an_event_without_one_writes_only_the_definition(self):
        assert len(_command()) == 1

    @pytest.mark.parametrize("overrides", [{}, {"inputVersionId": VERSION}])
    def test_the_definition_is_the_one_development_writes_with_or_without_a_version(self, overrides):
        # Captured from origin/development's constructPipeline for the same event. An image built before
        # the version field parses this definition during an upgrade, so no run may change it.
        assert _command(**overrides)[0] == DEVELOPMENT_DEFINITION

    def test_the_batch_submit_sends_every_argument(self):
        # executeBatchJob submits the list constructPipeline returned as the container command.
        command = _command(inputVersionId=VERSION)
        mod = registration._load_execute_batch_job()
        mod.lambda_handler({"jobName": "j", "definition": command, "taskToken": "tok"}, MagicMock())
        assert mod.batch.submit_job.call_args.kwargs["containerOverrides"]["command"] == command


@pytest.mark.unit
class TestTheDevelopmentImageParsesTheNewCommand:
    """D4 on PR #1022: a run launched during an upgrade can reach the image built from development, whose
    strict dataclasses rejected ``inputFile.versionId``. The command now keeps the version out of the
    definition; this feeds it to development's own parsing (``fixtures/pre994``)."""

    @pytest.mark.parametrize("version_id", [VERSION, "null", ""])
    def test_a_launch_with_or_without_a_version_parses(self, version_id):
        _, stage, stage_input = legacy.parse_like_development(
            "coordinateTransform", _command(inputVersionId=version_id))
        assert stage.type == "COORD_TRANSFORM"
        assert stage_input.objectKey == "xidC/scan.e57"

    def test_the_version_inside_the_definition_is_what_development_rejected(self):
        # The negative control: the definition this PR wrote before the fix.
        definition = json.loads(_command(inputVersionId=VERSION)[0])
        definition["stages"][0]["inputFile"]["versionId"] = VERSION
        with pytest.raises(TypeError, match="versionId"):
            legacy.parse_like_development("coordinateTransform", [json.dumps(definition)])
