# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_FILE_COPY,
    VAMS_CHANGE_SOURCE_FILE_MOVE,
    VAMS_CHANGE_SOURCE_FILE_RENAME,
    CHANGE_PROVENANCE_METADATA_KEYS,
)

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401


def _load_asset_files():
    """Return the real assetFiles module under test."""
    from backend.backend.handlers.assets import assetFiles as af
    return af


@pytest.mark.unit
class TestBuildChangeMetadata:
    def test_copy_sets_all_from_fields(self):
        from backend.backend.handlers.assets import assetFiles
        md = assetFiles.build_change_metadata(VAMS_CHANGE_SOURCE_FILE_COPY, "alice",
                                      from_db="db1", from_asset="a1", from_path="old/x.glb",
                                      from_version="srcver-1")
        assert md[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_FILE_COPY
        assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "alice"
        assert md[VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY] == "a1"
        assert md[VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY] == "db1"
        assert md[VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY] == "/old/x.glb"
        assert md[VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY] == "srcver-1"

    def test_every_provenance_key_present_irrelevant_cleared(self):
        from backend.backend.handlers.assets import assetFiles
        md = assetFiles.build_change_metadata(VAMS_CHANGE_SOURCE_FILE_MOVE, "bob",
                                      from_db="db1", from_asset="a1", from_path="d/x.glb")
        for key in CHANGE_PROVENANCE_METADATA_KEYS:
            assert key in md
        assert md[VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY] == ""

    def test_classify_move_vs_rename(self):
        from backend.backend.handlers.assets import assetFiles
        assert assetFiles.classify_move_change_source("a/b/x.glb", "a/b/y.glb") == VAMS_CHANGE_SOURCE_FILE_RENAME
        assert assetFiles.classify_move_change_source("a/b/x.glb", "a/c/x.glb") == VAMS_CHANGE_SOURCE_FILE_MOVE
        assert assetFiles.classify_move_change_source("x.glb", "y.glb") == VAMS_CHANGE_SOURCE_FILE_RENAME


@pytest.mark.unit
def test_build_archive_history_record():
    af = _load_asset_files()  # existing helper in this test file
    from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_FILE_ARCHIVE
    rec = af.build_archive_history_record("db1", "a1", "m/x.glb", "v9", "carol")
    assert rec["databaseId:assetId:filePath"] == "db1:a1:/m/x.glb"
    assert rec["versionId"] == "v9"
    assert rec["databaseId:assetId"] == "db1:a1"
    assert rec["databaseId"] == "db1"
    assert rec["assetId"] == "a1"
    assert rec["filePath"] == "/m/x.glb"
    assert rec["changeSource"] == VAMS_CHANGE_SOURCE_FILE_ARCHIVE
    assert rec["changeUserId"] == "carol"


@pytest.mark.unit
def test_build_archive_history_record_defaults_version_null_and_user_system():
    af = _load_asset_files()
    rec = af.build_archive_history_record("db1", "a1", "x.glb", None, None)
    assert rec["versionId"] == "null"
    assert rec["changeUserId"] == "SYSTEM_USER"


@pytest.mark.unit
class TestHistoryEnrichment:
    def test_query_history_map_empty_when_table_none(self, monkeypatch):
        af = _load_asset_files()
        monkeypatch.setattr(af, "asset_file_version_history_table", None)
        assert af.query_asset_version_history_map("db1", "a1") == {}

    def test_query_history_map_keyed_by_path_and_version(self, monkeypatch):
        af = _load_asset_files()

        class _T:
            def query(self, **kwargs):
                return {"Items": [
                    {"filePath": "m/x.glb", "versionId": "v1",
                     "changeSource": "upload", "changeUserId": "alice"},
                ]}
        monkeypatch.setattr(af, "asset_file_version_history_table", _T())
        result = af.query_asset_version_history_map("db1", "a1")
        assert result[("m/x.glb", "v1")]["changeSource"] == "upload"
        assert result[("m/x.glb", "v1")]["changeUserId"] == "alice"

    def test_apply_change_fields_missing_record_leaves_none(self):
        af = _load_asset_files()
        v = {"versionId": "v1"}
        af.apply_change_fields_to_version(v, None)
        assert v.get("changeSource") is None

    def test_apply_change_fields_sets_all_present_columns(self):
        af = _load_asset_files()
        v = {"versionId": "v1"}
        hist = {"changeSource": "fileCopy", "changeUserId": "bob",
                "changeAssetIdFrom": "a2", "changeDatabaseIdFrom": "db1",
                "changeAssetFilePathFrom": "old/x.glb",
                "changeAssetFileVersionFrom": "srcver-1"}
        af.apply_change_fields_to_version(v, hist)
        assert v["changeSource"] == "fileCopy"
        assert v["changeUserId"] == "bob"
        assert v["changeAssetIdFrom"] == "a2"
        assert v["changeAssetFileVersionFrom"] == "srcver-1"


@pytest.mark.unit
class TestArchiveProvenanceAndVersionHistory:
    """Covers two archive-related fixes:
    1. archive_s3_object returns the new delete-marker VersionId so the fileArchive
       history record keys to the version shown in file history.
    2. get_s3_object_metadata includes delete markers in version history for a
       currently-live (unarchived) file, not only when it is currently archived.
    """

    def test_archive_s3_object_returns_delete_marker_version_id(self, monkeypatch):
        af = _load_asset_files()

        class _S3:
            def delete_object(self, Bucket, Key):
                return {"VersionId": "marker-123", "DeleteMarker": True}

        monkeypatch.setattr(af, "s3_client", _S3())
        assert af.archive_s3_object("bucket", "db/asset/x.glb") == "marker-123"

    def test_archive_s3_object_null_when_versioning_off(self, monkeypatch):
        af = _load_asset_files()

        class _S3:
            def delete_object(self, Bucket, Key):
                return {}  # no VersionId on a non-versioned bucket

        monkeypatch.setattr(af, "s3_client", _S3())
        assert af.archive_s3_object("bucket", "db/asset/x.glb") == "null"

    def test_archive_s3_object_none_on_failure(self, monkeypatch):
        af = _load_asset_files()

        class _S3:
            def delete_object(self, Bucket, Key):
                raise Exception("boom")

        monkeypatch.setattr(af, "s3_client", _S3())
        assert af.archive_s3_object("bucket", "db/asset/x.glb") is None

    def test_live_file_version_history_includes_delete_markers(self, monkeypatch):
        af = _load_asset_files()
        from datetime import datetime

        key = "db/asset/x.glb"

        class _S3:
            def head_object(self, Bucket, Key):
                # Live file: head succeeds.
                return {
                    "ContentLength": 10,
                    "ContentType": "model/gltf-binary",
                    "LastModified": datetime(2026, 6, 10, 0, 0, 0),
                    "ETag": '"etag-live"',
                    "StorageClass": "STANDARD",
                    "Metadata": {},
                }

            def list_object_versions(self, Bucket, Prefix, MaxKeys=100):
                return {
                    "Versions": [
                        {"Key": key, "VersionId": "v2", "IsLatest": True, "Size": 10,
                         "LastModified": datetime(2026, 6, 10, 0, 0, 0),
                         "StorageClass": "STANDARD", "ETag": '"e2"'},
                        {"Key": key, "VersionId": "v1", "IsLatest": False, "Size": 8,
                         "LastModified": datetime(2026, 6, 8, 0, 0, 0),
                         "StorageClass": "STANDARD", "ETag": '"e1"'},
                    ],
                    "DeleteMarkers": [
                        # An intermediate archive point between v1 and v2.
                        {"Key": key, "VersionId": "marker-1", "IsLatest": False,
                         "LastModified": datetime(2026, 6, 9, 0, 0, 0)},
                    ],
                }

        monkeypatch.setattr(af, "s3_client", _S3())
        result = af.get_s3_object_metadata("bucket", key, include_versions=True)
        version_ids = {v["versionId"] for v in result["versions"]}
        # The delete marker must be present even though the file is currently live.
        assert "marker-1" in version_ids
        assert {"v1", "v2"} <= version_ids
        marker = next(v for v in result["versions"] if v["versionId"] == "marker-1")
        assert marker["isArchived"] is True


@pytest.mark.unit
class TestUnarchiveFile:
    """Covers the unarchive_file flow.

    unarchive restores an archived file by copying the most recent content
    version (the one before the latest delete marker) forward as a new current
    version. The version listing is fetched once and bounded, since S3 returns
    versions newest-first so only the head of the listing is needed.
    """

    BUCKET = "asset-bucket"
    BASE_KEY = "db1/a1/"
    REL_PATH = "/model.glb"
    FULL_KEY = "db1/a1/model.glb"

    def _patch_common(self, af, monkeypatch, s3_client, s3_resource):
        """Patch shared dependencies so only the S3 interaction matters."""
        monkeypatch.setattr(af, "get_asset_with_permissions",
                            lambda databaseId, assetId, op, claims: {"assetId": assetId})
        monkeypatch.setattr(af, "get_asset_s3_location",
                            lambda asset: (self.BUCKET, self.BASE_KEY))
        monkeypatch.setattr(af, "send_subscription_email", lambda db, a: None)
        # No associated preview files for the base file in these tests.
        monkeypatch.setattr(af, "find_preview_files_for_base_including_archived",
                            lambda bucket, base_key: [])
        monkeypatch.setattr(af, "s3_client", s3_client)
        monkeypatch.setattr(af, "s3_resource", s3_resource)

    def _make_s3_with_versions(self, versions, delete_markers):
        """Build a fake S3 client and resource that record the copy CopySource."""
        from datetime import datetime  # noqa: F401  (used by callers building versions)
        full_key = self.FULL_KEY
        recorded = {}

        class _CopyObject:
            def __init__(self, bucket, key):
                self.bucket = bucket
                self.key = key

            def copy(self, CopySource=None, ExtraArgs=None):
                recorded["CopySource"] = CopySource
                recorded["ExtraArgs"] = ExtraArgs

        class _S3Resource:
            def Object(self, bucket, key):
                return _CopyObject(bucket, key)

        class _S3Client:
            def list_object_versions(self, Bucket, Prefix, MaxKeys=None, **kwargs):
                return {"Versions": list(versions), "DeleteMarkers": list(delete_markers)}

            def head_object(self, Bucket, Key, VersionId=None):
                # First call (with VersionId): return source metadata to copy forward.
                if VersionId is not None:
                    return {"Metadata": {"existing": "value"}}
                # Second call (no VersionId): return the new current version id.
                return {"VersionId": "new-current-ver"}

        return _S3Client(), _S3Resource(), recorded

    def test_unarchive_copies_latest_version_before_delete_marker(self, monkeypatch):
        from datetime import datetime
        from common.s3MetadataKeys import (
            VAMS_CHANGE_SOURCE_METADATA_KEY,
            VAMS_CHANGE_SOURCE_FILE_UNARCHIVE,
        )
        af = _load_asset_files()

        # Two content versions plus a latest delete marker (file is archived).
        versions = [
            {"Key": self.FULL_KEY, "VersionId": "v2", "IsLatest": False,
             "LastModified": datetime(2026, 6, 10)},
            {"Key": self.FULL_KEY, "VersionId": "v1", "IsLatest": False,
             "LastModified": datetime(2026, 6, 8)},
        ]
        delete_markers = [
            {"Key": self.FULL_KEY, "VersionId": "marker-latest", "IsLatest": True,
             "LastModified": datetime(2026, 6, 11)},
        ]
        s3_client, s3_resource, recorded = self._make_s3_with_versions(versions, delete_markers)
        self._patch_common(af, monkeypatch, s3_client, s3_resource)

        result = af.unarchive_file("db1", "a1", self.REL_PATH, {"tokens": ["alice"]})

        assert result.success is True
        # Restores the most recent content version (v2), not the older v1.
        assert recorded["CopySource"]["VersionId"] == "v2"
        # Overlays unarchive provenance while preserving existing metadata.
        metadata = recorded["ExtraArgs"]["Metadata"]
        assert metadata[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_FILE_UNARCHIVE
        assert metadata["existing"] == "value"
        assert self.REL_PATH in result.affectedFiles

    def test_unarchive_rejects_file_not_archived(self, monkeypatch):
        from datetime import datetime
        af = _load_asset_files()

        # Live file: a content version exists, no latest delete marker.
        versions = [
            {"Key": self.FULL_KEY, "VersionId": "v1", "IsLatest": True,
             "LastModified": datetime(2026, 6, 8)},
        ]
        s3_client, s3_resource, recorded = self._make_s3_with_versions(versions, [])
        self._patch_common(af, monkeypatch, s3_client, s3_resource)

        with pytest.raises(af.VAMSGeneralErrorResponse, match="not archived"):
            af.unarchive_file("db1", "a1", self.REL_PATH, {"tokens": ["alice"]})
        assert "CopySource" not in recorded

    def test_unarchive_rejects_missing_file(self, monkeypatch):
        af = _load_asset_files()

        s3_client, s3_resource, recorded = self._make_s3_with_versions([], [])
        self._patch_common(af, monkeypatch, s3_client, s3_resource)

        with pytest.raises(af.VAMSGeneralErrorResponse, match="not found"):
            af.unarchive_file("db1", "a1", self.REL_PATH, {"tokens": ["alice"]})
        assert "CopySource" not in recorded

    def test_unarchive_rejects_when_no_prior_content_version(self, monkeypatch):
        from datetime import datetime
        af = _load_asset_files()

        # Only a delete marker exists, no content version to restore.
        delete_markers = [
            {"Key": self.FULL_KEY, "VersionId": "marker-latest", "IsLatest": True,
             "LastModified": datetime(2026, 6, 11)},
        ]
        s3_client, s3_resource, recorded = self._make_s3_with_versions([], delete_markers)
        self._patch_common(af, monkeypatch, s3_client, s3_resource)

        with pytest.raises(af.VAMSGeneralErrorResponse, match="previous version"):
            af.unarchive_file("db1", "a1", self.REL_PATH, {"tokens": ["alice"]})
        assert "CopySource" not in recorded

    def test_unarchive_ignores_sibling_key_versions(self, monkeypatch):
        from datetime import datetime
        af = _load_asset_files()

        # The Prefix listing also returns a sibling key (model.glb.bak) that shares
        # the prefix. Only versions for the exact key must be considered.
        versions = [
            {"Key": self.FULL_KEY + ".bak", "VersionId": "sibling-newest", "IsLatest": True,
             "LastModified": datetime(2026, 6, 20)},
            {"Key": self.FULL_KEY, "VersionId": "v1", "IsLatest": False,
             "LastModified": datetime(2026, 6, 8)},
        ]
        delete_markers = [
            {"Key": self.FULL_KEY, "VersionId": "marker-latest", "IsLatest": True,
             "LastModified": datetime(2026, 6, 11)},
        ]
        s3_client, s3_resource, recorded = self._make_s3_with_versions(versions, delete_markers)
        self._patch_common(af, monkeypatch, s3_client, s3_resource)

        result = af.unarchive_file("db1", "a1", self.REL_PATH, {"tokens": ["alice"]})

        assert result.success is True
        # Must restore the exact key's version, never the sibling's newer version.
        assert recorded["CopySource"]["VersionId"] == "v1"
        assert recorded["CopySource"]["Key"] == self.FULL_KEY
