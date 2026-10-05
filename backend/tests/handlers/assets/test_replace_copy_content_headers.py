# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A copy that replaces an asset file's user metadata keeps the file's content headers.

A ``MetadataDirective: REPLACE`` copy resets every system-defined header the request does not
restate, so the new version reads back as ``binary/octet-stream``. The managed copy does not
restore them on its multipart path either: s3transfer returns the caller's arguments unchanged
under ``REPLACE``. Each copy below writes a new object version with VAMS metadata, so each must
restate the source's ``ContentType`` and the other ``COPY_PRESERVED_HEADER_FIELDS`` from the
source's ``head_object`` response.
"""

import os
import types
from datetime import datetime

import pytest

from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    VAMS_PRIMARY_TYPE_METADATA_KEY,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_SOURCE_FILE_COPY,
    VAMS_CHANGE_SOURCE_FILE_MOVE,
    VAMS_CHANGE_SOURCE_FILE_RENAME,
    VAMS_CHANGE_SOURCE_FILE_UNARCHIVE,
    VAMS_CHANGE_SOURCE_FILE_REVERT,
    VAMS_CHANGE_SOURCE_UPLOAD,
)

# Env vars assetFiles and uploadFile read at import time (set before importing them).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level imports ensure the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402

BUCKET = "asset-bucket"
BASE_KEY = "db1/a1/"
REL_PATH = "/part.glb"
FULL_KEY = "db1/a1/part.glb"

# The system-defined headers of a typed source object, as head_object returns them.
CONTENT_HEADERS = {
    "ContentType": "model/gltf-binary",
    "ContentEncoding": "gzip",
    "ContentDisposition": 'attachment; filename="part.glb"',
    "ContentLanguage": "en",
    "CacheControl": "max-age=60",
}


def _source_head():
    return {
        **CONTENT_HEADERS,
        "ContentLength": 10,
        "VersionId": "src-ver-1",
        "Metadata": {DATABASE_ID_METADATA_KEY: "db1", ASSET_ID_METADATA_KEY: "a1", "custom": "keep"},
    }


def _assert_content_headers_kept(extra_args):
    assert extra_args.get("MetadataDirective") == "REPLACE"
    for field, value in CONTENT_HEADERS.items():
        assert extra_args.get(field) == value, field


class _FakeS3:
    """Fake S3 client and resource that record every managed copy and head_object call."""

    def __init__(self, head, versions=None, delete_markers=None):
        self._head = head
        self._versions = versions or []
        self._delete_markers = delete_markers or []
        self.copies = []
        self.head_calls = []
        self.deleted = []
        fake = self

        class _ManagedClient:
            def copy(self, CopySource=None, Bucket=None, Key=None, ExtraArgs=None, **kwargs):
                fake.copies.append({"CopySource": CopySource, "Bucket": Bucket, "Key": Key,
                                    "ExtraArgs": ExtraArgs})

        class _Object:
            def __init__(self, bucket, key):
                self.bucket = bucket
                self.key = key

            def copy(self, CopySource=None, ExtraArgs=None, **kwargs):
                fake.copies.append({"CopySource": CopySource, "Bucket": self.bucket, "Key": self.key,
                                    "ExtraArgs": ExtraArgs})

        class _Resource:
            meta = types.SimpleNamespace(client=_ManagedClient())

            def Object(self, bucket, key):
                return _Object(bucket, key)

        self.resource = _Resource()

    def head_object(self, Bucket, Key, VersionId=None, **kwargs):
        self.head_calls.append((Bucket, Key, VersionId))
        return dict(self._head)

    def delete_object(self, Bucket, Key, **kwargs):
        self.deleted.append(Key)
        return {}

    def list_object_versions(self, Bucket, Prefix, **kwargs):
        return {"Versions": list(self._versions), "DeleteMarkers": list(self._delete_markers)}


def _patch_asset_files(monkeypatch, fake):
    af = assetFiles
    monkeypatch.setattr(af, "s3_client", fake)
    monkeypatch.setattr(af, "s3_resource", fake.resource)
    monkeypatch.setattr(af, "get_asset_with_permissions",
                        lambda databaseId, assetId, op, claims: {"assetId": assetId})
    monkeypatch.setattr(af, "get_asset_s3_location", lambda asset: (BUCKET, BASE_KEY))
    monkeypatch.setattr(af, "send_subscription_email", lambda db, a: None)
    return af


@pytest.mark.unit
class TestFileOperationCopiesKeepContentHeaders:
    def test_copy_to_another_asset_keeps_content_headers(self, monkeypatch):
        fake = _FakeS3(_source_head())
        af = _patch_asset_files(monkeypatch, fake)

        assert af.copy_s3_object(
            BUCKET, FULL_KEY, BUCKET, "db1/a2/part.glb",
            source_asset_id="a1", source_database_id="db1",
            dest_asset_id="a2", dest_database_id="db1",
            change_source=VAMS_CHANGE_SOURCE_FILE_COPY, change_user_id="alice",
            source_rel_path=REL_PATH) is True

        extra = fake.copies[0]["ExtraArgs"]
        _assert_content_headers_kept(extra)
        # User metadata is still replaced, and the cross-account ACL is still granted.
        assert extra["Metadata"][ASSET_ID_METADATA_KEY] == "a2"
        assert extra["Metadata"]["custom"] == "keep"
        assert extra["Metadata"][VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_FILE_COPY
        assert extra["ACL"] == "bucket-owner-full-control"

    def test_copy_without_a_metadata_change_keeps_the_copy_directive(self, monkeypatch):
        # Control: a same-asset copy with no provenance takes the COPY branch, where S3 carries the
        # headers itself. It neither heads the source nor restates headers.
        fake = _FakeS3(_source_head())
        af = _patch_asset_files(monkeypatch, fake)

        assert af.copy_s3_object(BUCKET, FULL_KEY, BUCKET, "db1/a1/copy.glb") is True

        assert fake.copies[0]["ExtraArgs"] == {"ACL": "bucket-owner-full-control"}
        assert fake.head_calls == []

    @pytest.mark.parametrize("change_source,dest_key", [
        (VAMS_CHANGE_SOURCE_FILE_MOVE, "db1/a1/moved/part.glb"),
        (VAMS_CHANGE_SOURCE_FILE_RENAME, "db1/a1/renamed.glb"),
    ])
    def test_move_and_rename_keep_content_headers(self, monkeypatch, change_source, dest_key):
        fake = _FakeS3(_source_head())
        af = _patch_asset_files(monkeypatch, fake)

        assert af.move_s3_object(
            BUCKET, FULL_KEY, BUCKET, dest_key,
            change_source=change_source, change_user_id="alice",
            from_db="db1", from_asset="a1", from_path=REL_PATH) is True

        extra = fake.copies[0]["ExtraArgs"]
        _assert_content_headers_kept(extra)
        assert extra["Metadata"][VAMS_CHANGE_SOURCE_METADATA_KEY] == change_source
        assert extra["ACL"] == "bucket-owner-full-control"
        assert fake.deleted == [FULL_KEY]

    def test_unarchive_keeps_content_headers(self, monkeypatch):
        versions = [{"Key": FULL_KEY, "VersionId": "v1", "IsLatest": False,
                     "LastModified": datetime(2026, 6, 8)}]
        delete_markers = [{"Key": FULL_KEY, "VersionId": "marker-latest", "IsLatest": True,
                           "LastModified": datetime(2026, 6, 11)}]
        fake = _FakeS3(_source_head(), versions, delete_markers)
        af = _patch_asset_files(monkeypatch, fake)
        monkeypatch.setattr(af, "find_preview_files_for_base_including_archived",
                            lambda bucket, base_key: [])

        result = af.unarchive_file("db1", "a1", REL_PATH, {"tokens": ["alice"]})

        assert result.success is True
        assert fake.copies[0]["CopySource"]["VersionId"] == "v1"
        extra = fake.copies[0]["ExtraArgs"]
        _assert_content_headers_kept(extra)
        assert extra["Metadata"][VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_FILE_UNARCHIVE
        assert extra["ACL"] == "bucket-owner-full-control"

    def test_revert_keeps_content_headers(self, monkeypatch):
        fake = _FakeS3(_source_head())
        af = _patch_asset_files(monkeypatch, fake)
        monkeypatch.setattr(af, "get_s3_object_metadata", lambda bucket, key, include_versions=False: {
            "versions": [
                {"versionId": "v2", "isLatest": True, "isArchived": False},
                {"versionId": "v1", "isLatest": False, "isArchived": False},
            ],
        })
        monkeypatch.setattr(af, "delete_assetAuxiliary_files", lambda prefix: None)

        result = af.revert_file_version("db1", "a1", REL_PATH, "v1", {"tokens": ["alice"]})

        assert result.success is True
        assert fake.copies[0]["CopySource"]["VersionId"] == "v1"
        assert (BUCKET, FULL_KEY, "v1") in fake.head_calls
        extra = fake.copies[0]["ExtraArgs"]
        _assert_content_headers_kept(extra)
        assert extra["Metadata"][VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_FILE_REVERT
        assert extra["ACL"] == "bucket-owner-full-control"

    def test_set_primary_type_keeps_every_content_header(self, monkeypatch):
        fake = _FakeS3(_source_head())
        af = _patch_asset_files(monkeypatch, fake)
        monkeypatch.setattr(af, "is_file_archived", lambda bucket, key, version_id=None: False)

        result = af.set_primary_file("db1", "a1", REL_PATH, "primary", None, {"tokens": ["alice"]})

        assert result.success is True
        extra = fake.copies[0]["ExtraArgs"]
        _assert_content_headers_kept(extra)
        assert extra["Metadata"][VAMS_PRIMARY_TYPE_METADATA_KEY] == "primary"
        assert extra["ACL"] == "bucket-owner-full-control"


@pytest.mark.unit
class TestUploadFinalizeCopiesKeepContentHeaders:
    """The temporary-to-final copy of an upload, including a workflow output that was staged with a
    type, keeps the staged object's content headers."""

    def test_upload_finalize_copy_keeps_content_headers(self, monkeypatch):
        fake = _FakeS3(_source_head())
        uf = uploadFile
        monkeypatch.setattr(uf, "s3", fake)
        monkeypatch.setattr(uf, "s3_resource", fake.resource)

        assert uf.copy_s3_object(
            "run-bucket", "temp/up-1/part.glb", BUCKET, FULL_KEY, "db1", "a1",
            extra_metadata={VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_UPLOAD}) is True

        extra = fake.copies[0]["ExtraArgs"]
        _assert_content_headers_kept(extra)
        assert fake.head_calls == [("run-bucket", "temp/up-1/part.glb", None)]
        # The final object's user metadata is built fresh, not copied from the staged object.
        assert extra["Metadata"] == {DATABASE_ID_METADATA_KEY: "db1", ASSET_ID_METADATA_KEY: "a1",
                                     VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_UPLOAD}
        assert extra["ACL"] == "bucket-owner-full-control"

    def test_upload_finalize_copy_reuses_the_callers_head(self, monkeypatch):
        # The completion loops already HEAD the staged object; handing that response to the copy
        # restates its headers without another request. The fake's own HEAD carries no headers.
        fake = _FakeS3({})
        uf = uploadFile
        monkeypatch.setattr(uf, "s3", fake)
        monkeypatch.setattr(uf, "s3_resource", fake.resource)

        assert uf.copy_s3_object(
            "run-bucket", "temp/up-1/part.glb", BUCKET, FULL_KEY, "db1", "a1",
            extra_metadata={VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_UPLOAD},
            source_head=_source_head()) is True

        _assert_content_headers_kept(fake.copies[0]["ExtraArgs"])
        assert fake.head_calls == []

    def test_large_file_finalize_copy_keeps_content_headers(self, monkeypatch):
        fake = _FakeS3(_source_head())
        sq = sqsUploadFileLarge
        monkeypatch.setattr(sq, "s3", fake)
        monkeypatch.setattr(sq, "s3_resource", fake.resource)

        assert sq.copy_s3_object(
            "run-bucket", "temp/up-1/part.glb", BUCKET, FULL_KEY, "db1", "a1",
            change_metadata={VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_UPLOAD}) is True

        extra = fake.copies[0]["ExtraArgs"]
        _assert_content_headers_kept(extra)
        assert fake.head_calls == [("run-bucket", "temp/up-1/part.glb", None)]
        assert extra["Metadata"][DATABASE_ID_METADATA_KEY] == "db1"
        assert "custom" not in extra["Metadata"]
        assert extra["ACL"] == "bucket-owner-full-control"


@pytest.mark.unit
class TestReplaceMetadataCopyArgs:
    def test_a_header_the_source_does_not_carry_is_left_out(self):
        # The most common source carries only a ContentType. A header it lacks, or carries empty,
        # is omitted rather than sent as None or "", and an untyped source gets no invented type.
        from common.s3MetadataKeys import replace_metadata_copy_args

        metadata = {DATABASE_ID_METADATA_KEY: "db1"}
        typed = {"ContentType": "model/gltf-binary", "ContentEncoding": "", "ContentLength": 10,
                 "VersionId": "src-ver-1", "Metadata": {"custom": "keep"}}

        assert replace_metadata_copy_args(typed, metadata) == {
            "Metadata": metadata,
            "MetadataDirective": "REPLACE",
            "ContentType": "model/gltf-binary",
        }
        assert replace_metadata_copy_args({"ContentLength": 10}, metadata) == {
            "Metadata": metadata,
            "MetadataDirective": "REPLACE",
        }
