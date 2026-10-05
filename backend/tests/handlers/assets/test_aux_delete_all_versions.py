# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auxiliary-bucket cleanup removes every version under the prefix it is given.

The auxiliary bucket is versioned like every VAMS storage bucket. A delete that names no VersionId
only adds a delete marker and keeps every version behind it, so the cleanup that runs on permanent
file and asset delete, file revert, asset version revert and the explicit auxiliary delete removes the
versions and the delete markers under the file's or asset's auxiliary prefix, and nothing outside that
prefix.
"""

import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles, assetVersions  # noqa: E402

AUX_BUCKET = "vams-test-aux"


@pytest.fixture
def aux_s3():
    """A versioned auxiliary bucket in moto, and the client both modules are pointed at."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=AUX_BUCKET)
        client.put_bucket_versioning(Bucket=AUX_BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield client


def _put(client, key, versions=2, then_delete=False):
    for n in range(versions):
        client.put_object(Bucket=AUX_BUCKET, Key=key, Body=f"version {n}".encode())
    if then_delete:
        client.delete_object(Bucket=AUX_BUCKET, Key=key)


def _entries(client, prefix):
    """(key, is_delete_marker) for every version and delete marker under the prefix."""
    rows = []
    for page in client.get_paginator("list_object_versions").paginate(Bucket=AUX_BUCKET, Prefix=prefix):
        rows += [(v["Key"], False) for v in page.get("Versions", [])]
        rows += [(m["Key"], True) for m in page.get("DeleteMarkers", [])]
    return sorted(rows)


def _fresh_asset_service():
    """A private instance of assetService, loaded by path the way its other tests load it.

    The shared instance from test_assetService_history._load() has its delete_assetAuxiliary_files
    replaced by some of those tests, so this test takes its own.
    """
    from backend.tests.handlers.assets import test_assetService_history as history

    saved = history._cached_module
    history._cached_module = None
    try:
        return history._load()
    finally:
        history._cached_module = saved


@pytest.mark.unit
class TestAssetFilesAuxiliaryDelete:
    """assetFiles.delete_assetAuxiliary_files: permanent file/prefix delete, file revert and
    DELETE /database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles."""

    def test_removes_every_version_and_marker_under_the_file_prefix(self, aux_s3):
        _put(aux_s3, "db1/a1/scan.e57/preview/scan.e57.png")
        _put(aux_s3, "db1/a1/scan.e57/preview/potree/cloud.js", then_delete=True)
        _put(aux_s3, "db1/a1/scan.e57b/preview/x.png", versions=1)
        _put(aux_s3, "db1/a1/other.glb/preview/o.png", versions=1)
        # Positive control: two versions each, and a delete marker on the second key.
        assert len(_entries(aux_s3, "db1/a1/scan.e57/")) == 5

        with patch.object(assetFiles, "s3_client", aux_s3), \
             patch.object(assetFiles, "asset_aux_bucket_name", AUX_BUCKET):
            assetFiles.delete_assetAuxiliary_files("db1/a1/scan.e57")

        assert _entries(aux_s3, "db1/a1/scan.e57/") == []
        # A sibling whose name only extends the file's name, and another file, keep their data.
        assert _entries(aux_s3, "db1/a1/scan.e57b/") == [("db1/a1/scan.e57b/preview/x.png", False)]
        assert _entries(aux_s3, "db1/a1/other.glb/") == [("db1/a1/other.glb/preview/o.png", False)]

    def test_every_listing_page_is_deleted_by_version_and_entry_errors_are_logged(self):
        # Two listing pages; the second request reports one entry that S3 could not delete.
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [
            {"Versions": [{"Key": "db1/a1/f.laz/preview/a.png", "VersionId": "v2"},
                          {"Key": "db1/a1/f.laz/preview/a.png", "VersionId": "v1"}]},
            {"Versions": [{"Key": "db1/a1/f.laz/preview/b.png", "VersionId": "null"}],
             "DeleteMarkers": [{"Key": "db1/a1/f.laz/preview/b.png", "VersionId": "m1"}]},
        ]
        client.delete_objects.side_effect = [
            {},
            {"Errors": [{"Key": "db1/a1/f.laz/preview/b.png", "VersionId": "null", "Code": "AccessDenied"}]},
        ]
        with patch.object(assetFiles, "s3_client", client), \
             patch.object(assetFiles, "asset_aux_bucket_name", AUX_BUCKET), \
             patch.object(assetFiles.logger, "warning") as m_warning, \
             patch.object(assetFiles.logger, "exception") as m_exception:
            assetFiles.delete_assetAuxiliary_files("db1/a1/f.laz")

        client.get_paginator.assert_called_once_with("list_object_versions")
        client.get_paginator.return_value.paginate.assert_called_once_with(
            Bucket=AUX_BUCKET, Prefix="db1/a1/f.laz/")
        sent = [c.kwargs["Delete"]["Objects"] for c in client.delete_objects.call_args_list]
        assert sent == [
            [{"Key": "db1/a1/f.laz/preview/a.png", "VersionId": "v2"},
             {"Key": "db1/a1/f.laz/preview/a.png", "VersionId": "v1"}],
            [{"Key": "db1/a1/f.laz/preview/b.png", "VersionId": "null"},
             {"Key": "db1/a1/f.laz/preview/b.png", "VersionId": "m1"}],
        ]
        client.delete_object.assert_not_called()
        m_warning.assert_called_once()
        m_exception.assert_not_called()

    def test_an_empty_prefix_lists_nothing(self):
        # An empty prefix would otherwise address the whole bucket.
        client = MagicMock()
        with patch.object(assetFiles, "s3_client", client):
            assetFiles.delete_assetAuxiliary_files("")
        assert client.method_calls == []

    def test_a_listing_failure_is_logged_not_raised(self):
        # Callers treat auxiliary cleanup as best effort: it never fails the request it runs in.
        client = MagicMock()
        client.get_paginator.side_effect = RuntimeError("AccessDenied")
        with patch.object(assetFiles, "s3_client", client), \
             patch.object(assetFiles, "asset_aux_bucket_name", AUX_BUCKET), \
             patch.object(assetFiles.logger, "exception") as m_log:
            assetFiles.delete_assetAuxiliary_files("db1/a1/scan.e57/")
        m_log.assert_called_once()


@pytest.mark.unit
class TestAssetServiceAuxiliaryDelete:
    """assetService.delete_assetAuxiliary_files: permanent asset delete."""

    def test_removes_every_version_and_marker_under_the_asset_prefix(self, aux_s3):
        _put(aux_s3, "db1/a1/scan.e57/preview/scan.e57.png")
        _put(aux_s3, "db1/a1/model.glb/preview/model.png", then_delete=True)
        _put(aux_s3, "db1/a10/model.glb/preview/model.png", versions=1)
        _put(aux_s3, "db2/a1/model.glb/preview/model.png", versions=1)
        assert len(_entries(aux_s3, "db1/a1/")) == 5

        m = _fresh_asset_service()
        with patch.object(m, "s3", aux_s3), \
             patch.object(m, "s3_assetAuxiliary_bucket", AUX_BUCKET):
            m.delete_assetAuxiliary_files("db1", {"Key": "a1/"})

        assert _entries(aux_s3, "db1/a1/") == []
        # An asset whose id only extends this one's, and the same asset id in another database.
        assert _entries(aux_s3, "db1/a10/") == [("db1/a10/model.glb/preview/model.png", False)]
        assert _entries(aux_s3, "db2/a1/") == [("db2/a1/model.glb/preview/model.png", False)]


@pytest.mark.unit
class TestAssetVersionsAuxiliaryDelete:
    """assetVersions.delete_assetAuxiliary_files: asset version revert, once per reverted file."""

    def test_removes_every_version_and_marker_under_the_file_prefix(self, aux_s3):
        _put(aux_s3, "db1/a1/scan.e57/preview/scan.e57.png")
        _put(aux_s3, "db1/a1/scan.e57/preview/potree/cloud.js", then_delete=True)
        _put(aux_s3, "db1/a1/scan.e57b/preview/x.png", versions=1)
        assert len(_entries(aux_s3, "db1/a1/scan.e57/")) == 5

        with patch.object(assetVersions, "s3_client", aux_s3),              patch.object(assetVersions, "asset_aux_bucket_name", AUX_BUCKET):
            assetVersions.delete_assetAuxiliary_files(
                assetVersions.aux_bucket_asset_file_base("db1", "a1/scan.e57"))

        assert _entries(aux_s3, "db1/a1/scan.e57/") == []
        assert _entries(aux_s3, "db1/a1/scan.e57b/") == [("db1/a1/scan.e57b/preview/x.png", False)]

    def test_a_listing_failure_is_logged_not_raised(self):
        # A failed cleanup must not fail the revert of the file it runs for.
        client = MagicMock()
        client.get_paginator.side_effect = RuntimeError("AccessDenied")
        with patch.object(assetVersions, "s3_client", client),              patch.object(assetVersions, "asset_aux_bucket_name", AUX_BUCKET),              patch.object(assetVersions.logger, "exception") as m_log:
            assetVersions.delete_assetAuxiliary_files("db1/a1/scan.e57/")
        m_log.assert_called_once()
        client.delete_object.assert_not_called()
