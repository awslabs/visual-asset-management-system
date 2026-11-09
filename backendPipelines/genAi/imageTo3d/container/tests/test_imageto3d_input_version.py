# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every view is downloaded at the version the run was launched on (#994).

constructPipeline puts each view's version on its ``inputFiles`` entry (and the primary's on
``inputFile``). ``download_input_image`` sends it as ``VersionId``; an entry without one makes exactly
the call it made before, and a version that no longer exists fails the run naming the file and version.
"""

import importlib.util
import os
import sys
from unittest.mock import ANY, MagicMock

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


@pytest.fixture
def main_module():
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main_versions", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def s3mod():
    from vams_utils.aws import s3
    return s3


@pytest.mark.unit
class TestEachViewIsReadAtItsVersion:
    def _download(self, main_module, monkeypatch, tmp_path, entry):
        calls = []

        def _fake(bucket, key, path, **kwargs):
            calls.append((key, kwargs))
            return path

        monkeypatch.setattr(main_module, "INPUT_DIR", tmp_path)
        monkeypatch.setattr(main_module.s3, "download", _fake)
        main_module.download_input_image(entry)
        return calls

    def test_a_view_with_a_version_downloads_it(self, main_module, monkeypatch, tmp_path):
        calls = self._download(main_module, monkeypatch, tmp_path,
                               {"bucketName": "b", "objectKey": "x/cat_front.png", "versionId": VERSION})
        assert calls == [("x/cat_front.png", {"version_id": VERSION})]

    @pytest.mark.parametrize("entry", [
        {"bucketName": "b", "objectKey": "x/cat_front.png"},
        {"bucketName": "b", "objectKey": "x/cat_front.png", "versionId": ""},
    ])
    def test_a_view_without_one_downloads_the_current_object(self, main_module, monkeypatch, tmp_path, entry):
        assert self._download(main_module, monkeypatch, tmp_path, entry) == [("x/cat_front.png", {})]

    def test_a_deleted_version_fails_naming_the_file_and_version(self, main_module, monkeypatch, tmp_path, s3mod):
        def _missing(bucket, key, path, **kwargs):
            raise s3mod.InputVersionMissingError(key, kwargs["version_id"])

        monkeypatch.setattr(main_module, "INPUT_DIR", tmp_path)
        monkeypatch.setattr(main_module.s3, "download", _missing)
        with pytest.raises(s3mod.InputVersionMissingError) as excinfo:
            main_module.download_input_image({"bucketName": "b", "objectKey": "x/cat.png", "versionId": VERSION})
        assert "x/cat.png" in str(excinfo.value) and VERSION in str(excinfo.value)


@pytest.mark.unit
class TestOneFileAtTwoVersionsIsTwoInputs:
    """A launch may select one file at two versions; the views are de-duplicated by key AND version."""

    OLDER = "older.Kq2Zr8vS0dJcVb1xYfT9wE3uN"

    def _download_all(self, main_module, monkeypatch, tmp_path, entries):
        calls = []

        def _fake(bucket, key, path, **kwargs):
            calls.append((key, kwargs.get("version_id", ""), path))
            return path

        monkeypatch.setattr(main_module, "INPUT_DIR", tmp_path)
        monkeypatch.setattr(main_module.s3, "download", _fake)
        return main_module.download_input_images({"inputFiles": entries}), calls

    def test_both_versions_are_downloaded_to_their_own_paths(self, main_module, monkeypatch, tmp_path):
        paths, calls = self._download_all(main_module, monkeypatch, tmp_path, [
            {"bucketName": "b", "objectKey": "x/cat_front.png", "versionId": VERSION},
            {"bucketName": "b", "objectKey": "x/cat_front.png", "versionId": self.OLDER},
        ])
        assert [(key, version) for key, version, _ in calls] == [
            ("x/cat_front.png", VERSION), ("x/cat_front.png", self.OLDER)]
        assert len(set(paths)) == 2
        # The file keeps its name, which is what names the view it shows.
        assert [p.name for p in paths] == ["cat_front.png", "cat_front.png"]
        assert paths[0] == tmp_path / "cat_front.png"

    @pytest.mark.parametrize("second", [VERSION, None])
    def test_the_same_key_at_the_same_version_is_downloaded_once(self, main_module, monkeypatch, tmp_path, second):
        first = {"bucketName": "b", "objectKey": "x/cat_front.png"}
        if second:
            first["versionId"] = second
        repeat = dict(first, versionId=second or "")
        paths, calls = self._download_all(main_module, monkeypatch, tmp_path, [first, repeat])
        assert len(paths) == 1 and len(calls) == 1

    def test_distinct_files_keep_the_flat_layout(self, main_module, monkeypatch, tmp_path):
        paths, _ = self._download_all(main_module, monkeypatch, tmp_path, [
            {"bucketName": "b", "objectKey": "x/cat_front.png", "versionId": VERSION},
            {"bucketName": "b", "objectKey": "x/cat_back.png", "versionId": VERSION},
        ])
        assert paths == [tmp_path / "cat_front.png", tmp_path / "cat_back.png"]


@pytest.mark.unit
class TestDownloadSendsTheVersion:
    @pytest.fixture
    def bucket_client(self, s3mod, monkeypatch):
        if isinstance(s3mod.ClientError, MagicMock) or not isinstance(s3mod.ClientError, type):
            pytest.skip("botocore is stubbed in this environment")
        client = MagicMock()
        monkeypatch.setattr(s3mod, "client_for_bucket", lambda bucket: client)
        return client

    def test_a_version_is_passed_as_extra_args(self, s3mod, bucket_client, tmp_path):
        s3mod.download("b", "x/cat.png", str(tmp_path / "cat.png"), version_id=VERSION)
        bucket_client.download_fileobj.assert_called_once_with(
            "b", "x/cat.png", ANY, Config=ANY, ExtraArgs={"VersionId": VERSION})

    def test_no_version_makes_the_call_it_made_before(self, s3mod, bucket_client, tmp_path):
        s3mod.download("b", "x/cat.png", str(tmp_path / "cat.png"))
        bucket_client.download_fileobj.assert_called_once_with("b", "x/cat.png", ANY, Config=ANY)

    @pytest.mark.parametrize("code", ["404", "405", "NoSuchVersion", "NoSuchKey"])
    def test_a_missing_version_raises(self, s3mod, bucket_client, tmp_path, code):
        bucket_client.download_fileobj.side_effect = s3mod.ClientError({"Error": {"Code": code}}, "GetObject")
        with pytest.raises(s3mod.InputVersionMissingError):
            s3mod.download("b", "x/cat.png", str(tmp_path / "cat.png"), version_id=VERSION)

    def test_other_failures_still_return_none(self, s3mod, bucket_client, tmp_path):
        bucket_client.download_fileobj.side_effect = s3mod.ClientError({"Error": {"Code": "403"}}, "GetObject")
        assert s3mod.download("b", "x/cat.png", str(tmp_path / "cat.png"), version_id=VERSION) is None
