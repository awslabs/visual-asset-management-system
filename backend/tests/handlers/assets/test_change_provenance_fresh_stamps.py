# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Change provenance on the operations that write a new version by copying an existing one.

Set primary type and preview-file unarchive each copy an existing Amazon S3 version forward as
the new current version. The new version must record the operation that wrote it, not the
provenance of the writer of that source version: sqsBucketSync turns these keys into the
version's history row, and the fileUpload trigger dispatcher reads them to decide whether a
workflow may fire. Every source here is workflow-written, because its `workflowExecution` stamp
is the value that misleads both consumers when it is carried forward.
"""

import os
import pytest
from unittest.mock import MagicMock

from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    VAMS_PRIMARY_TYPE_METADATA_KEY,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY,
    CHANGE_PROVENANCE_METADATA_KEYS,
)

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-level import ensures the real backend.backend.handlers.assets package is populated in
# sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "asset-bucket"
BASE_KEY = "db1/a1/"
REL_PATH = "/model.glb"
FULL_KEY = "db1/a1/model.glb"
PREVIEW_KEY = "db1/a1/model.glb.previewFile.png"
CLAIMS = {"tokens": ["alice"]}


def _workflow_written_metadata(**overrides):
    """User metadata of a version written by a workflow execution."""
    metadata = {
        ASSET_ID_METADATA_KEY: "a1",
        DATABASE_ID_METADATA_KEY: "db1",
        VAMS_CHANGE_SOURCE_METADATA_KEY: "workflowExecution",
        VAMS_CHANGE_USER_ID_METADATA_KEY: "SYSTEM_USER",
        VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY: "conversion-3d-basic",
        VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: "exec-571a47bf",
        VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY: "",
        VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY: "",
        VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY: "",
        VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY: "",
    }
    metadata.update(overrides)
    return metadata


def _assert_fresh_stamp(metadata, change_source, user_id, from_version):
    """Every provenance key describes the copying operation; nothing of the source's survives."""
    assert metadata[VAMS_CHANGE_SOURCE_METADATA_KEY] == change_source
    assert metadata[VAMS_CHANGE_USER_ID_METADATA_KEY] == user_id
    assert metadata[VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY] == from_version
    assert metadata[VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY] == ""
    assert metadata[VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY] == ""
    assert metadata[VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY] == ""
    assert metadata[VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY] == ""
    assert metadata[VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY] == ""
    assert CHANGE_PROVENANCE_METADATA_KEYS <= set(metadata)


class _ManagedCopyS3:
    """S3 resource double that records each managed copy (`s3_resource.meta.client.copy`)."""

    def __init__(self):
        self.copies = []
        self.meta = MagicMock()
        self.meta.client.copy.side_effect = self._copy

    def _copy(self, CopySource=None, Bucket=None, Key=None, ExtraArgs=None, **kwargs):
        self.copies.append({"CopySource": CopySource, "Bucket": Bucket, "Key": Key,
                            "ExtraArgs": ExtraArgs or {}})


@pytest.mark.unit
class TestSetPrimaryTypeProvenance:
    """set_primary_file rewrites the current version's metadata onto a new version."""

    def _set_primary(self, monkeypatch, primary_type):
        af = assetFiles
        current_head = {
            "VersionId": "s3v-current",
            "ContentType": "model/gltf-binary",
            "Metadata": _workflow_written_metadata(**{VAMS_PRIMARY_TYPE_METADATA_KEY: "lod2"}),
        }

        class _S3Client:
            def head_object(self, Bucket, Key, **kwargs):
                return current_head

        s3_resource = _ManagedCopyS3()
        monkeypatch.setattr(af, "get_asset_with_permissions",
                            lambda databaseId, assetId, op, claims: {"assetId": assetId})
        monkeypatch.setattr(af, "get_asset_s3_location", lambda asset: (BUCKET, BASE_KEY))
        monkeypatch.setattr(af, "is_file_archived", lambda bucket, key, version_id=None: False)
        monkeypatch.setattr(af, "send_subscription_email", lambda db, a: None)
        monkeypatch.setattr(af, "s3_client", _S3Client())
        monkeypatch.setattr(af, "s3_resource", s3_resource)

        result = af.set_primary_file("db1", "a1", REL_PATH, primary_type, None, CLAIMS)
        return result, s3_resource

    def test_set_stamps_file_metadata_update_from_the_current_version(self, monkeypatch):
        result, s3_resource = self._set_primary(monkeypatch, "primary")

        assert result.success is True
        extra = s3_resource.copies[0]["ExtraArgs"]
        metadata = extra["Metadata"]
        assert metadata[VAMS_PRIMARY_TYPE_METADATA_KEY] == "primary"
        _assert_fresh_stamp(metadata, "fileMetadataUpdate", "alice", "s3v-current")
        assert metadata[ASSET_ID_METADATA_KEY] == "a1"
        assert extra["ContentType"] == "model/gltf-binary"

    def test_clear_stamps_file_metadata_update_too(self, monkeypatch):
        result, s3_resource = self._set_primary(monkeypatch, "")

        assert result.primaryType is None
        metadata = s3_resource.copies[0]["ExtraArgs"]["Metadata"]
        assert VAMS_PRIMARY_TYPE_METADATA_KEY not in metadata
        _assert_fresh_stamp(metadata, "fileMetadataUpdate", "alice", "s3v-current")


