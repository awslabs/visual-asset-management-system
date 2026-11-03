#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""genAi/imageTo3d: every view travels from the manifest to the Batch container with its launch version (#994).

``vamsExecute`` forwards the primary input's ``inputVersionId`` and, parallel to ``inputS3AssetFilePaths``,
each view's version in ``inputVersionIds``. ``openPipeline`` names both on the state-machine input (this
hop enumerates its fields, so a field it does not name is dropped), and ``constructPipeline`` puts each
on the definition's ``inputFile`` / ``inputFiles`` entries. An event without them reads every view as it
is now. Reuses the fixtures of ``test_imageto3d_pipeline`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_imageto3d_pipeline import TestConstructPipeline, TestOpenPipeline, TestVamsExecute  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
BACK_VERSION = "Lm9vYmFyLmJhY2sudmVyc2lvbg"
# The definition origin/development's constructPipeline writes for the no-version test event
DEVELOPMENT_DEFINITION = (
    '{"jobName": "ImageTo3dJob_20260101_000000_abc123", "model": "trellis2", "assetId": "xidM",'
    ' "databaseId": "dbM", "inputFile": {"bucketName": "abkt", "objectKey": "xidM/photos/mug.pn'
    'g", "fileExtension": ".png"}, "inputFiles": [{"bucketName": "abkt", "objectKey": "xidM/pho'
    'tos/mug.png", "fileExtension": ".png"}], "outputFiles": {"bucketName": "abkt", "objectDir"'
    ': "pipelines/imageTo3d/JOB/output/E1/files/"}, "outputMetadata": {"bucketName": "abkt", "o'
    'bjectDir": "pipelines/imageTo3d/JOB/output/E1/metadata/"}, "outputResults": {"bucketName":'
    ' "", "objectDir": ""}, "temporaryFiles": {"bucketName": "aux", "objectDir": "pipelines/ima'
    'geTo3d/E1/"}, "inputMetadataS3Location": "s3://abkt/.../metadata.json", "inputConfiguratio'
    'nS3Location": "s3://abkt/.../config.json", "bucketRegions": {}}'
)


def _views_manifest(*version_ids):
    manifest = TestVamsExecute()._manifest()
    first = manifest["inputFiles"][0]
    manifest["inputFiles"] = [dict(first, key=f"xidM/photos/mug_{i}.png", relativePath=f"/photos/mug_{i}.png",
                                   versionId=version_id)
                              for i, version_id in enumerate(version_ids)]
    return manifest


@pytest.mark.unit
class TestVamsExecuteForwardsTheVersions:
    def test_the_primary_and_every_view_carry_their_version(self):
        _mod, resp, payload = TestVamsExecute()._run(
            manifest=_views_manifest(VERSION, BACK_VERSION), config={"MODEL": "hunyuan3d"})
        assert resp["statusCode"] == 200
        assert payload["inputVersionId"] == VERSION
        assert payload["inputVersionIds"] == [VERSION, BACK_VERSION]
        assert len(payload["inputVersionIds"]) == len(payload["inputS3AssetFilePaths"])

    def test_no_version_sends_empty_values(self):
        _mod, _resp, payload = TestVamsExecute()._run(manifest=_views_manifest("null"))
        assert payload["inputVersionId"] == ""
        assert payload["inputVersionIds"] == [""]


@pytest.mark.unit
class TestOpenPipelineNamesTheVersions:
    def test_the_state_machine_input_carries_both(self):
        event = TestOpenPipeline()._event()
        event.update(inputVersionId=VERSION, inputVersionIds=[VERSION, BACK_VERSION],
                     inputS3AssetFilePaths=["s3://abkt/xidM/photos/mug.png", "s3://abkt/xidM/photos/back.png"])
        _mod, resp, start, _fail = TestOpenPipeline()._run(event)
        assert resp["statusCode"] == 200
        payload = json.loads(start.call_args.kwargs["input"])
        assert payload["inputVersionId"] == VERSION
        assert payload["inputVersionIds"] == [VERSION, BACK_VERSION]

    def test_an_event_from_an_older_lambda_forwards_empty_values(self):
        _mod, _resp, start, _fail = TestOpenPipeline()._run(TestOpenPipeline()._event())
        payload = json.loads(start.call_args.kwargs["input"])
        assert (payload["inputVersionId"], payload["inputVersionIds"]) == ("", [])


@pytest.mark.unit
class TestConstructPipelinePutsEachVersionOnItsView:
    def _definition(self, **overrides):
        construct = TestConstructPipeline()
        out = construct._load().lambda_handler(construct._event(**overrides), MagicMock())
        return json.loads(out["definition"][2])

    def test_each_view_carries_its_own_version(self):
        definition = self._definition(
            inputVersionId=VERSION, inputVersionIds=[VERSION, BACK_VERSION],
            inputS3AssetFilePaths=["s3://abkt/xidM/photos/mug.png", "s3://abkt/xidM/photos/mug_back.png"])
        assert definition["inputFile"]["versionId"] == VERSION
        assert [view["versionId"] for view in definition["inputFiles"]] == [VERSION, BACK_VERSION]

    def test_a_view_without_a_version_writes_no_version_field(self):
        definition = self._definition(
            inputVersionId=VERSION, inputVersionIds=[VERSION, ""],
            inputS3AssetFilePaths=["s3://abkt/xidM/photos/mug.png", "s3://abkt/xidM/photos/mug_back.png"])
        assert definition["inputFiles"][0]["versionId"] == VERSION
        assert "versionId" not in definition["inputFiles"][1]

    def test_a_single_view_run_carries_the_primary_version(self):
        definition = self._definition(inputVersionId=VERSION)
        assert [view["versionId"] for view in definition["inputFiles"]] == [VERSION]

    def test_without_a_version_the_definition_is_the_one_development_writes(self):
        # Captured from origin/development's constructPipeline for the same event. An image built before
        # the version field parses this definition during an upgrade, so a no-version run must not change it.
        assert json.dumps(self._definition()) == DEVELOPMENT_DEFINITION

    def test_an_event_from_an_older_lambda_reads_every_view_as_it_is_now(self):
        definition = self._definition(
            inputS3AssetFilePaths=["s3://abkt/xidM/photos/mug.png", "s3://abkt/xidM/photos/mug_back.png"])
        assert "versionId" not in definition["inputFile"]
        assert all("versionId" not in view for view in definition["inputFiles"])

    def test_a_short_version_list_pads_with_the_current_object(self):
        definition = self._definition(
            inputVersionIds=[VERSION],
            inputS3AssetFilePaths=["s3://abkt/xidM/photos/mug.png", "s3://abkt/xidM/photos/mug_back.png"])
        assert [view.get("versionId") for view in definition["inputFiles"]] == [VERSION, None]
