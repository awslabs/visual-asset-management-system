# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Creating or reverting an asset version records the new version as the asset row's latest change:
the row written back carries `lastChangeSource: assetVersion` and a fresh `lastChangeAt`, keeps the
`lastUploadAt` of the last user upload, and drops `lastChangeWorkflowExecutionId`. That row write is
the stream MODIFY the compliance trigger evaluates on, so a version-coverage verdict follows the new
version rather than waiting for the next unrelated change."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder (see S27-TEST-001).
from backend.backend.handlers.assets import assetVersions as _assetVersions

DB = "db1"
ASSET = "asset-1"
LAST_UPLOAD_AT = "2026-03-01T12:00:00+00:00"


def _asset():
    return {
        "databaseId": DB, "assetId": ASSET, "assetName": "N", "description": "desc",
        "bucketId": "bucket-1", "assetLocation": {"Key": f"{ASSET}/"}, "currentVersionId": "5",
        "lastChangeSource": "workflowExecution", "lastChangeAt": LAST_UPLOAD_AT,
        "lastChangeWorkflowExecutionId": "exec-1", "lastUploadAt": LAST_UPLOAD_AT,
    }


def _written_row(runner):
    av = _assetVersions
    asset_table = MagicMock(name="asset_table")
    with patch.object(av, "asset_table", asset_table), \
            patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset()), \
            patch.object(av, "get_asset_s3_location", return_value=("asset-bucket", f"{ASSET}/")), \
            patch.object(av, "list_s3_files_with_versions", return_value=[{
                "relativeKey": "model.glb", "versionId": "s3v1", "size": 1,
                "lastModified": "2026-01-01T00:00:00", "etag": "e"}]), \
            patch.object(av, "reserve_next_asset_version", return_value="6"), \
            patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "2"}), \
            patch.object(av, "get_asset_file_versions", return_value={"files": []}), \
            patch.object(av, "save_asset_file_versions", return_value=True), \
            patch.object(av, "save_asset_metadata_version", return_value=True), \
            patch.object(av, "mark_assetVersion_as_current", return_value=True), \
            patch.object(av, "send_subscription_email"):
        runner(av)
    assert asset_table.put_item.call_count == 1
    return asset_table.put_item.call_args.kwargs["Item"]


def _create(av):
    request = MagicMock(useLatestFiles=True, files=[], comment="c", versionAlias=None)
    av.create_asset_version(DB, ASSET, request, {"tokens": ["alice"]})


def _revert(av):
    request = MagicMock(assetVersionId="2", revertMetadata=False, comment="c")
    av.revert_asset_version(DB, ASSET, request, {"tokens": ["alice"]})


@pytest.mark.unit
@pytest.mark.parametrize("runner", [_create, _revert], ids=["create", "revert"])
class TestAssetVersionProvenance:

    def test_the_row_records_the_version_as_its_latest_change(self, runner):
        row = _written_row(runner)
        assert row["currentVersionId"] == "6"
        assert row["lastChangeSource"] == "assetVersion"
        assert datetime.fromisoformat(row["lastChangeAt"]) > datetime.fromisoformat(LAST_UPLOAD_AT)

    def test_the_last_upload_instant_is_left_as_it_is(self, runner):
        assert _written_row(runner)["lastUploadAt"] == LAST_UPLOAD_AT

    def test_a_previous_workflow_execution_id_is_dropped(self, runner):
        assert "lastChangeWorkflowExecutionId" not in _written_row(runner)
