# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A delete or archive whose path resolves to the key the file list shows at `/` names the asset's top-level
folder, so it is refused like `/` and nothing in the bucket changes (#1943): a folder location itself (its
folder marker, which every created asset has), or a single-file location's companion folder `{key}/`, named
as a folder (`isPrefix` true) or as a single key (`isPrefix` false). A real subfolder, including one named
like the asset, still deletes and archives. A path with a leading `/` is a path in the asset (PR #1965), so
`/A/` is the subfolder named like the asset (`A/A/`), not the location folder, and the `//` folder (`A//`,
keys with an empty segment after the location, #1977) is a subfolder too.

Layout (bucket `vams-guard`, versioned): asset `A` (database db1) at `A/` holds its folder marker `A/`,
`x.txt`, `sub/y.txt` and a root subfolder named like the asset, `A/z.txt` (key `A/A/z.txt`). Asset `A2` (db1)
sits below a parent folder, at `base/A2/`, and holds its marker, `x.txt` and `sub/y.txt`. Asset `L` (db1) has a
location stored with a leading `/`, `/lead/`, and holds the marker `lead/` and `x.txt`. Asset `M` (db1) was
created from one object: its location `m/model.glb` is not a folder, so the object's own key is the location
and still deletes and archives; its companion folder `m/model.glb/` holds a marker and `sub/n.txt`.
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
A_KEYS = ("A/", "A/x.txt", "A/sub/y.txt", "A/A/z.txt")
A2_KEYS = ("base/A2/", "base/A2/x.txt", "base/A2/sub/y.txt")
L_KEYS = ("lead/", "lead/x.txt")
M_KEYS = ("m/model.glb", "m/model.glb/", "m/model.glb/sub/n.txt")
ALL_KEYS = A_KEYS + A2_KEYS + L_KEYS + M_KEYS
ROWS = {
    "A": asset_row("db1", "A", "reg-1", "A/", BUCKET),
    "A2": asset_row("db1", "A2", "reg-1", "base/A2/", BUCKET),
    "L": asset_row("db1", "L", "reg-1", "/lead/", BUCKET),
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
        ("A", "A/"), ("A", "A"), ("A2", "base/A2/"), ("A2", "base/A2"), ("L", "lead/"), ("L", "lead"),
        ("M", "m/model.glb/"),
    ])
    def test_a_prefix_delete_is_refused_and_no_version_is_removed(self, af, asset_id, path):
        before = _state(af.test_client)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", asset_id, path, True, True, CLAIMS)
        assert str(raised.value).endswith(DELETE_REFUSAL)
        assert raised.value.status_code == 400
        assert _state(af.test_client) == before

    @pytest.mark.parametrize("asset_id,path", [
        ("A", "A/"), ("A", "A"), ("A2", "base/A2/"), ("A2", "base/A2"), ("L", "lead/"), ("L", "lead"),
        ("M", "m/model.glb/"),
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

    @pytest.mark.parametrize("is_prefix", [True, False], ids=["prefix", "single-key"])
    def test_authorization_is_checked_before_the_path(self, af, monkeypatch, is_prefix):
        def deny(*args, **kwargs):
            raise af.VAMSAuthorizationDeniedResponse()

        monkeypatch.setattr(af, "get_asset_with_permissions", deny)
        before = _state(af.test_client)
        with pytest.raises(af.VAMSAuthorizationDeniedResponse):
            af.delete_file("db1", "A", "A/", is_prefix, True, CLAIMS)
        with pytest.raises(af.VAMSAuthorizationDeniedResponse):
            af.archive_file("db1", "A", "A/", is_prefix, CLAIMS)
        assert _state(af.test_client) == before


SINGLE_KEY_REFUSED = [("A", "A/"), ("A2", "base/A2/"), ("L", "lead/"), ("M", "m/model.glb/")]
RECORD_HELPERS = ("delete_assetAuxiliary_files", "delete_file_metadata", "delete_files_metadata",
                  "delete_file_version_records", "send_subscription_email")


def _record_helper_calls(af, monkeypatch):
    """The viewer-data, metadata, version-record and notification calls a request makes."""
    calls = []
    for name in RECORD_HELPERS:
        monkeypatch.setattr(af, name, lambda *args, _name=name, **kwargs: calls.append(_name))
    return calls


@pytest.mark.unit
class TestASingleKeyRequestForTheTopLevelFolderIsRefused:
    """With `isPrefix` false, the path names the key itself: the location folder's marker, or a single-file
    asset's companion folder marker. Both are the entry the file list shows at `/`."""

    @pytest.mark.parametrize("asset_id,path", SINGLE_KEY_REFUSED)
    def test_a_single_key_delete_is_refused_and_no_version_is_removed(self, af, monkeypatch, asset_id, path):
        calls = _record_helper_calls(af, monkeypatch)
        before = _state(af.test_client)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", asset_id, path, False, True, CLAIMS)
        assert str(raised.value).endswith(DELETE_REFUSAL)
        assert raised.value.status_code == 400
        assert _state(af.test_client) == before
        assert calls == []

    @pytest.mark.parametrize("asset_id,path", SINGLE_KEY_REFUSED)
    def test_a_single_key_archive_is_refused_and_nothing_is_archived(self, af, monkeypatch, asset_id, path):
        calls = _record_helper_calls(af, monkeypatch)
        before = _state(af.test_client)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.archive_file("db1", asset_id, path, False, CLAIMS)
        assert str(raised.value).endswith(ARCHIVE_REFUSAL)
        assert raised.value.status_code == 400
        assert _state(af.test_client) == before
        assert calls == []

    def test_the_marker_of_the_subfolder_named_like_the_asset_still_deletes_by_its_path(self, af, monkeypatch):
        """'/A/' is the path of 'A/A/', so its folder marker is a single key of a subfolder, not the location."""
        calls = _record_helper_calls(af, monkeypatch)
        af.test_client.put_object(Bucket=BUCKET, Key="A/A/", Body=b"")
        af.delete_file("db1", "A", "/A/", False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)
        assert "delete_file_version_records" in calls

    def test_a_file_of_an_asset_with_a_leading_slash_location_still_deletes(self, af):
        af.delete_file("db1", "L", "/x.txt", False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"lead/x.txt"})


@pytest.mark.unit
class TestARealSubfolderStillDeletesAndArchives:
    def test_a_subfolder_delete_removes_only_its_files(self, af):
        af.delete_file("db1", "A", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/sub/y.txt"})

    def test_a_subfolder_archive_archives_only_its_files(self, af):
        af.archive_file("db1", "A", "/sub/", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/sub/y.txt"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

    @pytest.mark.parametrize("path", ["A/A/", "/A/", "/A"], ids=["full-key", "path", "path-no-slash"])
    def test_the_subfolder_named_like_the_asset_deletes_by_its_full_key_or_its_path(self, af, path):
        af.delete_file("db1", "A", path, True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/A/z.txt"})

    @pytest.mark.parametrize("path", ["A/A/", "/A/"], ids=["full-key", "path"])
    def test_the_subfolder_named_like_the_asset_archives_by_its_full_key_or_its_path(self, af, path):
        af.archive_file("db1", "A", path, True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/A/z.txt"})

    def test_a_path_that_repeats_a_parent_folder_names_no_folder_of_the_asset(self, af):
        """'/base/A2/' is the path of 'base/A2/base/A2/', which holds nothing: answered as a missing folder."""
        before = _state(af.test_client)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", "A2", "/base/A2/", True, True, CLAIMS)
        assert "Cannot delete the top-level asset folder" not in str(raised.value)
        assert _state(af.test_client) == before

    def test_the_empty_segment_folder_deletes_and_archives(self, af):
        """#1977: '//' resolves to 'A//', not to the location folder 'A/'."""
        af.test_client.put_object(Bucket=BUCKET, Key="A//w.txt", Body=b"w")
        af.archive_file("db1", "A", "//", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(ALL_KEYS)
        af.delete_file("db1", "A", "//", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

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
class TestTheObjectOfASingleObjectAssetIsNotGuarded:
    """The object's own key is the location here, so a request naming the object resolves to the location key
    and must still act on the object."""

    @pytest.mark.parametrize("path", ["/model.glb", "m/model.glb"], ids=["path", "full-key"])
    def test_the_object_deletes_by_its_path_or_its_key(self, af, path):
        af.delete_file("db1", "M", path, False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb"})

    @pytest.mark.parametrize("path", ["/model.glb", "m/model.glb"], ids=["path", "full-key"])
    def test_the_object_archives_by_its_path_or_its_key(self, af, path):
        af.archive_file("db1", "M", path, False, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

    def test_a_companion_subfolder_deletes_with_a_prefix(self, af):
        af.delete_file("db1", "M", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})

    def test_a_companion_subfolder_archives_with_a_prefix(self, af):
        af.archive_file("db1", "M", "/sub/", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})
