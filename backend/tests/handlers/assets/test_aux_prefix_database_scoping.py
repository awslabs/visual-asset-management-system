# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auxiliary-bucket prefixes used by asset delete and version revert.

Auxiliary preview/viewer data is written under the database-scoped per-file layout
``{databaseId}/{assetFileKey}/preview/...`` (see
``common.workflows.executionRecords.aux_preview_file_prefix``), so the asset-delete
helper in assetService and the bulk version revert in assetVersions must target the
same layout.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetVersions  # noqa: F401,E402


@pytest.mark.unit
class TestAssetVersionsAuxPrefix:
    def test_base_matches_writer_preview_layout(self):
        from backend.backend.handlers.assets import assetVersions as av
        from backend.backend.common.workflows import executionRecords as er

        base = av.aux_bucket_asset_file_base("db1", "asset-1/scans/pump.e57")
        assert base == "db1/asset-1/scans/pump.e57/"
        assert er.aux_preview_file_prefix("db1", "asset-1/scans/pump.e57").startswith(base)

    def test_preserves_custom_asset_base_prefix(self):
        from backend.backend.handlers.assets import assetVersions as av
        assert av.aux_bucket_asset_file_base("db1", "custom/base/a1/x.laz") == \
            "db1/custom/base/a1/x.laz/"

    def test_revert_deletes_scoped_prefix(self):
        from backend.backend.handlers.assets import assetVersions as av

        request = MagicMock()
        request.assetVersionId = "1"
        request.revertMetadata = False
        request.comment = None

        with patch.object(av, 'get_asset_with_permissions', return_value={
                    'assetId': 'asset-1', 'databaseId': 'db1', 'bucketId': 'bucket-1',
                    'assetLocation': {'Key': 'asset-1/'}, 'currentVersionId': '1',
                }), \
                patch.object(av, 'get_asset_s3_location', return_value=("asset-bucket", "asset-1/")), \
                patch.object(av, 'get_asset_version_metadata', return_value={'assetVersionId': '1'}), \
                patch.object(av, 'get_asset_file_versions', return_value={'files': [
                    {'relativeKey': '/scans/pump.e57', 'versionId': 'v1'},
                ]}), \
                patch.object(av, 'list_s3_files_with_versions', return_value=[]), \
                patch.object(av, 'does_file_version_exist', return_value=True), \
                patch.object(av, 'copy_s3_object_version', return_value='v-new'), \
                patch.object(av, 'delete_assetAuxiliary_files') as mock_delete, \
                patch.object(av, 'save_asset_file_versions', return_value=True), \
                patch.object(av, 'save_asset_metadata_version', return_value=True), \
                patch.object(av, 'update_asset_version_metadata', MagicMock()), \
                patch.object(av, 'send_subscription_email', MagicMock()):
            response = av.revert_asset_version("db1", "asset-1", request, {"tokens": ["alice"]})

        assert response.success is True
        mock_delete.assert_called_once_with("db1/asset-1/scans/pump.e57/")


@pytest.mark.unit
class TestAssetServiceAuxPrefix:
    def test_permanent_delete_passes_live_database_id(self):
        from backend.tests.handlers.assets.test_assetService_history import _load

        m = _load()
        asset = {
            "databaseId": "db1#deleted", "assetId": "a1", "assetName": "N1",
            "bucketId": "b1", "assetLocation": {"Key": "a1/"},
        }
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": dict(asset)}
        m.write_asset_history_record = MagicMock()
        m.send_subscription_email = MagicMock()
        m.get_asset_bucket_details = MagicMock(return_value={"bucketName": "bucket"})
        m.claims_and_roles = {"tokens": ["u1"]}
        m.delete_s3_prefix_all_versions = MagicMock(return_value=[])
        m.delete_assetAuxiliary_files = MagicMock()
        m.delete_asset_metadata_for_permanent_deletion = MagicMock()
        m.sns_client = MagicMock()
        m.subscription_table = MagicMock()
        m.asset_links_table = None
        m.asset_upload_table = None
        m.comment_table = None
        m.versions_table = None
        m.asset_versions_files_table = None
        m.asset_file_metadata_versions_table = None
        request = MagicMock()
        request.confirmPermanentDelete = True

        m.delete_asset_permanent("db1#deleted", "a1", request, {"tokens": ["u1"]})

        m.delete_assetAuxiliary_files.assert_called_once_with("db1", {"Key": "a1/"})
