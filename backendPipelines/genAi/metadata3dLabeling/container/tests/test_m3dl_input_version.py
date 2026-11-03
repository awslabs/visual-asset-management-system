# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender render stage reads the selected file at the version the run was launched on (#994).

constructPipeline puts the version on the stage's ``inputFile.versionId``; the download of the selected
file sends it as ``VersionId``. With ``includeAllAssetFileHierarchyFiles`` the other files of the asset
folder are fetched as they are now. A stage without a version makes exactly the calls it made before,
and a version that no longer exists fails the stage naming the file and the version.
"""

from unittest.mock import ANY, MagicMock

import pytest
from botocore.exceptions import ClientError

import main.pipelines.blenderRenderer.pipeline as pipeline
from main.utils.aws import s3
from main.utils.pipeline.objects import PipelineStage, PipelineStatus

PRIMARY_KEY = "xid/test/pump.glb"
SIBLING_KEY = "xid/test/pump.bin"
VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


def _stage(version_id=None):
    input_file = {"bucketName": "asset-bucket", "objectKey": PRIMARY_KEY}
    if version_id is not None:
        input_file["versionId"] = version_id
    return PipelineStage(
        type="BLENDERRENDERER",
        inputFile=input_file,
        outputFiles={"bucketName": "aux-bucket", "objectDir": "xid/test/pump.glb/genAi/"},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles={"bucketName": "aux-bucket", "objectDir": "xid/test/pump.glb/genAi/"},
    )


def _run(monkeypatch, tmp_path, stage, download, hierarchy=False):
    monkeypatch.chdir(tmp_path)
    listing = [{"key": PRIMARY_KEY, "relativePath": "pump.glb"}, {"key": SIBLING_KEY, "relativePath": "pump.bin"}]
    monkeypatch.setattr(pipeline.ext, "create_dir", lambda parts: str(tmp_path / "-".join(parts)))
    (tmp_path / "tmp-input").mkdir(exist_ok=True)
    (tmp_path / "tmp-output").mkdir(exist_ok=True)
    monkeypatch.setattr(pipeline.s3, "get_all_files_in_path", lambda bucket, path: list(listing))
    monkeypatch.setattr(pipeline.s3, "download", download)
    monkeypatch.setattr(pipeline.s3, "uploadV2", lambda bucket, key, path: key)
    monkeypatch.setattr(pipeline.s3, "delete_all_path_contents", lambda bucket, key: None)
    monkeypatch.setattr(pipeline, "allconvert_blenderrenderer_pipeline",
                        lambda input_file_path, output_dir: {"output_dir": output_dir, "returncode": 0,
                                                                     "output_files": []})
    parameters = '{"includeAllAssetFileHierarchyFiles": "True"}' if hierarchy else ""
    return pipeline.run(stage, "", parameters, False)


def _recording(calls):
    def _download(bucket, key, path, **kwargs):
        calls.append((key, kwargs))
        open(path, "w", encoding="utf-8").write("model bytes")
        return path
    return _download


@pytest.mark.unit
class TestTheSelectedFileIsReadAtItsVersion:
    def test_the_selected_file_is_downloaded_at_its_version(self, monkeypatch, tmp_path):
        calls = []
        _run(monkeypatch, tmp_path, _stage(VERSION), _recording(calls))
        assert calls == [(PRIMARY_KEY, {"version_id": VERSION})]

    def test_hierarchy_siblings_are_read_as_they_are_now(self, monkeypatch, tmp_path):
        calls = []
        _run(monkeypatch, tmp_path, _stage(VERSION), _recording(calls), hierarchy=True)
        assert calls == [(PRIMARY_KEY, {"version_id": VERSION}), (SIBLING_KEY, {})]

    def test_a_stage_without_a_version_downloads_the_current_object(self, monkeypatch, tmp_path):
        calls = []
        _run(monkeypatch, tmp_path, _stage(), _recording(calls))
        assert calls == [(PRIMARY_KEY, {})]

    def test_a_deleted_version_fails_the_stage_naming_it(self, monkeypatch, tmp_path):
        def _download(bucket, key, path, **kwargs):
            raise s3.InputVersionMissingError(key, kwargs["version_id"])

        result = _run(monkeypatch, tmp_path, _stage(VERSION), _download)
        assert result.status == PipelineStatus.FAILED
        assert PRIMARY_KEY in result.errorMessage and VERSION in result.errorMessage


@pytest.mark.unit
class TestDownloadSendsTheVersion:
    @pytest.fixture
    def bucket_client(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(s3, "client_for_bucket", lambda bucket: client)
        return client

    def test_a_version_is_passed_as_extra_args(self, bucket_client, tmp_path):
        s3.download("b", PRIMARY_KEY, str(tmp_path / "f"), version_id=VERSION)
        bucket_client.download_fileobj.assert_called_once_with("b", PRIMARY_KEY, ANY, ExtraArgs={"VersionId": VERSION})

    def test_no_version_makes_the_call_it_made_before(self, bucket_client, tmp_path):
        s3.download("b", PRIMARY_KEY, str(tmp_path / "f"))
        bucket_client.download_fileobj.assert_called_once_with("b", PRIMARY_KEY, ANY)

    @pytest.mark.parametrize("code", ["404", "405", "NoSuchVersion", "NoSuchKey"])
    def test_a_missing_version_raises(self, bucket_client, tmp_path, code):
        bucket_client.download_fileobj.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
        with pytest.raises(s3.InputVersionMissingError):
            s3.download("b", PRIMARY_KEY, str(tmp_path / "f"), version_id=VERSION)

    def test_other_failures_still_return_none(self, bucket_client, tmp_path):
        bucket_client.download_fileobj.side_effect = ClientError({"Error": {"Code": "403"}}, "GetObject")
        assert s3.download("b", PRIMARY_KEY, str(tmp_path / "f"), version_id=VERSION) is None
