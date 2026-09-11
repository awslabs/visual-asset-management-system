# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""HeadObject-miss routing for the archive-file and set-primary-type paths.

S3 HeadObject reports a missing key as ``404`` (``NotFound``), never as
``NoSuchKey``, so the archived-file check that follows each head_object call
has to accept that spelling. Otherwise an already-archived file is reported as a
generic "Error checking file" instead of "File is already archived".
"""

import os
import pytest
from botocore.exceptions import ClientError

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401


BUCKET = "asset-bucket"
BASE_KEY = "db1/a1/"
REL_PATH = "/model.glb"
FULL_KEY = "db1/a1/model.glb"
CLAIMS = {"tokens": ["alice"]}

MISSING_CODES = ["404", "NotFound", "NoSuchKey"]


def _load_asset_files():
    """Return the real assetFiles module under test."""
    from backend.backend.handlers.assets import assetFiles as af
    return af


def _client_error(code, operation="HeadObject"):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class _HeadRaises:
    """S3 client double whose head_object raises the configured ClientError."""

    def __init__(self, error):
        self.error = error
        self.calls = []

    def head_object(self, Bucket, Key, **kwargs):
        self.calls.append((Bucket, Key))
        raise self.error


def _patch_common(af, monkeypatch, s3_client, archived):
    """Patch shared dependencies so only the head_object outcome matters.

    Returns the list of (bucket, key) pairs is_file_archived was asked about, so a
    test can assert whether the archived-check branch ran at all.
    """
    monkeypatch.setattr(af, "get_asset_with_permissions",
                        lambda databaseId, assetId, op, claims: {"assetId": assetId})
    monkeypatch.setattr(af, "get_asset_s3_location",
                        lambda asset: (BUCKET, BASE_KEY))
    monkeypatch.setattr(af, "s3_client", s3_client)
    archived_calls = []

    def _is_file_archived(bucket, key, version_id=None):
        archived_calls.append((bucket, key))
        return archived

    monkeypatch.setattr(af, "is_file_archived", _is_file_archived)
    return archived_calls


@pytest.mark.unit
class TestIsMissingObjectError:
    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_object_codes(self, code):
        af = _load_asset_files()
        assert af._is_missing_object_error(_client_error(code)) is True

    @pytest.mark.parametrize("code", ["AccessDenied", "MethodNotAllowed", "403", "NoSuchBucket"])
    def test_other_codes(self, code):
        af = _load_asset_files()
        assert af._is_missing_object_error(_client_error(code)) is False

    def test_missing_error_block(self):
        af = _load_asset_files()
        assert af._is_missing_object_error(ClientError({}, "HeadObject")) is False


@pytest.mark.unit
class TestArchiveFileHeadMiss:
    """archive_file on a single file whose head_object misses."""

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_head_reports_already_archived(self, monkeypatch, code):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error(code))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=True)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.archive_file("db1", "a1", REL_PATH, False, CLAIMS)

        assert "File is already archived." in str(exc.value)
        assert archived_calls == [(BUCKET, FULL_KEY)]

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_head_not_archived_reports_not_found(self, monkeypatch, code):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error(code))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=False)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.archive_file("db1", "a1", REL_PATH, False, CLAIMS)

        assert "File not found." in str(exc.value)
        assert archived_calls == [(BUCKET, FULL_KEY)]

    def test_unrelated_error_skips_archived_check(self, monkeypatch):
        """Control: a non-missing code must still take the generic path, even when
        the file would read as archived."""
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error("AccessDenied"))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=True)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.archive_file("db1", "a1", REL_PATH, False, CLAIMS)

        assert "Error checking file." in str(exc.value)
        assert archived_calls == []


@pytest.mark.unit
class TestSetPrimaryFileHeadMiss:
    """set_primary_file on a file whose head_object misses."""

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_head_reports_archived(self, monkeypatch, code):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error(code))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=True)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.set_primary_file("db1", "a1", REL_PATH, "primary", None, CLAIMS)

        assert "Cannot set primary type on archived file" in str(exc.value)
        assert archived_calls == [(BUCKET, FULL_KEY)]

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_head_not_archived_reports_not_found(self, monkeypatch, code):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error(code))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=False)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.set_primary_file("db1", "a1", REL_PATH, "primary", None, CLAIMS)

        assert "File not found" in str(exc.value)
        assert archived_calls == [(BUCKET, FULL_KEY)]

    def test_unrelated_error_skips_archived_check(self, monkeypatch):
        """Control: a non-missing code must still take the generic path, even when
        the file would read as archived."""
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error("AccessDenied"))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=True)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.set_primary_file("db1", "a1", REL_PATH, "primary", None, CLAIMS)

        assert "Error checking file" in str(exc.value)
        assert archived_calls == []


class _HeadOk:
    """S3 client double whose head_object succeeds."""

    def head_object(self, Bucket, Key, **kwargs):
        return {"ContentLength": 1}


@pytest.mark.unit
class TestCopyFileHeadMiss:
    """copy_file's source check: HeadObject never says NoSuchKey, so the 404 spelling must
    reach the 'Source file not found.' branch rather than the generic one."""

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_source_reports_not_found(self, monkeypatch, code):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error(code))
        _patch_common(af, monkeypatch, s3, archived=False)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.copy_file("db1", "a1", REL_PATH, "/copy.glb", None, None, CLAIMS)

        assert "Source file not found." in str(exc.value)
        assert s3.calls == [(BUCKET, FULL_KEY)]

    def test_unrelated_error_is_generic(self, monkeypatch):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error("AccessDenied"))
        _patch_common(af, monkeypatch, s3, archived=False)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.copy_file("db1", "a1", REL_PATH, "/copy.glb", None, None, CLAIMS)

        assert "Error checking source file." in str(exc.value)


@pytest.mark.unit
class TestMoveFileHeadMiss:
    """move_file's source check gates the archived-file message on the same spelling."""

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_head_archived_reports_unarchive_first(self, monkeypatch, code):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error(code))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=True)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.move_file("db1", "a1", REL_PATH, "/moved.glb", CLAIMS)

        assert "Unarchive it first." in str(exc.value)
        assert archived_calls == [(BUCKET, FULL_KEY)]

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_head_not_archived_reports_not_found(self, monkeypatch, code):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error(code))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=False)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.move_file("db1", "a1", REL_PATH, "/moved.glb", CLAIMS)

        assert "Source file not found." in str(exc.value)
        assert archived_calls == [(BUCKET, FULL_KEY)]

    def test_unrelated_error_skips_archived_check(self, monkeypatch):
        af = _load_asset_files()
        s3 = _HeadRaises(_client_error("AccessDenied"))
        archived_calls = _patch_common(af, monkeypatch, s3, archived=True)

        with pytest.raises(af.VAMSGeneralErrorResponse) as exc:
            af.move_file("db1", "a1", REL_PATH, "/moved.glb", CLAIMS)

        assert "Error checking source file." in str(exc.value)
        assert archived_calls == []


