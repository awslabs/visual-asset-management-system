#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/coordinateTransform container: the input is read at the version the run was launched on (#994).

constructPipeline puts the version on the stage's ``inputFile.versionId``; the download sends it as
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
