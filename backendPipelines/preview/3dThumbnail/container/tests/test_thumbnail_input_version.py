#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail container: the input is read at the version the run was launched on (#994).

constructPipeline names the version in a second command argument; the entry point puts it on the
stage's ``inputFile.versionId`` (``objects.apply_input_versions``). The size check, the
download and the streaming GET (every resume included) then send it as ``VersionId``; a stage without
one reads the current object with exactly the calls it made before. A version that no longer exists
fails the stage with a message naming the file and the version, rather than rendering the current
object."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from boto3.exceptions import S3TransferFailedError
from botocore.exceptions import ClientError, ReadTimeoutError

from preview_pipeline import core
from preview_pipeline.utils import s3_utils
from preview_pipeline.utils.pipeline.objects import PipelineStage, PipelineStatus, PipelineType, StageOutput

from test_thumbnail_large_inputs import _patches, _run

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


def _stage(key, ext, version_id=None):
    output = StageOutput(bucketName="run-bucket", objectDir="pipelines/p/job/output/e1/files/")
    input_file = {"bucketName": "asset-bucket", "objectKey": key, "fileExtension": ext}
    if version_id is not None:
        input_file["versionId"] = version_id
    return PipelineStage(type=PipelineType.PREVIEW_3D_THUMBNAIL, inputFile=input_file,
                         outputFiles=output.__dict__, outputMetadata=output.__dict__,
                         temporaryFiles=output.__dict__)


def _client_error(code, operation="HeadObject"):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


@pytest.fixture
def bucket_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(s3_utils, "client_for_bucket", lambda bucket: client)
    return client


@pytest.mark.unit
class TestS3HelpersSendTheVersion:
    def test_the_size_check_heads_the_version(self, bucket_client):
        bucket_client.head_object.return_value = {"ContentLength": 7}
        assert s3_utils.get_object_size("b", "k", version_id=VERSION) == 7
        bucket_client.head_object.assert_called_once_with(Bucket="b", Key="k", VersionId=VERSION)

    def test_the_size_check_without_a_version_heads_the_current_object(self, bucket_client):
        bucket_client.head_object.return_value = {"ContentLength": 7}
        s3_utils.get_object_size("b", "k")
        bucket_client.head_object.assert_called_once_with(Bucket="b", Key="k")

    def test_the_download_passes_the_version(self, bucket_client, tmp_path):
        s3_utils.download("b", "k", str(tmp_path / "f"), version_id=VERSION)
        assert bucket_client.download_file.call_args.kwargs["ExtraArgs"] == {"VersionId": VERSION}

    def test_the_download_without_a_version_sends_no_extra_args(self, bucket_client, tmp_path):
        s3_utils.download("b", "k", str(tmp_path / "f"))
        assert "ExtraArgs" not in bucket_client.download_file.call_args.kwargs

    def test_every_streamed_get_and_resume_reads_the_version(self, bucket_client):
        first, second = MagicMock(), MagicMock()

        def _dropping(chunk_size):
            yield b"abc"
            raise ReadTimeoutError(endpoint_url="https://s3")

        first.iter_chunks.side_effect = _dropping
        second.iter_chunks.return_value = iter([b"def"])
        bucket_client.get_object.side_effect = [{"Body": first}, {"Body": second}]
        assert b"".join(s3_utils.iter_object_bytes("b", "k", version_id=VERSION)) == b"abcdef"
        calls = [c.kwargs for c in bucket_client.get_object.call_args_list]
        assert calls == [{"Bucket": "b", "Key": "k", "VersionId": VERSION},
                         {"Bucket": "b", "Key": "k", "VersionId": VERSION, "Range": "bytes=3-"}]

    def test_a_stream_without_a_version_reads_the_current_object(self, bucket_client):
        body = MagicMock()
        body.iter_chunks.return_value = iter([b"x"])
        bucket_client.get_object.return_value = {"Body": body}
        list(s3_utils.iter_object_bytes("b", "k"))
        assert bucket_client.get_object.call_args.kwargs == {"Bucket": "b", "Key": "k"}

    @pytest.mark.parametrize("code", ["404", "405", "NoSuchVersion", "NoSuchKey", "MethodNotAllowed"])
    def test_a_missing_version_raises_naming_the_file_and_version(self, bucket_client, code):
        bucket_client.head_object.side_effect = _client_error(code)
        with pytest.raises(s3_utils.InputVersionMissingError) as excinfo:
            s3_utils.get_object_size("b", "xid/model.glb", version_id=VERSION)
        assert "xid/model.glb" in str(excinfo.value) and VERSION in str(excinfo.value)

    def test_a_download_of_a_missing_version_raises(self, bucket_client, tmp_path):
        bucket_client.download_file.side_effect = _client_error("404")
        with pytest.raises(s3_utils.InputVersionMissingError):
            s3_utils.download("b", "k", str(tmp_path / "f"), version_id=VERSION)
        bucket_client.download_file.side_effect = S3TransferFailedError(
            "An error occurred (NoSuchVersion) when calling the GetObject operation")
        with pytest.raises(s3_utils.InputVersionMissingError):
            s3_utils.download("b", "k", str(tmp_path / "f"), version_id=VERSION)

    def test_a_missing_current_object_keeps_returning_none(self, bucket_client, tmp_path):
        bucket_client.head_object.side_effect = _client_error("404")
        bucket_client.download_file.side_effect = _client_error("404")
        assert s3_utils.get_object_size("b", "k") is None
        assert s3_utils.download("b", "k", str(tmp_path / "f")) is None

    def test_other_errors_on_a_versioned_read_are_not_reported_as_a_deleted_version(self, bucket_client):
        bucket_client.head_object.side_effect = _client_error("403")
        assert s3_utils.get_object_size("b", "k", version_id=VERSION) is None


