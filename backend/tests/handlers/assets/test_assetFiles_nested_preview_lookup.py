# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A file operation acts on the file's own preview companions only.

The companions of `model.glb` are `model.glb.previewFile.*` in the same folder. A file with the same
name in a subfolder, `lod1/model.glb`, is another file: delete, archive, unarchive, copy and move of
`model.glb` leave its preview where it is and as it is, while `model.glb`'s own preview follows it.
"""

import os

import boto3
import pytest
from moto import mock_aws

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "asset-bucket"
BASE_KEY = "db1/a1/"
OWN = f"{BASE_KEY}model.glb"
OWN_PREVIEW = f"{BASE_KEY}model.glb.previewFile.gif"
NESTED = f"{BASE_KEY}lod1/model.glb"
NESTED_PREVIEW = f"{BASE_KEY}lod1/model.glb.previewFile.png"
CLAIMS = {"tokens": ["alice"]}
NO_OP_HELPERS = (
    "delete_assetAuxiliary_files", "delete_file_metadata", "delete_file_version_records",
    "send_subscription_email", "write_archive_history", "copy_auxiliary_files",
    "move_auxiliary_files",
)


@pytest.fixture
def asset(monkeypatch):
    from backend.backend.handlers.assets import assetFiles as af

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in (OWN, OWN_PREVIEW, NESTED, NESTED_PREVIEW):
            client.put_object(Bucket=BUCKET, Key=key, Body=key.encode())
        monkeypatch.setattr(af, "s3_client", client)
        monkeypatch.setattr(af, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(af, "get_asset_with_permissions",
                            lambda databaseId, assetId, op, claims: {"assetId": assetId})
        monkeypatch.setattr(af, "get_asset_s3_location", lambda a: (BUCKET, BASE_KEY))
        monkeypatch.setattr(af, "_copy_file_metadata_to_destination", lambda *args: 0)
        for name in NO_OP_HELPERS:
            monkeypatch.setattr(af, name, lambda *args, **kwargs: None)
        yield af, client


def _current_body(client, key):
    return client.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def _is_current(client, key):
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def _keys(client, prefix=BASE_KEY):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", []))


def _nested_preview_untouched(client):
    assert _is_current(client, NESTED_PREVIEW)
    assert _current_body(client, NESTED_PREVIEW) == NESTED_PREVIEW.encode()
    versions = client.list_object_versions(Bucket=BUCKET, Prefix=NESTED_PREVIEW)
    assert len(versions.get("Versions", [])) == 1
    assert versions.get("DeleteMarkers", []) == []


@pytest.mark.unit
class TestTheLookupsMatchTheFilesOwnCompanions:
    def test_current_previews(self, asset):
        af, _client = asset
        assert af.find_preview_files_for_base(BUCKET, OWN) == [OWN_PREVIEW]
        assert af.find_preview_files_for_base(BUCKET, NESTED) == [NESTED_PREVIEW]

    def test_previews_including_archived(self, asset):
        af, client = asset
        client.delete_object(Bucket=BUCKET, Key=OWN_PREVIEW)
        found = af.find_preview_files_for_base_including_archived(BUCKET, OWN)
        assert [(p["key"], p["isArchived"]) for p in found] == [(OWN_PREVIEW, True)]

    def test_a_longer_file_name_is_not_a_companion(self, asset):
        af, client = asset
        client.put_object(Bucket=BUCKET, Key=f"{BASE_KEY}model.glb2.previewFile.gif", Body=b"x")
        assert f"{BASE_KEY}model.glb2.previewFile.gif" not in af.find_preview_files_for_base(BUCKET, OWN)


@pytest.mark.unit
class TestFileOperationsLeaveTheNestedFilesPreview:
    def test_delete(self, asset):
        af, client = asset
        response = af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.gif"]
        assert client.list_object_versions(Bucket=BUCKET, Prefix=OWN_PREVIEW).get("Versions", []) == []
        _nested_preview_untouched(client)

    def test_archive(self, asset):
        af, client = asset
        response = af.archive_file("db1", "a1", "/model.glb", False, CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.gif"]
        assert not _is_current(client, OWN_PREVIEW)
        _nested_preview_untouched(client)

    def test_unarchive(self, asset):
        """`lod1/model.glb` and its preview were archived on their own; unarchiving `model.glb`
        restores `model.glb` and its preview and leaves the other file's preview archived."""
        af, client = asset
        for key in (OWN, OWN_PREVIEW, NESTED, NESTED_PREVIEW):
            client.delete_object(Bucket=BUCKET, Key=key)
        response = af.unarchive_file("db1", "a1", "/model.glb", CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.gif"]
        assert _is_current(client, OWN_PREVIEW)
        assert not _is_current(client, NESTED_PREVIEW)

    def test_copy(self, asset):
        af, client = asset
        response = af.copy_file("db1", "a1", "/model.glb", "/copy.glb", None, None, CLAIMS)
        assert response.success is True
        assert _keys(client, f"{BASE_KEY}copy.glb") == [f"{BASE_KEY}copy.glb",
                                                        f"{BASE_KEY}copy.glb.previewFile.gif"]
        assert _current_body(client, f"{BASE_KEY}copy.glb.previewFile.gif") == OWN_PREVIEW.encode()
        _nested_preview_untouched(client)

    def test_move(self, asset):
        af, client = asset
        response = af.move_file("db1", "a1", "/model.glb", "/renamed.glb", CLAIMS)
        assert response.success is True
        assert _keys(client, f"{BASE_KEY}renamed.glb") == [f"{BASE_KEY}renamed.glb",
                                                           f"{BASE_KEY}renamed.glb.previewFile.gif"]
        assert _current_body(client, f"{BASE_KEY}renamed.glb.previewFile.gif") == OWN_PREVIEW.encode()
        assert not _is_current(client, OWN_PREVIEW)
        _nested_preview_untouched(client)
