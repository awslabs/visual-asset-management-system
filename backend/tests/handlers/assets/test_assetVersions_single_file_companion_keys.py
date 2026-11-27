# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #2023 (item 1): an asset version created from a file list, and a version revert, reach the files of a
single-file asset's companion folder.

An asset created from one existing object (location `p1/model.glb`) holds the files of its companion folder
`p1/model.glb/`, and a version names them by their path in it (`/sub/x.obj`). Creating a version from an explicit
file list and reverting to a version used to build each key as the location key plus the path with no `/` between
them (`p1/model.glbsub/x.obj`), so every such file was skipped and the reverted version listed none. The keys now
come from the asset's path mapping (`AssetFilePaths.key_for`), as the version file listing does. A folder asset
(`f1/`) is unchanged. These tests run the real version functions against a versioned moto bucket.
"""

from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from backend.backend.handlers.assets import assetVersions as _assetVersions  # noqa: F401
from models.assetsV3 import AssetFileVersionItemModel, CreateAssetVersionRequestModel, RevertAssetVersionRequestModel

BUCKET = "vams-versions"
DB = "db1"
CLAIMS = {"tokens": ["tester"], "roles": []}
SINGLE = {"assetId": "s1", "location": "p1/model.glb",
          "keys": ("p1/model.glb", "p1/model.glb/sub/x.obj", "p1/model.glb/notes.txt"),
          "paths": ("/sub/x.obj", "/notes.txt")}
FOLDER = {"assetId": "f1", "location": "f1/",
          "keys": ("f1/sub/x.obj", "f1/notes.txt"),
          "paths": ("/sub/x.obj", "/notes.txt")}


@pytest.fixture
def av(monkeypatch):
    """assetVersions with a versioned moto bucket; the asset record names SINGLE's or FOLDER's location."""
    from backend.backend.handlers.assets import assetVersions as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        first_versions = {}
        for key in SINGLE["keys"] + FOLDER["keys"]:
            first_versions[key] = client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")["VersionId"]
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v2")
        assets = {spec["assetId"]: {"databaseId": DB, "assetId": spec["assetId"], "bucketId": "b1",
                                    "assetLocation": {"Key": spec["location"]}, "currentVersionId": "1"}
                  for spec in (SINGLE, FOLDER)}
        saved = {}
        stored_versions = {}

        def save_file_versions(database_id, asset_id, version_id, files):
            saved[(asset_id, version_id)] = [dict(f) for f in files]
            return True

        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "get_asset_with_permissions",
                            lambda database_id, asset_id, *args, **kwargs: dict(assets[asset_id]))
        monkeypatch.setattr(module, "get_default_bucket_details", lambda bucket_id: {"bucketName": BUCKET})
        monkeypatch.setattr(module, "reserve_next_asset_version", lambda *args, **kwargs: "2")
        monkeypatch.setattr(module, "save_asset_file_versions", save_file_versions)
        monkeypatch.setattr(module, "save_asset_metadata_version", lambda *args, **kwargs: True)
        monkeypatch.setattr(module, "revert_asset_metadata_version", lambda *args, **kwargs: True)
        monkeypatch.setattr(module, "finalize_asset_version", lambda *args, **kwargs: {})
        monkeypatch.setattr(module, "send_subscription_email", MagicMock())
        monkeypatch.setattr(module, "delete_assetAuxiliary_files", MagicMock())
        monkeypatch.setattr(module, "get_asset_version_metadata", lambda *args, **kwargs: {"assetVersionId": "1"})
        monkeypatch.setattr(module, "get_asset_file_versions",
                            lambda database_id, asset_id, version_id: stored_versions.get((asset_id, version_id)))
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        module.test_client = client
        module.test_first_versions = first_versions
        module.test_saved = saved
        module.test_stored_versions = stored_versions
        yield module


def _key(spec, path):
    return spec["location"] + ("" if spec["location"].endswith("/") else "/") + path.lstrip("/")


def _file_list(av, spec):
    return [AssetFileVersionItemModel(relativeKey=path, versionId=av.test_first_versions[_key(spec, path)])
            for path in spec["paths"]]


def _current_body(av, key):
    return av.test_client.get_object(Bucket=BUCKET, Key=key)["Body"].read()


@pytest.mark.unit
@pytest.mark.parametrize("spec", [SINGLE, FOLDER], ids=["single-file asset", "folder asset"])
class TestAVersionFromAFileList:
    def test_holds_every_named_file(self, av, spec):
        request = CreateAssetVersionRequestModel(useLatestFiles=False, files=_file_list(av, spec), comment="from list")
        response = av.create_asset_version(DB, spec["assetId"], request, CLAIMS)
        assert response.skippedFiles is None
        assert sorted(f["relativeKey"] for f in av.test_saved[(spec["assetId"], "2")]) == sorted(spec["paths"])

    def test_records_the_named_version_of_each_file(self, av, spec):
        request = CreateAssetVersionRequestModel(useLatestFiles=False, files=_file_list(av, spec), comment="from list")
        av.create_asset_version(DB, spec["assetId"], request, CLAIMS)
        recorded = {f["relativeKey"]: f["versionId"] for f in av.test_saved[(spec["assetId"], "2")]}
        assert recorded == {path: av.test_first_versions[_key(spec, path)] for path in spec["paths"]}


@pytest.mark.unit
@pytest.mark.parametrize("spec", [SINGLE, FOLDER], ids=["single-file asset", "folder asset"])
class TestARevert:
    def _revert(self, av, spec):
        av.test_stored_versions[(spec["assetId"], "1")] = {"files": [
            {"relativeKey": path, "versionId": av.test_first_versions[_key(spec, path)], "size": 2, "etag": "e"}
            for path in spec["paths"]]}
        request = RevertAssetVersionRequestModel(assetVersionId="1", comment="back")
        return av.revert_asset_version(DB, spec["assetId"], request, CLAIMS)

    def test_restores_every_file_of_the_version(self, av, spec):
        response = self._revert(av, spec)
        assert response.skippedFiles is None
        for path in spec["paths"]:
            assert _current_body(av, _key(spec, path)) == b"v1"

    def test_the_new_version_lists_every_file(self, av, spec):
        self._revert(av, spec)
        assert sorted(f["relativeKey"] for f in av.test_saved[(spec["assetId"], "2")]) == sorted(spec["paths"])

    def test_no_key_outside_the_location_is_written(self, av, spec):
        self._revert(av, spec)
        keys = {o["Key"] for o in av.test_client.list_objects_v2(Bucket=BUCKET).get("Contents", [])}
        assert keys == set(SINGLE["keys"] + FOLDER["keys"])


@pytest.mark.unit
class TestTheOwnFileOfASingleFileAsset:
    def test_a_revert_leaves_the_own_object_alone(self, av):
        before = av.test_client.list_object_versions(Bucket=BUCKET, Prefix="p1/model.glb")["Versions"]
        own_before = [v["VersionId"] for v in before if v["Key"] == "p1/model.glb"]
        TestARevert()._revert(av, SINGLE)
        after = av.test_client.list_object_versions(Bucket=BUCKET, Prefix="p1/model.glb")["Versions"]
        assert [v["VersionId"] for v in after if v["Key"] == "p1/model.glb"] == own_before

    def test_a_path_with_a_dot_dot_segment_is_skipped(self, av):
        files = [AssetFileVersionItemModel.construct(relativeKey="/sub/../../p2/other.obj", versionId="v",
                                                     isArchived=False)]
        request = CreateAssetVersionRequestModel.construct(useLatestFiles=False, files=files, comment="c",
                                                           versionAlias=None)
        response = av.create_asset_version(DB, "s1", request, CLAIMS)
        assert response.skippedFiles == ["/sub/../../p2/other.obj"]
        assert av.test_saved[("s1", "2")] == []