@pytest.mark.unit
class TestTheStageReadsTheLaunchVersion:
    def _download(self, calls):
        def _fake(bucket, key, path, **kwargs):
            calls.append(kwargs)
            open(path, "wb").write(b"x")
            return path
        return _fake

    def test_the_size_check_and_download_carry_the_version(self, tmp_path):
        calls = []
        size = MagicMock(return_value=1024)
        patches = _patches(tmp_path, 1024, download=self._download(calls))
        patches.append(patch.object(core.s3, "get_object_size", size))
        patches.append(patch.object(core, "_load_file", MagicMock(return_value=MagicMock())))
        result = _run(patches, _stage("asset1/model.glb", ".glb", VERSION))
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        size.assert_called_once_with("asset-bucket", "asset1/model.glb", version_id=VERSION)
        assert calls == [{"version_id": VERSION}]

    def test_a_stage_without_a_version_makes_the_calls_it_made_before(self, tmp_path):
        calls = []
        size = MagicMock(return_value=1024)
        patches = _patches(tmp_path, 1024, download=self._download(calls))
        patches.append(patch.object(core.s3, "get_object_size", size))
        patches.append(patch.object(core, "_load_file", MagicMock(return_value=MagicMock())))
        result = _run(patches, _stage("asset1/model.glb", ".glb"))
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        size.assert_called_once_with("asset-bucket", "asset1/model.glb")
        assert calls == [{}]

    def test_a_streamed_input_streams_the_version(self, tmp_path):
        stream = MagicMock(return_value=iter([b"1 2 3\n"]))
        patches = _patches(tmp_path, 1024, stream=stream)
        patches.append(patch.object(core.pointcloud_handler, "load_stream", MagicMock(return_value=MagicMock())))
        result = _run(patches, _stage("asset1/scan.ptx", ".ptx", VERSION))
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        stream.assert_called_once_with("asset-bucket", "asset1/scan.ptx", version_id=VERSION)

    def test_a_deleted_version_fails_the_stage_naming_it(self, tmp_path):
        download = MagicMock()
        patches = _patches(tmp_path, 1024, download=download)
        patches.append(patch.object(core.s3, "get_object_size", MagicMock(
            side_effect=s3_utils.InputVersionMissingError("asset1/model.glb", VERSION))))
        result = _run(patches, _stage("asset1/model.glb", ".glb", VERSION))
        assert result.status == PipelineStatus.FAILED
        assert "asset1/model.glb" in result.errorMessage and VERSION in result.errorMessage
        download.assert_not_called()

    def test_a_version_deleted_between_the_size_check_and_the_download_fails_the_stage(self, tmp_path):
        patches = _patches(tmp_path, 1024, download=MagicMock(
            side_effect=s3_utils.InputVersionMissingError("asset1/model.glb", VERSION)))
        result = _run(patches, _stage("asset1/model.glb", ".glb", VERSION))
        assert result.status == PipelineStatus.FAILED
        assert VERSION in result.errorMessage


# --- D4 on PR #1022: the launch version arrives beside the definition ---------------------------------
# constructPipeline keeps the version out of the definition, which an image built before the field parses
# into dataclasses that reject an unknown key, and names it in a second Batch command argument:
# {"inputVersions": {"<stage type>": "<version id>"}}. This image reads it from there.
import json  # noqa: E402
import sys  # noqa: E402

from preview_pipeline import __main__ as d4_main  # noqa: E402
from preview_pipeline.utils.pipeline import objects as d4_objects  # noqa: E402

D4_STAGE = "PREVIEW_3D_THUMBNAIL"


def _d4_definition(**input_extra):
    input_file = {"bucketName": "asset-bucket", "objectKey": "asset1/model.glb", "fileExtension": ".glb"}
    input_file.update(input_extra)
    return {"jobName": "j", "stages": [{"type": D4_STAGE, "inputFile": input_file, "outputFiles": {"bucketName": "o", "objectDir": "d/"}, "outputMetadata": {"bucketName": "o", "objectDir": "d/"}, "temporaryFiles": {"bucketName": "o", "objectDir": "d/"}}]}


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