@pytest.mark.unit
class TestCheckDestinationFileExists:
    """check_destination_file_exists answers False for every missing-object spelling and
    raises for anything else."""

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_missing_is_false(self, monkeypatch, code):
        af = _load_asset_files()
        monkeypatch.setattr(af, "s3_client", _HeadRaises(_client_error(code)))
        assert af.check_destination_file_exists(BUCKET, FULL_KEY, REL_PATH) is False

    def test_present_is_true(self, monkeypatch):
        # POSITIVE CONTROL for the arm above.
        af = _load_asset_files()
        monkeypatch.setattr(af, "s3_client", _HeadOk())
        assert af.check_destination_file_exists(BUCKET, FULL_KEY, REL_PATH) is True

    def test_unrelated_error_raises(self, monkeypatch):
        af = _load_asset_files()
        monkeypatch.setattr(af, "s3_client", _HeadRaises(_client_error("AccessDenied")))
        with pytest.raises(af.VAMSGeneralErrorResponse):
            af.check_destination_file_exists(BUCKET, FULL_KEY, REL_PATH)


@pytest.mark.unit
class TestGetS3ObjectMetadataHeadMiss:
    """get_s3_object_metadata falls through to the version-history archived check on a
    HeadObject miss under every spelling."""

    @pytest.mark.parametrize("code", MISSING_CODES)
    def test_delete_marker_reports_archived(self, monkeypatch, code):
        from datetime import datetime, timezone
        af = _load_asset_files()
        monkeypatch.setattr(af, "s3_client", _HeadRaises(_client_error(code)))
        monkeypatch.setattr(af, "list_all_object_versions", lambda bucket, key, client=None: {
            "DeleteMarkers": [{"Key": FULL_KEY, "VersionId": "dm1", "IsLatest": True,
                               "LastModified": datetime(2026, 1, 1, tzinfo=timezone.utc)}],
            "Versions": [],
        })
        result = af.get_s3_object_metadata(BUCKET, FULL_KEY)
        assert result["isArchived"] is True
        assert result["key"] == FULL_KEY
