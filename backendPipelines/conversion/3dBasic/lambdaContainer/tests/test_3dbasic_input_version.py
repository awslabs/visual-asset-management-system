#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/3dBasic: the input is converted at the version the run was launched on (#994).

The handler reads the workflow manifest itself. ``resolve_inputs_from_manifest`` returns the first
input file's ``versionId`` ("" for none, "" or a legacy payload; "null" is sent), the handler passes it to
``convert_input_output``, and the input download sends it as ``VersionId``. The files the input
references keep being read as they are now. A run without a version makes the call it made before,
and a version that no longer exists fails the run naming the file and the version."""

import os
import sys
from unittest.mock import ANY, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_conversion_contract import _exporting_trimesh, _load, _manifest  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
OUTPUT = "s3://obkt/pipelines/p1/JOB/output/E1/files/"


def _resolve(mod, manifest):
    with patch.object(mod, "fetch_manifest", MagicMock(return_value=manifest)):
        return mod.resolve_inputs_from_manifest({"inputManifestS3Location": "s3://abkt/m.json"})


@pytest.mark.unit
class TestTheResolverReturnsTheVersion:
    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_first_input_files_version(self, version_id):
        mod = _load()
        manifest = _manifest("/model.obj", "xid/model.obj")
        manifest["inputFiles"][0]["versionId"] = version_id
        assert _resolve(mod, manifest)[3] == version_id

    @pytest.mark.parametrize("version_id", ["", None])
    def test_no_version_to_send(self, version_id):
        mod = _load()
        manifest = _manifest("/model.obj", "xid/model.obj")
        manifest["inputFiles"][0]["versionId"] = version_id
        assert _resolve(mod, manifest)[3] == ""

    def test_a_legacy_payload_has_no_version(self):
        mod = _load()
        with patch.object(mod, "fetch_manifest", MagicMock(return_value=None)):
            resolved = mod.resolve_inputs_from_manifest(
                {"inputS3AssetFilePath": "s3://abkt/xid/model.obj", "outputS3AssetFilesPath": OUTPUT})
        assert resolved[3] == ""


@pytest.mark.unit
class TestTheHandlerConvertsTheVersion:
    def test_the_handler_passes_the_version_to_the_conversion(self):
        mod = _load()
        convert = MagicMock()
        with patch.object(mod, "fetch_input_configuration", MagicMock(return_value={"outputType": ".glb"})), \
                patch.object(mod, "resolve_inputs_from_manifest",
                             MagicMock(return_value=("s3://abkt/xid/m.stl", OUTPUT, "", VERSION))), \
                patch.object(mod, "convert_input_output", convert):
            mod.lambda_handler({"body": {"inputManifestS3Location": "s3://abkt/m.json"}}, MagicMock())
        convert.assert_called_once_with("s3://abkt/xid/m.stl", OUTPUT, ".glb", "", VERSION)

    def _convert(self, mod, version):
        calls = []

        def _download(bucket, key, path, **kwargs):
            calls.append((key, kwargs))
            open(path, "wb").close()
            return path

        with patch.object(mod, "download", MagicMock(side_effect=_download)), \
                patch.object(mod, "uploadV2", MagicMock(side_effect=lambda bucket, key, path: key)), \
                patch.object(mod, "trimesh", _exporting_trimesh()):
            mod.convert_input_output("s3://abkt/xid/model.stl", OUTPUT, ".glb", "", version)
        return calls

    def test_the_input_download_reads_the_version(self):
        assert self._convert(_load(), VERSION) == [("xid/model.stl", {"version_id": VERSION})]

    def test_no_version_downloads_the_current_object(self):
        assert self._convert(_load(), "") == [("xid/model.stl", {})]


@pytest.mark.unit
class TestDownloadSendsTheVersion:
    def _client(self, mod):
        client = MagicMock()
        return client, patch.object(mod, "_s3_client_for_bucket", lambda bucket: client)

    def test_a_version_is_passed_as_extra_args(self, tmp_path):
        mod = _load()
        client, patched = self._client(mod)
        with patched:
            mod.download("abkt", "xid/m.stl", str(tmp_path / "m.stl"), version_id=VERSION)
        client.download_fileobj.assert_called_once_with("abkt", "xid/m.stl", ANY, ExtraArgs={"VersionId": VERSION})

    def test_no_version_makes_the_call_it_made_before(self, tmp_path):
        mod = _load()
        client, patched = self._client(mod)
        with patched:
            mod.download("abkt", "xid/m.stl", str(tmp_path / "m.stl"))
        client.download_fileobj.assert_called_once_with("abkt", "xid/m.stl", ANY)

    @pytest.mark.parametrize("code", ["404", "405", "NoSuchVersion", "NoSuchKey"])
    def test_a_missing_version_fails_naming_the_file_and_version(self, tmp_path, code):
        mod = _load()
        client, patched = self._client(mod)
        client.download_fileobj.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
        with patched, pytest.raises(Exception) as excinfo:
            mod.download("abkt", "xid/m.stl", str(tmp_path / "m.stl"), version_id=VERSION)
        assert "xid/m.stl" in str(excinfo.value) and VERSION in str(excinfo.value)

    def test_other_failures_keep_the_generic_message(self, tmp_path):
        mod = _load()
        client, patched = self._client(mod)
        client.download_fileobj.side_effect = ClientError({"Error": {"Code": "403"}}, "GetObject")
        with patched, pytest.raises(Exception, match="Could not download input file from S3 bucket"):
            mod.download("abkt", "xid/m.stl", str(tmp_path / "m.stl"), version_id=VERSION)
