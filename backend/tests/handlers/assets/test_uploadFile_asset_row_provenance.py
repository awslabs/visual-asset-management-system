# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Change provenance on the asset row an upload completion updates.

The end-state Lambda of a workflow writes the execution's outputs back into the asset through an
``uploadFile`` cross-call carrying ``workflowExecutionId``. The S3 objects it writes carry
``vams-changesource: workflowExecution``, but the asset ROW update the completion performs
(``assetType`` / ``previewLocation``) carried no provenance, so the DynamoDB stream MODIFY it emits
was indistinguishable from a user's upload -- and the compliance trigger re-evaluated the asset on
its own pipeline rule's output. Every completion now records ``lastChangeSource`` (``upload`` or
``workflowExecution``), ``lastChangeWorkflowExecutionId`` (only for a workflow execution) and
``lastChangeAt`` on the row, through the one update helper both completion paths use, without
touching the conditional write that keeps a removed asset from being recreated.

The update expression the handler builds is deserialized back to attribute names and values, so
the assertion is on what DynamoDB would store rather than on the expression text.
"""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_UPLOAD, VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level import ensures the real backend.backend.handlers.assets package is populated in
# sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.tests.handlers.assets.test_uploadFile_asset_record_targeted_update import (  # noqa: E402
    ASSET_ID, DATABASE_ID, FakeAssetTable, _asset, _complete_external, _complete_multipart,
    _multipart_request, _real_to_update_expr)

EXECUTION_ID = "7324c89972d748a8ae3204ce71ed8d3f"


def _external_request(uploadType="assetFile", workflow_execution_id=None, workflow_id=None):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    files = ([{"relativeKey": "/out/model.glb", "tempKey": "temp/up-1/model.glb"}]
             if uploadType == "assetFile"
             else [{"relativeKey": "thumb.png", "tempKey": "temp/up-1/thumb.png"}])
    payload = {"assetId": ASSET_ID, "databaseId": DATABASE_ID, "uploadType": uploadType, "files": files}
    if workflow_execution_id is not None:
        payload["workflowExecutionId"] = workflow_execution_id
        payload["workflowId"] = workflow_id or "conversion-3d-basic"
        payload["changeUserId"] = "SYSTEM_USER"
    return CompleteExternalUploadRequestModel(**payload)


def _the_one_write(table):
    assert len(table.updated_attributes) == 1, table.updated_attributes
    return table.updated_attributes[0]


def _is_recent_iso_instant(value):
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, "lastChangeAt must be an aware instant"
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60


@pytest.mark.unit
class TestAPlainUploadRecordsUploadProvenance:

    def test_external_asset_file_completion(self):
        table = FakeAssetTable(_asset())
        _complete_external(_external_request(), table, _asset())

        written = _the_one_write(table)
        assert written['assetType'] == 'folder'
        assert written['lastChangeSource'] == VAMS_CHANGE_SOURCE_UPLOAD == 'upload'
        assert 'lastChangeWorkflowExecutionId' not in written
        _is_recent_iso_instant(written['lastChangeAt'])
        assert written['lastUploadAt'] == written['lastChangeAt']

    def test_external_preview_completion(self):
        table = FakeAssetTable(_asset())
        _complete_external(_external_request(uploadType="assetPreview"), table, _asset())

        written = _the_one_write(table)
        assert 'previewLocation' in written
        assert written['lastChangeSource'] == 'upload'
        assert 'lastChangeWorkflowExecutionId' not in written
        _is_recent_iso_instant(written['lastChangeAt'])

    def test_multipart_asset_file_completion(self):
        """The web client's path; the request model carries no workflow fields at all."""
        table = FakeAssetTable(_asset())
        _complete_multipart(_multipart_request("assetFile", "/out/scan.laz"), table, _asset())

        written = _the_one_write(table)
        assert written['assetType'] == 'folder'
        assert written['lastChangeSource'] == 'upload'
        assert 'lastChangeWorkflowExecutionId' not in written
        _is_recent_iso_instant(written['lastChangeAt'])

    def test_multipart_preview_completion(self):
        table = FakeAssetTable(_asset())
        _complete_multipart(_multipart_request("assetPreview", "thumb.png"), table, _asset())

        written = _the_one_write(table)
        assert 'previewLocation' in written
        assert written['lastChangeSource'] == 'upload'
        assert 'lastChangeWorkflowExecutionId' not in written


