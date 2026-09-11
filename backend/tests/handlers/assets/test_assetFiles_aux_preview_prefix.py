# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auxiliary-bucket prefixes used by the asset file lifecycle.

Auxiliary preview/viewer data is written under the database-scoped per-file layout
``{databaseId}/{assetFileKey}/preview/...`` (see
``common.workflows.executionRecords.aux_preview_file_prefix``). The delete / move /
copy / revert paths must target that same layout.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402


def _load_asset_files():
    from backend.backend.handlers.assets import assetFiles as af
    return af


@pytest.mark.unit
class TestAuxBucketAssetFileBase:
    def test_matches_writer_preview_layout(self):
        """The base prefix contains the writer's preview prefix for the same file."""
        af = _load_asset_files()
        from backend.backend.common.workflows import executionRecords as er

        writer_prefix = er.aux_preview_file_prefix("db1", "asset-1/scans/pump.e57")
        base = af.aux_bucket_asset_file_base("db1", "asset-1/scans/pump.e57")
        assert base == "db1/asset-1/scans/pump.e57/"
        assert writer_prefix.startswith(base)

    def test_preserves_custom_asset_base_prefix(self):
        af = _load_asset_files()
        assert af.aux_bucket_asset_file_base("db1", "custom/base/asset-1/x.laz") == \
            "db1/custom/base/asset-1/x.laz/"

    def test_normalizes_slashes(self):
        af = _load_asset_files()
        assert af.aux_bucket_asset_file_base("db1", "/asset-1/folder/") == "db1/asset-1/folder/"
        assert af.aux_bucket_asset_file_base("db1", "") == "db1/"


@pytest.mark.unit
class TestLifecycleUsesDatabaseScopedPrefix:
    def test_delete_auxiliary_preview_endpoint_lists_scoped_prefix(self):
        """The delete-auxiliary-preview endpoint lists the database-scoped prefix."""
        af = _load_asset_files()
        aux_key = "db1/asset-1/scans/pump.e57/preview/r/octree.bin"

        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": [{"Key": aux_key}]}]
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value = paginator

        with patch.object(af, 's3_client', mock_s3), \
                patch.object(af, 'get_asset_with_permissions', return_value={
                    'assetId': 'asset-1', 'databaseId': 'db1', 'bucketId': 'bucket-1',
                    'assetLocation': {'Key': 'asset-1/'},
                }), \
                patch.object(af, 'get_asset_s3_location', return_value=("asset-bucket", "asset-1/")), \
                patch.object(af, 'delete_assetAuxiliary_files') as mock_delete, \
                patch.object(af, 'send_subscription_email', MagicMock()):
            response = af.delete_auxiliary_preview_asset_files("db1", "asset-1", "/scans/pump.e57", {})

        assert response.success is True
        assert paginator.paginate.call_args.kwargs['Prefix'] == "db1/asset-1/scans/pump.e57/"
        mock_delete.assert_called_once_with("db1/asset-1/scans/pump.e57/")

    def test_revert_deletes_scoped_prefix(self):
        """Reverting a file version clears the aux data at the database-scoped prefix."""
        af = _load_asset_files()

        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {'Metadata': {}, 'VersionId': 'v-new'}

        with patch.object(af, 'get_asset_with_permissions', return_value={
                    'assetId': 'asset-1', 'databaseId': 'db1', 'bucketId': 'bucket-1',
                    'assetLocation': {'Key': 'asset-1/'},
                }), \
                patch.object(af, 'get_asset_s3_location', return_value=("asset-bucket", "asset-1/")), \
                patch.object(af, 'get_s3_object_metadata', return_value={'versions': [
                    {'versionId': 'v2', 'isLatest': True, 'isArchived': False},
                    {'versionId': 'v1', 'isLatest': False, 'isArchived': False},
                ]}), \
                patch.object(af, 's3_client', mock_s3), \
                patch.object(af, 's3_resource', MagicMock()), \
                patch.object(af, 'delete_assetAuxiliary_files') as mock_delete, \
                patch.object(af, 'send_subscription_email', MagicMock()):
            af.revert_file_version("db1", "asset-1", "/scans/pump.e57", "v1", {"tokens": ["alice"]})

        mock_delete.assert_called_once_with("db1/asset-1/scans/pump.e57/")

    def test_move_relocates_scoped_prefix(self):
        """Moving a file moves its aux data between database-scoped prefixes."""
        af = _load_asset_files()

        from botocore.exceptions import ClientError
        not_found = ClientError({'Error': {'Code': '404'}}, 'HeadObject')

        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = [{'Metadata': {}}, not_found]
        mock_s3.list_object_versions.return_value = {}

        with patch.object(af, 'get_asset_with_permissions', return_value={
                    'assetId': 'asset-1', 'databaseId': 'db1', 'bucketId': 'bucket-1',
                    'assetLocation': {'Key': 'asset-1/'},
                }), \
                patch.object(af, 'get_asset_s3_location', return_value=("asset-bucket", "asset-1/")), \
                patch.object(af, 's3_client', mock_s3), \
                patch.object(af, 'is_file_archived', return_value=False), \
                patch.object(af, 'move_s3_object', return_value=True), \
                patch.object(af, 'process_preview_files', return_value=[]), \
                patch.object(af, 'move_auxiliary_files') as mock_move, \
                patch.object(af, '_copy_file_metadata_to_destination', return_value=0), \
                patch.object(af, 'send_subscription_email', MagicMock()):
            af.move_file("db1", "asset-1", "/scans/pump.e57", "/final/pump.e57", {"tokens": ["alice"]})

        mock_move.assert_called_once_with(
            "db1/asset-1/scans/pump.e57/",
            "db1/asset-1/final/pump.e57/",
        )
