# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The preview lookups return a file's own previews and nothing that only shares their prefix.

A file's preview is ``{file key}.previewFile.{ext}`` with no ``/`` in ``ext``
(``common.assetLocations``). The lookups list the prefix ``{file key}.previewFile.``, which also
matches keys inside a folder whose name starts with it. Here the single-file asset is the object
``p/model.glb`` with its preview ``p/model.glb.previewFile.png``, and the folder asset next to it is
``p/model.glb.previewFile.png/``, holding ``model.glb``, ``model.glb.previewFile.jpg`` and ``x.txt``.
Deleting, archiving, unarchiving, copying or moving the single-file asset's file, and uploading a new
preview for it, act on ``p/model.glb.previewFile.png`` only, against a versioned moto bucket.
"""

import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

BUCKET = "asset-bucket"
FILE_LOCATION = "p/model.glb"
OWN_PREVIEW = "p/model.glb.previewFile.png"
FOLDER = "p/model.glb.previewFile.png/"
FOLDER_KEYS = (f"{FOLDER}model.glb", f"{FOLDER}model.glb.previewFile.jpg", f"{FOLDER}x.txt")
CLAIMS = {"tokens": ["alice"]}
NO_OP_HELPERS = (
    "delete_assetAuxiliary_files", "delete_file_metadata", "delete_file_version_records",
    "send_subscription_email", "write_archive_history", "copy_auxiliary_files",
    "move_auxiliary_files",
)


@pytest.fixture
def bucket(monkeypatch):
    """assetFiles and uploadFile against one versioned moto bucket; the asset is the single-file location."""
    from backend.backend.handlers.assets import assetFiles as af
    from backend.backend.handlers.assets import uploadFile as uf

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in (FILE_LOCATION, OWN_PREVIEW) + FOLDER_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=key.encode())
        asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1",
                 "assetLocation": {"Key": FILE_LOCATION}}
        monkeypatch.setattr(af, "s3_client", client)
        monkeypatch.setattr(af, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(af, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(af, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET,
                                               "baseAssetsPrefix": ""})
        monkeypatch.setattr(af, "_copy_file_metadata_to_destination", lambda *args: 0)
        monkeypatch.setattr(af, "MAX_PARALLEL_S3_WORKERS", 1)
        # No other asset's location is stored in or at this one (development's nested-location read)
        monkeypatch.setattr(af, "nested_locations_for", lambda *args, **kwargs: af.NestedLocations(FILE_LOCATION))
        for name in NO_OP_HELPERS:
            monkeypatch.setattr(af, name, lambda *args, **kwargs: None)
        monkeypatch.setattr(uf, "s3", client)
        yield af, uf, client, asset


def _is_current(client, key):
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def _folder_untouched(client):
    for key in FOLDER_KEYS:
        versions = client.list_object_versions(Bucket=BUCKET, Prefix=key)
        assert [v["Key"] for v in versions.get("Versions", []) if v["Key"] == key] == [key], key
        assert [m for m in versions.get("DeleteMarkers", []) if m["Key"] == key] == [], key
        assert client.get_object(Bucket=BUCKET, Key=key)["Body"].read() == key.encode()


@pytest.mark.unit
class TestTheLookupsReturnOnlyTheFilesOwnPreviews:
    def test_current_previews(self, bucket):
        af, uf, _client, _asset = bucket
        assert uf.find_existing_preview_files(BUCKET, FILE_LOCATION) == [OWN_PREVIEW]
        assert af.find_preview_files_for_base(BUCKET, FILE_LOCATION) == [OWN_PREVIEW]

    def test_previews_including_archived(self, bucket):
        af, _uf, client, _asset = bucket
        client.delete_object(Bucket=BUCKET, Key=OWN_PREVIEW)
        client.delete_object(Bucket=BUCKET, Key=f"{FOLDER}model.glb.previewFile.jpg")
        found = af.find_preview_files_for_base_including_archived(BUCKET, FILE_LOCATION)
        assert [(p["key"], p["isArchived"]) for p in found] == [(OWN_PREVIEW, True)]


@pytest.mark.unit
class TestOperationsOnTheFileLeaveTheFolderAlone:
    def test_preview_upload_clean_up(self, bucket):
        """The upload completion builds the base key from the uploaded preview's path and removes the
        file's earlier previews before the copy (``uploadFile._move_one_file``)."""
        _af, uf, client, asset = bucket
        base_key = uf.normalize_s3_path(asset["assetLocation"]["Key"],
                                        uf.get_base_file_path("/model.glb.previewFile.png"))
        assert base_key == FILE_LOCATION
        assert uf.delete_existing_preview_files(BUCKET, base_key) == [OWN_PREVIEW]
        assert not _is_current(client, OWN_PREVIEW)
        _folder_untouched(client)

    def test_permanent_delete(self, bucket):
        af, _uf, client, _asset = bucket
        response = af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.png"]
        listed = client.list_object_versions(Bucket=BUCKET, Prefix=OWN_PREVIEW)
        assert [v for v in listed.get("Versions", []) + listed.get("DeleteMarkers", [])
                if v["Key"] == OWN_PREVIEW] == []
        _folder_untouched(client)

    def test_archive_and_unarchive(self, bucket):
        af, _uf, client, _asset = bucket
        response = af.archive_file("db1", "a1", "/model.glb", False, CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.png"]
        assert not _is_current(client, OWN_PREVIEW)
        _folder_untouched(client)
        response = af.unarchive_file("db1", "a1", "/model.glb", CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.png"]
        assert _is_current(client, OWN_PREVIEW)
        _folder_untouched(client)

    def test_copy(self, bucket):
        af, _uf, client, _asset = bucket
        response = af.copy_file("db1", "a1", "/model.glb", "/copy.glb", None, None, CLAIMS)
        assert response.success is True
        copied = sorted(o["Key"] for o in client.list_objects_v2(
            Bucket=BUCKET, Prefix=f"{FILE_LOCATION}/").get("Contents", []))
        assert copied == [f"{FILE_LOCATION}/copy.glb", f"{FILE_LOCATION}/copy.glb.previewFile.png"]
        _folder_untouched(client)

    def test_move_is_refused_and_moves_nothing(self, bucket):
        from models.common import VAMSGeneralErrorResponse
        af, _uf, client, _asset = bucket
        with pytest.raises(VAMSGeneralErrorResponse):
            af.move_file("db1", "a1", "/model.glb", "/renamed.glb", CLAIMS)
        assert _is_current(client, FILE_LOCATION) and _is_current(client, OWN_PREVIEW)
        _folder_untouched(client)
