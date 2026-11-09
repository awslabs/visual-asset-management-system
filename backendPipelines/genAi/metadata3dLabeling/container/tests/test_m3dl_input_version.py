# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender render stage reads the selected file at the version the run was launched on (#994).

constructPipeline names the version in a second command argument; the entry point puts it on the
stage's ``inputFile.versionId`` (``objects.apply_input_versions``) and the download of the selected
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


# --- D4 on PR #1022: the launch version arrives beside the definition ---------------------------------
# constructPipeline keeps the version out of the definition, which an image built before the field parses
# into dataclasses that reject an unknown key, and names it in a second Batch command argument:
# {"inputVersions": {"<stage type>": "<version id>"}}. This image reads it from there.
import json  # noqa: E402
import sys  # noqa: E402

import main.__main__ as d4_main  # noqa: E402
from main.utils.pipeline import objects as d4_objects  # noqa: E402

D4_STAGE = "BLENDERRENDERER"


def _d4_definition(**input_extra):
    input_file = {"bucketName": "asset-bucket", "objectKey": "xid/test/pump.glb", "fileExtension": ".glb"}
    input_file.update(input_extra)
    return {"jobName": "j", "stages": [{"type": D4_STAGE, "inputFile": input_file, "outputFiles": {"bucketName": "o", "objectDir": "d/"}, "outputMetadata": {"bucketName": "", "objectDir": ""}, "temporaryFiles": {"bucketName": "o", "objectDir": "d/"}}]}


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

        monkeypatch.setattr(d4_main.core, "run", _run)
        monkeypatch.setattr(sys, "argv", ["prog", json.dumps(_d4_definition())] + extra_args)
        with pytest.raises(SystemExit):
            d4_main.main()
        expected = VERSION if extra_args else None
        assert seen[0]["stages"][0]["inputFile"].get("versionId") == expected