@pytest.mark.unit
class TestUnarchiveProvenance:
    """unarchive_file copies the newest content version of the base file, and of each archived
    preview of it, forward as the new current version."""

    def _unarchive(self, monkeypatch, heads, previews):
        from datetime import datetime
        af = assetFiles
        listing = {
            "Versions": [
                {"Key": FULL_KEY, "VersionId": "v2", "IsLatest": False,
                 "LastModified": datetime(2026, 6, 10)},
                {"Key": FULL_KEY, "VersionId": "v1", "IsLatest": False,
                 "LastModified": datetime(2026, 6, 8)},
                {"Key": PREVIEW_KEY, "VersionId": "pv1", "IsLatest": False,
                 "LastModified": datetime(2026, 6, 10)},
            ],
            "DeleteMarkers": [
                {"Key": FULL_KEY, "VersionId": "marker-base", "IsLatest": True,
                 "LastModified": datetime(2026, 6, 11)},
                {"Key": PREVIEW_KEY, "VersionId": "marker-preview", "IsLatest": True,
                 "LastModified": datetime(2026, 6, 11)},
            ],
        }
        copies = {}

        class _CopyObject:
            def __init__(self, key):
                self.key = key

            def copy(self, CopySource=None, ExtraArgs=None):
                copies[self.key] = {"CopySource": CopySource, "ExtraArgs": ExtraArgs or {}}

        class _S3Resource:
            def Object(self, bucket, key):
                return _CopyObject(key)

        class _S3Client:
            def list_object_versions(self, Bucket, Prefix, MaxKeys=None, **kwargs):
                return {name: [e for e in entries if e["Key"].startswith(Prefix)]
                        for name, entries in listing.items()}

            def head_object(self, Bucket, Key, VersionId=None, **kwargs):
                if VersionId is not None:
                    return heads[(Key, VersionId)]
                return {"VersionId": "new-current-ver"}

        monkeypatch.setattr(af, "get_asset_with_permissions",
                            lambda databaseId, assetId, op, claims: {"assetId": assetId})
        monkeypatch.setattr(af, "get_asset_s3_location", lambda asset: (BUCKET, BASE_KEY))
        monkeypatch.setattr(af, "send_subscription_email", lambda db, a: None)
        monkeypatch.setattr(af, "find_preview_files_for_base_including_archived",
                            lambda bucket, base_key: previews)
        monkeypatch.setattr(af, "s3_client", _S3Client())
        monkeypatch.setattr(af, "s3_resource", _S3Resource())

        result = af.unarchive_file("db1", "a1", REL_PATH, CLAIMS)
        return result, copies

    def test_preview_unarchive_replaces_the_preview_provenance(self, monkeypatch):
        heads = {
            (FULL_KEY, "v2"): {"Metadata": _workflow_written_metadata()},
            (PREVIEW_KEY, "pv1"): {
                "Metadata": _workflow_written_metadata(
                    **{VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY: "preview-3d-thumbnail"}),
                "ContentType": "image/png",
            },
        }
        result, copies = self._unarchive(
            monkeypatch, heads, previews=[{"key": PREVIEW_KEY, "isArchived": True}])

        assert "/model.glb.previewFile.png" in result.affectedFiles
        preview_extra = copies[PREVIEW_KEY]["ExtraArgs"]
        assert copies[PREVIEW_KEY]["CopySource"]["VersionId"] == "pv1"
        assert preview_extra.get("MetadataDirective") == "REPLACE"
        _assert_fresh_stamp(preview_extra.get("Metadata", {}), "fileUnarchive", "alice", "")
        # The preview keeps its own context metadata and content type.
        assert preview_extra["Metadata"][ASSET_ID_METADATA_KEY] == "a1"
        assert preview_extra.get("ContentType") == "image/png"
        assert preview_extra.get("ACL") == "bucket-owner-full-control"
