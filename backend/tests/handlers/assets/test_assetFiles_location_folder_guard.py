# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A prefix delete or archive whose path resolves to the asset's own location folder names the whole
asset, so it is refused like the top-level folder `/` and nothing in the bucket changes (#1943). A real
subfolder, including one named like the asset, still deletes and archives.

Layout (bucket `vams-guard`, versioned): asset `A` (database db1) at `A/` holds `x.txt`, `sub/y.txt` and a
root subfolder named like the asset, `A/z.txt` (key `A/A/z.txt`). Asset `A2` (db1) sits below a parent
folder, at `base/A2/`, and holds `x.txt` and `sub/y.txt`. Asset `M` (db1) was created from one object: its
location `m/model.glb` is not a folder, so the guard does not apply, and its companion folder holds `sub/n.txt`.
"""

import sys

import boto3
import pytest
from moto import mock_aws

import os  # noqa: E402

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level imports populate the real handler packages before the root conftest's autouse fixture runs
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

BUCKET = "vams-guard"
A_KEYS = ("A/x.txt", "A/sub/y.txt", "A/A/z.txt")
A2_KEYS = ("base/A2/x.txt", "base/A2/sub/y.txt")
M_KEYS = ("m/model.glb", "m/model.glb/sub/n.txt")
ALL_KEYS = A_KEYS + A2_KEYS + M_KEYS
ROWS = {
    "A": asset_row("db1", "A", "reg-1", "A/", BUCKET),
    "A2": asset_row("db1", "A2", "reg-1", "base/A2/", BUCKET),
    "M": asset_row("db1", "M", "reg-1", "m/model.glb", BUCKET),
}
CLAIMS = {"tokens": ["tester"], "roles": []}
DELETE_REFUSAL = ": Cannot delete the top-level asset folder"
ARCHIVE_REFUSAL = ": Cannot archive the top-level asset folder"


def _state(client):
    """Every (key, versionId, is delete marker) in the bucket, so a refusal can be shown to change nothing."""
    response = client.list_object_versions(Bucket=BUCKET)
    versions = [(v["Key"], v["VersionId"], False) for v in response.get("Versions", [])]
    markers = [(m["Key"], m["VersionId"], True) for m in response.get("DeleteMarkers", [])]
    return sorted(versions + markers)


def _live_keys(client):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def _version_keys(client):
    return sorted({v["Key"] for v in client.list_object_versions(Bucket=BUCKET).get("Versions", [])})


@pytest.fixture
def af(monkeypatch):
    from backend.backend.handlers.assets import assetFiles as module

    al.clear_nested_locations_cache()
    fa.clear_registration_cache()
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in ALL_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v2")
        monkeypatch.setattr(al, "index_ready", lambda name: True)
        monkeypatch.setattr(module, "asset_table", AttributionAssetTable(list(ROWS.values())))
        monkeypatch.setattr(module, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "get_asset_with_permissions",
                            lambda database_id, asset_id, *args, **kwargs: dict(ROWS[asset_id]))
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": "/"})
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        for name in ("send_subscription_email", "delete_assetAuxiliary_files", "delete_file_metadata",
                     "delete_file_version_records", "move_auxiliary_files", "copy_auxiliary_files"):
            monkeypatch.setattr(module, name, lambda *args, **kwargs: None)
        module.test_client = client
        yield module
    al.clear_nested_locations_cache()


@pytest.mark.unit
class TestAPrefixThatNamesTheLocationFolderIsRefused:
    @pytest.mark.parametrize("asset_id,path", [
        ("A", "/A/"), ("A", "/A"), ("A", "A/"), ("A", "A"),
        ("A2", "/base/A2/"), ("A2", "/base/A2"), ("A2", "base/A2/"),
    ])
    def test_a_prefix_delete_is_refused_and_no_version_is_removed(self, af, asset_id, path):
        before = _state(af.test_client)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", asset_id, path, True, True, CLAIMS)
        assert str(raised.value).endswith(DELETE_REFUSAL)
        assert raised.value.status_code == 400
        assert _state(af.test_client) == before

    @pytest.mark.parametrize("asset_id,path", [
        ("A", "/A/"), ("A", "/A"), ("A", "A/"), ("A", "A"),
        ("A2", "/base/A2/"), ("A2", "/base/A2"), ("A2", "base/A2/"),
    ])
    def test_a_prefix_archive_is_refused_and_nothing_is_archived(self, af, asset_id, path):
        before = _state(af.test_client)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.archive_file("db1", asset_id, path, True, CLAIMS)
        assert str(raised.value).endswith(ARCHIVE_REFUSAL)
        assert raised.value.status_code == 400
        assert _state(af.test_client) == before

    def test_the_top_level_folder_is_still_refused(self, af):
        for path in ("/", ""):
            with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
                af.delete_file("db1", "A", path, True, True, CLAIMS)
            assert str(raised.value).endswith(DELETE_REFUSAL)
            with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
                af.archive_file("db1", "A", path, True, CLAIMS)
            assert str(raised.value).endswith(ARCHIVE_REFUSAL)

    def test_authorization_is_checked_before_the_path(self, af, monkeypatch):
        def deny(*args, **kwargs):
            raise af.VAMSAuthorizationDeniedResponse()

        monkeypatch.setattr(af, "get_asset_with_permissions", deny)
        before = _state(af.test_client)
        with pytest.raises(af.VAMSAuthorizationDeniedResponse):
            af.delete_file("db1", "A", "/A/", True, True, CLAIMS)
        with pytest.raises(af.VAMSAuthorizationDeniedResponse):
            af.archive_file("db1", "A", "/A/", True, CLAIMS)
        assert _state(af.test_client) == before


@pytest.mark.unit
class TestARealSubfolderStillDeletesAndArchives:
    def test_a_subfolder_delete_removes_only_its_files(self, af):
        af.delete_file("db1", "A", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/sub/y.txt"})

    def test_a_subfolder_archive_archives_only_its_files(self, af):
        af.archive_file("db1", "A", "/sub/", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/sub/y.txt"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

    def test_the_subfolder_named_like_the_asset_deletes_by_its_full_key(self, af):
        af.delete_file("db1", "A", "/A/A/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/A/z.txt"})

    def test_the_subfolder_named_like_the_asset_archives_by_its_full_key(self, af):
        af.archive_file("db1", "A", "/A/A/", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/A/z.txt"})

    def test_a_subfolder_of_an_asset_below_a_parent_folder_deletes(self, af):
        af.delete_file("db1", "A2", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"base/A2/sub/y.txt"})

    def test_a_single_file_delete_is_unchanged(self, af):
        af.delete_file("db1", "A", "/x.txt", False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/x.txt"})

    def test_a_single_file_archive_is_unchanged(self, af):
        af.archive_file("db1", "A", "/x.txt", False, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/x.txt"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)


@pytest.mark.unit
class TestASingleObjectAssetIsNotGuarded:
    """The object's own key is the location here, so a request naming the object resolves to the location key
    and must still act on the object."""

    def test_the_object_deletes_by_its_path(self, af):
        af.delete_file("db1", "M", "/model.glb", False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb"})

    def test_the_object_archives_by_its_path(self, af):
        af.archive_file("db1", "M", "/model.glb", False, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

    def test_a_companion_subfolder_deletes_with_a_prefix(self, af):
        af.delete_file("db1", "M", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})

    def test_a_companion_subfolder_archives_with_a_prefix(self, af):
        af.archive_file("db1", "M", "/sub/", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})
