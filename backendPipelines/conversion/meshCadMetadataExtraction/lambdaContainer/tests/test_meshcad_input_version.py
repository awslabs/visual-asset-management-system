#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/meshCadMetadataExtraction: attributes are extracted from the version the run was launched on (#994).

The handler reads the workflow manifest itself. ``resolve_inputs_from_manifest`` returns the first
input file's ``versionId`` ("" for none, "" or a legacy payload; "null" is sent), the handler passes it to
``extract_metadata``, and the input download sends it as ``VersionId``. A run without a version makes
the call it made before, and a version that no longer exists fails the run naming the file and the
version."""

import os
import sys
from unittest.mock import ANY, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_manifest_contract import (  # noqa: E402
    _OUTPUT_METADATA_PREFIX, _RUN_BUCKET, _load, _manifest, _original_entry)

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
OUTPUT = f"s3://{_RUN_BUCKET}/{_OUTPUT_METADATA_PREFIX}"


def _resolve(mod, manifest):
    with patch.object(mod, "fetch_manifest", MagicMock(return_value=manifest)):
        return mod.resolve_inputs_from_manifest({"inputManifestS3Location": "s3://abkt/m.json"})


@pytest.mark.unit
class TestTheResolverReturnsTheVersion:
    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_first_input_files_version(self, version_id):
        entry = dict(_original_entry(), versionId=version_id)
        assert _resolve(_load(), _manifest([entry]))[3] == version_id

    @pytest.mark.parametrize("version_id", ["", None])
    def test_no_version_to_send(self, version_id):
        entry = dict(_original_entry(), versionId=version_id)
        assert _resolve(_load(), _manifest([entry]))[3] == ""

    def test_a_legacy_payload_has_no_version(self):
        mod = _load()
        with patch.object(mod, "fetch_manifest", MagicMock(return_value=None)):
            resolved = mod.resolve_inputs_from_manifest(
                {"inputS3AssetFilePath": "s3://abkt/xid/m.stl", "outputS3AssetMetadataPath": OUTPUT})
        assert resolved[3] == ""


@pytest.mark.unit
class TestTheExtractionReadsTheVersion:
    """The download stub writes the bytes the read would return, so the input text check (#1044)
    runs for real on the version the run read."""

    ASCII_STL = b"solid pump\nendsolid pump\n"

    def _extract(self, mod, version, content=ASCII_STL):
        calls = []

        def _download(bucket, key, path, **kwargs):
            calls.append((key, kwargs))
            with open(path, "wb") as handle:
                handle.write(content)
            return path

        with patch.object(mod, "download", MagicMock(side_effect=_download)), \
                patch.object(mod, "upload", MagicMock(side_effect=lambda bucket, key, path: key)), \
                patch.object(mod, "extract_mesh_metadata", MagicMock(return_value={"volume": 1.5})), \
                patch.object(mod, "get_handler_for_format", MagicMock(return_value="mesh")):
            mod.extract_metadata("test/pump.stl", "s3://abkt/xid/test/pump.stl", OUTPUT, version)
        return calls

    def test_the_input_download_reads_the_version(self):
        assert self._extract(_load(), VERSION) == [("xid/test/pump.stl", {"version_id": VERSION})]

    def test_no_version_downloads_the_current_object(self):
        assert self._extract(_load(), "") == [("xid/test/pump.stl", {})]

    def test_a_version_that_is_not_utf8_fails_naming_the_file(self):
        # "solid caf\xe9": Latin-1, not UTF-8, in an STL that is not laid out as binary.
        with pytest.raises(Exception) as excinfo:
            self._extract(_load(), VERSION, content=b"solid caf\xe9\nendsolid caf\xe9\n")
        message = str(excinfo.value)
        assert message.startswith("Attribute extraction failed: pump.stl could not be read as STL")
        assert "is not UTF-8" in message


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
