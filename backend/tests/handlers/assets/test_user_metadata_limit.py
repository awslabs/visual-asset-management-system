# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A copy that replaces an asset file's user metadata stays within the Amazon S3 2 KB limit.

Amazon S3 counts the UTF-8 bytes of every user-metadata key and value against a 2 KB limit and
refuses a copy over it. ``replace_metadata_copy_args`` leaves out the blank change-provenance
entries a new version is stamped with, since readers treat a missing provenance key as blank, and
raises ``UserMetadataTooLargeError`` for metadata that is still over the limit. The file operations
a caller drives directly (set primary type, revert, unarchive) answer that with a specific message
rather than a generic failure, and write no new version.
"""

from datetime import datetime

import pytest

# The harness module sets the environment assetFiles reads at import time, so it is imported first
from tests.handlers.assets.test_replace_copy_content_headers import (
    FULL_KEY,
    REL_PATH,
    _FakeS3,
    _patch_asset_files,
    _source_head,
)
from backend.backend.handlers.assets.assetFiles import USER_METADATA_TOO_LARGE_MESSAGE
from common.s3MetadataKeys import (
    CHANGE_PROVENANCE_METADATA_KEYS,
    DATABASE_ID_METADATA_KEY,
    S3_USER_METADATA_LIMIT_BYTES,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    UserMetadataTooLargeError,
    replace_metadata_copy_args,
    user_metadata_size,
)


def _metadata_of_size(size, key="custom"):
    """User metadata whose single entry totals ``size`` bytes."""
    return {key: "x" * (size - len(key))}


@pytest.mark.unit
class TestReplaceMetadataCopyArgsLimit:
    def test_blank_provenance_entries_are_left_out(self):
        metadata = {
            DATABASE_ID_METADATA_KEY: "db1",
            "custom": "",
            VAMS_CHANGE_SOURCE_METADATA_KEY: "fileMetadataUpdate",
            VAMS_CHANGE_USER_ID_METADATA_KEY: "alice",
            VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY: "",
            VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: None,
        }

        written = replace_metadata_copy_args({"ContentLength": 10}, metadata)["Metadata"]

        assert written == {
            DATABASE_ID_METADATA_KEY: "db1",
            "custom": "",
            VAMS_CHANGE_SOURCE_METADATA_KEY: "fileMetadataUpdate",
            VAMS_CHANGE_USER_ID_METADATA_KEY: "alice",
        }
        # The caller's dict is not modified
        assert VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY in metadata

    def test_metadata_at_the_limit_is_accepted(self):
        metadata = _metadata_of_size(S3_USER_METADATA_LIMIT_BYTES)

        assert user_metadata_size(metadata) == S3_USER_METADATA_LIMIT_BYTES
        assert replace_metadata_copy_args({}, metadata)["Metadata"] == metadata

    def test_metadata_over_the_limit_raises(self):
        with pytest.raises(UserMetadataTooLargeError):
            replace_metadata_copy_args({}, _metadata_of_size(S3_USER_METADATA_LIMIT_BYTES + 1))

    def test_size_counts_utf8_bytes_not_characters(self):
        # "é" is two bytes in UTF-8
        assert user_metadata_size({"k": "é"}) == 3
        metadata = {"k": "é" * (S3_USER_METADATA_LIMIT_BYTES // 2)}
        with pytest.raises(UserMetadataTooLargeError):
            replace_metadata_copy_args({}, metadata)

    def test_dropped_blank_provenance_does_not_count_against_the_limit(self):
        blank_provenance = {key: "" for key in CHANGE_PROVENANCE_METADATA_KEYS}
        metadata = {**_metadata_of_size(S3_USER_METADATA_LIMIT_BYTES), **blank_provenance}

        assert user_metadata_size(metadata) > S3_USER_METADATA_LIMIT_BYTES
        assert replace_metadata_copy_args({}, metadata)["Metadata"] == _metadata_of_size(
            S3_USER_METADATA_LIMIT_BYTES)


def _oversized_head():
    head = _source_head()
    head["Metadata"] = {**head["Metadata"], **_metadata_of_size(S3_USER_METADATA_LIMIT_BYTES, "notes")}
    return head


@pytest.mark.unit
class TestFileOperationsReportTheLimit:
    def test_set_primary_type(self, monkeypatch):
        fake = _FakeS3(_oversized_head())
        af = _patch_asset_files(monkeypatch, fake)
        monkeypatch.setattr(af, "is_file_archived", lambda bucket, key, version_id=None: False)

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.set_primary_file("db1", "a1", REL_PATH, "primary", None, {"tokens": ["alice"]})

        assert str(raised.value).endswith(USER_METADATA_TOO_LARGE_MESSAGE)
        assert fake.copies == []

    def test_revert(self, monkeypatch):
        fake = _FakeS3(_oversized_head())
        af = _patch_asset_files(monkeypatch, fake)
        monkeypatch.setattr(af, "get_s3_object_metadata", lambda bucket, key, include_versions=False: {
            "versions": [
                {"versionId": "v2", "isLatest": True, "isArchived": False},
                {"versionId": "v1", "isLatest": False, "isArchived": False},
            ],
        })
        monkeypatch.setattr(af, "delete_assetAuxiliary_files", lambda prefix: None)

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.revert_file_version("db1", "a1", REL_PATH, "v1", {"tokens": ["alice"]})

        assert str(raised.value).endswith(USER_METADATA_TOO_LARGE_MESSAGE)
        assert fake.copies == []

    def test_unarchive(self, monkeypatch):
        versions = [{"Key": FULL_KEY, "VersionId": "v1", "IsLatest": False,
                     "LastModified": datetime(2026, 6, 8)}]
        delete_markers = [{"Key": FULL_KEY, "VersionId": "marker-latest", "IsLatest": True,
                           "LastModified": datetime(2026, 6, 11)}]
        fake = _FakeS3(_oversized_head(), versions, delete_markers)
        af = _patch_asset_files(monkeypatch, fake)
        monkeypatch.setattr(af, "find_preview_files_for_base_including_archived",
                            lambda bucket, base_key: [])

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.unarchive_file("db1", "a1", REL_PATH, {"tokens": ["alice"]})

        assert str(raised.value).endswith(USER_METADATA_TOO_LARGE_MESSAGE)
        assert fake.copies == []

    def test_metadata_within_the_limit_still_writes_the_version(self, monkeypatch):
        """Paired control: the same source with ordinary metadata sets the primary type."""
        fake = _FakeS3(_source_head())
        af = _patch_asset_files(monkeypatch, fake)
        monkeypatch.setattr(af, "is_file_archived", lambda bucket, key, version_id=None: False)

        result = af.set_primary_file("db1", "a1", REL_PATH, "primary", None, {"tokens": ["alice"]})

        assert result.success is True
        assert len(fake.copies) == 1
        written = fake.copies[0]["ExtraArgs"]["Metadata"]
        assert VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY not in written
        assert written[VAMS_CHANGE_USER_ID_METADATA_KEY] == "alice"