@pytest.mark.unit
class TestAWorkflowExecutionUploadRecordsWorkflowProvenance:

    def test_external_asset_file_completion_names_the_execution(self):
        table = FakeAssetTable(_asset())
        _complete_external(_external_request(workflow_execution_id=EXECUTION_ID), table, _asset())

        written = _the_one_write(table)
        assert written['assetType'] == 'folder'
        assert written['lastChangeSource'] == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION == 'workflowExecution'
        assert written['lastChangeWorkflowExecutionId'] == EXECUTION_ID
        _is_recent_iso_instant(written['lastChangeAt'])
        # The last user upload's instant stays on the row for the compliance trigger.
        assert 'lastUploadAt' not in written

    def test_external_preview_completion_names_the_execution(self):
        table = FakeAssetTable(_asset())
        _complete_external(_external_request(uploadType="assetPreview",
                                             workflow_execution_id=EXECUTION_ID), table, _asset())

        written = _the_one_write(table)
        assert 'previewLocation' in written
        assert written['lastChangeSource'] == 'workflowExecution'
        assert written['lastChangeWorkflowExecutionId'] == EXECUTION_ID

    def test_the_provenance_lands_on_the_stored_row(self):
        """What the DynamoDB stream MODIFY image will carry, which is what the compliance trigger
        reads."""
        table = FakeAssetTable(_asset(description='edited'))
        _complete_external(_external_request(workflow_execution_id=EXECUTION_ID), table, _asset())

        stored = table.stored()
        assert stored['lastChangeSource'] == 'workflowExecution'
        assert stored['lastChangeWorkflowExecutionId'] == EXECUTION_ID
        assert stored['description'] == 'edited', "the targeted write still leaves other fields alone"
        assert table.put_item_calls == []


@pytest.mark.unit
class TestTheUpdateHelper:

    def test_the_conditional_write_is_unchanged(self):
        from backend.backend.handlers.assets import uploadFile as uf
        table = MagicMock()
        with patch.object(uf, 'to_update_expr', _real_to_update_expr), patch.object(uf, 'asset_table', table):
            uf.update_asset_attributes(DATABASE_ID, ASSET_ID, {'assetType': 'folder'},
                                       workflow_execution_id=EXECUTION_ID)
        kwargs = table.update_item.call_args.kwargs
        assert kwargs['Key'] == {'databaseId': DATABASE_ID, 'assetId': ASSET_ID}
        condition = kwargs['ConditionExpression']
        assert 'attribute_exists(databaseId)' in condition and 'attribute_exists(assetId)' in condition
        written = {kwargs['ExpressionAttributeNames'][n.strip()]: kwargs['ExpressionAttributeValues'][v.strip()]
                   for n, v in (a.split(' = ') for a in kwargs['UpdateExpression'][len('SET '):].split(', '))}
        assert set(written) == {'assetType', 'lastChangeSource', 'lastChangeWorkflowExecutionId', 'lastChangeAt'}
        assert kwargs['UpdateExpression'].startswith('SET ') and 'REMOVE' not in kwargs['UpdateExpression']

    def test_an_upload_completions_write_stamps_the_upload_instant(self):
        from backend.backend.handlers.assets import uploadFile as uf
        table = MagicMock()
        with patch.object(uf, 'to_update_expr', _real_to_update_expr), patch.object(uf, 'asset_table', table):
            uf.update_asset_attributes(DATABASE_ID, ASSET_ID, {'assetType': 'folder'})
        kwargs = table.update_item.call_args.kwargs
        written = {kwargs['ExpressionAttributeNames'][n.strip()]: kwargs['ExpressionAttributeValues'][v.strip()]
                   for n, v in (a.split(' = ') for a in kwargs['UpdateExpression'][len('SET '):].split(', '))}
        assert set(written) == {'assetType', 'lastChangeSource', 'lastChangeAt', 'lastUploadAt'}
        assert written['lastUploadAt'] == written['lastChangeAt']

    def test_provenance_for_an_upload_and_for_an_execution(self):
        from backend.backend.handlers.assets import uploadFile as uf
        upload = uf.asset_change_provenance()
        assert upload['lastChangeSource'] == 'upload'
        assert set(upload) == {'lastChangeSource', 'lastChangeAt', 'lastUploadAt'}
        assert upload['lastUploadAt'] == upload['lastChangeAt']
        execution = uf.asset_change_provenance(EXECUTION_ID)
        assert execution['lastChangeSource'] == 'workflowExecution'
        assert execution['lastChangeWorkflowExecutionId'] == EXECUTION_ID
        assert set(execution) == {'lastChangeSource', 'lastChangeWorkflowExecutionId', 'lastChangeAt'}
        # An empty execution id is no execution.
        assert uf.asset_change_provenance("")['lastChangeSource'] == 'upload'
