#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/coordinateTransform container: the input is read at the version the run was launched on (#994).

constructPipeline names the version in a second command argument; the entry point puts it on the
stage's ``inputFile.versionId`` (``objects.apply_input_versions``) and the download sends it as
``VersionId``. A stage without one makes exactly the call it made before, and a version that no longer
exists fails the stage with a message naming the file and the version."""

import os
import sys
from unittest.mock import ANY, MagicMock

import pytest
from botocore.exceptions import ClientError

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from coord_transform_pipeline import core  # noqa: E402
from coord_transform_pipeline.utils.aws import s3  # noqa: E402
from coord_transform_pipeline.utils.pipeline.objects import PipelineStage, PipelineStatus  # noqa: E402

PARAMS = '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700","outputFormats":["laz"]}'
VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


def _stage(version_id=None):
    input_file = {"bucketName": "bucket", "objectKey": "asset/cloud.laz", "fileExtension": ".laz"}
    if version_id is not None:
        input_file["versionId"] = version_id
    return PipelineStage(type="COORD_TRANSFORM", inputFile=input_file,
                         outputFiles={"bucketName": "bucket", "objectDir": "asset/"},
                         outputMetadata={"bucketName": "bucket", "objectDir": "asset/meta/"})


@pytest.fixture
def bucket_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(s3, "client_for_bucket", lambda bucket: client)
    return client


class TestDownloadSendsTheVersion:
    def test_a_version_is_passed_as_extra_args(self, bucket_client, tmp_path):
        target = str(tmp_path / "in" / "cloud.laz")
        assert s3.download("bucket", "asset/cloud.laz", target, version_id=VERSION) == target
        bucket_client.download_fileobj.assert_called_once_with(
            "bucket", "asset/cloud.laz", ANY, ExtraArgs={"VersionId": VERSION})

    def test_no_version_makes_the_call_it_made_before(self, bucket_client, tmp_path):
        s3.download("bucket", "asset/cloud.laz", str(tmp_path / "in" / "cloud.laz"))
        bucket_client.download_fileobj.assert_called_once_with("bucket", "asset/cloud.laz", ANY)

    @pytest.mark.parametrize("code", ["404", "405", "NoSuchVersion", "NoSuchKey"])
    def test_a_missing_version_raises_naming_the_file_and_version(self, bucket_client, tmp_path, code):
        bucket_client.download_fileobj.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
        with pytest.raises(s3.InputVersionMissingError) as excinfo:
            s3.download("bucket", "asset/cloud.laz", str(tmp_path / "f"), version_id=VERSION)
        assert "asset/cloud.laz" in str(excinfo.value) and VERSION in str(excinfo.value)

    def test_other_failures_still_return_none(self, bucket_client, tmp_path):
        bucket_client.download_fileobj.side_effect = ClientError({"Error": {"Code": "403"}}, "GetObject")
        assert s3.download("bucket", "k", str(tmp_path / "f"), version_id=VERSION) is None


class TestTheStageReadsTheLaunchVersion:
    def _run(self, monkeypatch, stage, download):
        monkeypatch.setattr(core.s3, "download", download)
        return core._run_transform_stage(stage, "", PARAMS, local_test=False)

    def _recording(self, calls):
        def _download(bucket, key, dest, **kwargs):
            calls.append(kwargs)
            return None
        return _download

    def test_the_download_carries_the_version(self, monkeypatch):
        calls = []
        self._run(monkeypatch, _stage(VERSION), self._recording(calls))
        assert calls == [{"version_id": VERSION}]

    def test_a_stage_without_a_version_downloads_the_current_object(self, monkeypatch):
        calls = []
        self._run(monkeypatch, _stage(), self._recording(calls))
        assert calls == [{}]

    def test_a_deleted_version_fails_the_stage_naming_it(self, monkeypatch):
        def _download(bucket, key, dest, **kwargs):
            raise s3.InputVersionMissingError(key, kwargs["version_id"])

        result = self._run(monkeypatch, _stage(VERSION), _download)
        assert result.status == PipelineStatus.FAILED
        assert "asset/cloud.laz" in result.errorMessage and VERSION in result.errorMessage


# --- D4 on PR #1022: the launch version arrives beside the definition ---------------------------------
# constructPipeline keeps the version out of the definition, which an image built before the field parses
# into dataclasses that reject an unknown key, and names it in a second Batch command argument:
# {"inputVersions": {"<stage type>": "<version id>"}}. This image reads it from there.
import json  # noqa: E402

from coord_transform_pipeline import __main__ as d4_main  # noqa: E402
from coord_transform_pipeline.utils.pipeline import objects as d4_objects  # noqa: E402

D4_STAGE = "COORD_TRANSFORM"


def _d4_definition(**input_extra):
    input_file = {"bucketName": "asset-bucket", "objectKey": "asset/cloud.laz", "fileExtension": ".laz"}
    input_file.update(input_extra)
    return {"jobName": "j", "stages": [{"type": D4_STAGE, "inputFile": input_file, "outputFiles": {"bucketName": "o", "objectDir": "d/"}, "outputMetadata": {"bucketName": "o", "objectDir": "m/"}}]}


def _d4_argument(stage_type=D4_STAGE, version_id=VERSION):
    return json.dumps({"inputVersions": {stage_type: version_id}})


@pytest.mark.unit
class TestTheLaunchVersionArrivesBesideTheDefinition:
    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_stage_input_takes_the_named_version(self, version_id):
        params = d4_objects.apply_input_versions(_d4_definition(), [_d4_argument(version_id=version_id)])
        assert d4_objects.StageInput(**params["stages"][0]["inputFile"]).versionId == version_id

    def test_a_definition_alone_reads_the_current_object(self):
        params = d4_objects.apply_input_versions(_d4_definition(), [])
        assert params == _d4_definition()
        assert d4_objects.StageInput(**params["stages"][0]["inputFile"]).versionId == ""

    def test_a_version_already_in_the_input_file_is_kept(self):
        params = d4_objects.apply_input_versions(_d4_definition(versionId="kept"), [_d4_argument()])
        assert params["stages"][0]["inputFile"]["versionId"] == "kept"

    def test_a_stage_of_another_type_is_not_given_the_version(self):
        params = d4_objects.apply_input_versions(_d4_definition(), [_d4_argument(stage_type="OTHER")])
        assert "versionId" not in params["stages"][0]["inputFile"]

    @pytest.mark.parametrize("argument", ["not json", "[1]", '{"other": 1}', '{"inputVersions": [1]}',
                                          '{"inputVersions": {"%s": 5}}' % D4_STAGE])
    def test_an_unreadable_version_argument_fails_rather_than_reading_the_current_object(self, argument):
        with pytest.raises(ValueError, match="inputVersions"):
            d4_objects.apply_input_versions(_d4_definition(), [argument])

    @pytest.mark.parametrize("extra_args", [[], ["@ARG@"]])
    def test_main_hands_run_the_definition_with_the_named_version(self, monkeypatch, extra_args):
        extra_args = [_d4_argument() if a == "@ARG@" else a for a in extra_args]
        seen = []

        def _run(params, *args, **kwargs):
            seen.append(params)
            return MagicMock(status=d4_objects.PipelineStatus.COMPLETE)

        monkeypatch.setattr(d4_main, "run", _run)
        monkeypatch.setattr(sys, "argv", ["prog", json.dumps(_d4_definition())] + extra_args)
        with pytest.raises(SystemExit):
            d4_main.main()
        expected = VERSION if extra_args else None
        assert seen[0]["stages"][0]["inputFile"].get("versionId") == expected
