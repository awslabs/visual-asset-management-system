# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset-record writes on the upload-completion path.

An upload completion reads the asset once and finishes seconds to minutes later, so any
field a concurrent writer changed in between is still in the record it holds. The
completion owns exactly two attributes -- ``assetType`` for an asset-file upload and
``previewLocation`` for a preview upload -- so it must write only those, and only to a
record that still exists: a full-record write reverts the concurrent edit, and an
unconditional write recreates an asset removed during the upload.
"""

import os
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

# Set env vars required by uploadFile at import time (before importing the module).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

DATABASE_ID = "db-1"
ASSET_ID = "asset-1"
BUCKET = "asset-bucket"


def _real_to_update_expr(record, op="SET"):
    """The real `common.dynamodb.to_update_expr`.

    The handler binds `to_update_expr` at import time and `tests/conftest.py` re-registers
    `sys.modules['common.dynamodb']`, so the bound name can be a stand-in whose call yields
    nothing to unpack into three values. Patching the real logic in is what makes the
    expression the handler builds observable -- the same approach as
    `tests/handlers/workflows/test_workflowService.py`.
    """
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(keys_attr_names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values_attr_names)}
    expr = "{op} ".format(op=op) + ", ".join(
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names))
    return keys_map, values_map, expr


class FakeAssetTable:
    """In-memory asset table that applies a targeted SET update to the stored item.

    Records put_item separately so a full-record write is distinguishable from an
    attribute update, and honors an attribute_exists ConditionExpression so a write
    against a removed record fails the way DynamoDB fails it.
    """

    def __init__(self, item=None):
        self.items = {}
        if item is not None:
            self.items[(item['databaseId'], item['assetId'])] = dict(item)
        self.put_item_calls = []
        self.updated_attributes = []

    def stored(self):
        return self.items.get((DATABASE_ID, ASSET_ID))

    def put_item(self, Item, **kwargs):
        self.put_item_calls.append(Item)
        self.items[(Item['databaseId'], Item['assetId'])] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ConditionExpression=None, **kwargs):
        item = self.items.get((Key['databaseId'], Key['assetId']))
        if ConditionExpression and 'attribute_exists' in ConditionExpression and item is None:
            raise ClientError(
                {'Error': {'Code': 'ConditionalCheckFailedException',
                           'Message': 'The conditional request failed'}},
                'UpdateItem')
        assert UpdateExpression.startswith('SET '), UpdateExpression
        written = {}
        for assignment in UpdateExpression[len('SET '):].split(', '):
            name_ref, value_ref = [part.strip() for part in assignment.split(' = ')]
            written[ExpressionAttributeNames[name_ref]] = ExpressionAttributeValues[value_ref]
        self.updated_attributes.append(written)
        if item is not None:
            item.update(written)


def _asset(**overrides):
    item = {
        'databaseId': DATABASE_ID,
        'assetId': ASSET_ID,
        'bucketId': 'bucket-1',
        'assetLocation': {'Key': 'asset-1/'},
        'description': 'original',
        'tags': ['keep'],
        'assetType': 'none',
    }
    item.update(overrides)
    return item


def _file_request():
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    return CompleteExternalUploadRequestModel(
        assetId=ASSET_ID,
        databaseId=DATABASE_ID,
        uploadType="assetFile",
        files=[{"relativeKey": "/out/scan.laz", "tempKey": "temp/up-1/scan.laz"}],
    )


def _preview_request():
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    return CompleteExternalUploadRequestModel(
        assetId=ASSET_ID,
        databaseId=DATABASE_ID,
        uploadType="assetPreview",
        files=[{"relativeKey": "thumb.png", "tempKey": "temp/up-1/thumb.png"}],
    )


def _complete_external(request_model, table, asset_read, asset_type='folder'):
    """Run complete_external_upload against the fake asset table.

    asset_read is the record the completion holds -- the snapshot taken before the
    concurrent edit that `table` already carries.
    """
    from backend.backend.handlers.assets import uploadFile as uf

    with patch.object(uf, 'get_upload_details', return_value={
        'assetId': ASSET_ID,
        'databaseId': DATABASE_ID,
        'uploadType': request_model.uploadType,
        'isExternalUpload': True,
        'temporaryPrefix': 'temp/up-1/',
    }), patch.object(uf, 'get_asset_details', return_value=asset_read), \
            patch.object(uf, 'get_default_bucket_details', return_value={
                'bucketId': 'bucket-1',
                'bucketName': BUCKET,
                'baseAssetsPrefix': '',
            }), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': DATABASE_ID}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'validateS3AssetExtensionsAndContentType', return_value=True), \
            patch.object(uf, 'copy_s3_object', return_value=True), \
            patch.object(uf, 'delete_s3_object', MagicMock()), \
            patch.object(uf, 'determine_asset_type', return_value=asset_type), \
            patch.object(uf, 'send_subscription_email', MagicMock()), \
            patch.object(uf, 'to_update_expr', _real_to_update_expr), \
            patch.object(uf, 'asset_table', table):
        mock_s3.head_object.return_value = {'ContentLength': 1024}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        return uf.complete_external_upload("up-1", request_model, {})


@pytest.mark.unit
class TestAssetFileCompletionWrite:
    def test_concurrent_description_edit_survives_the_completion(self):
        table = FakeAssetTable(_asset(description='edited'))
        response = _complete_external(_file_request(), table, _asset(description='original'))

        assert response.overallSuccess is True
        assert table.stored()['description'] == 'edited'

    def test_completion_does_not_rewrite_the_whole_record(self):
        table = FakeAssetTable(_asset())
        _complete_external(_file_request(), table, _asset())

        assert table.put_item_calls == []
        assert table.updated_attributes == [{'assetType': 'folder'}]

    def test_completion_still_records_the_determined_asset_type(self):
        """POSITIVE CONTROL: the attribute the completion owns is still written."""
        table = FakeAssetTable(_asset())
        response = _complete_external(_file_request(), table, _asset())

        assert table.stored()['assetType'] == 'folder'
        assert response.assetType == 'folder'

    def test_asset_removed_during_the_upload_is_not_recreated(self):
        from backend.backend.handlers.assets import uploadFile as uf
        table = FakeAssetTable()  # the asset was archived/deleted mid-upload

        with pytest.raises(uf.VAMSGeneralErrorResponse):
            _complete_external(_file_request(), table, _asset())

        assert table.items == {}
        assert table.put_item_calls == []


def _multipart_request(uploadType, relativeKey):
    from backend.backend.models.assetsV3 import CompleteUploadRequestModel
    return CompleteUploadRequestModel(
        assetId=ASSET_ID,
        databaseId=DATABASE_ID,
        uploadType=uploadType,
        files=[{
            "relativeKey": relativeKey,
            "uploadIdS3": "s3-upload-1",
            "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
        }],
    )


def _complete_multipart(request_model, table, asset_read, asset_type='folder'):
    """Run complete_upload (the internal multipart path) against the fake asset table."""
    from backend.backend.handlers.assets import uploadFile as uf

    with patch.object(uf, 'get_upload_details', return_value={
        'assetId': ASSET_ID,
        'databaseId': DATABASE_ID,
        'uploadType': request_model.uploadType,
    }), patch.object(uf, 'get_asset_details', return_value=asset_read), \
            patch.object(uf, 'get_default_bucket_details', return_value={
                'bucketId': 'bucket-1',
                'bucketName': BUCKET,
                'baseAssetsPrefix': '',
            }), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': DATABASE_ID}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 'calculate_total_file_size_from_parts', return_value=(1024, True, None)), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'validateS3AssetExtensionsAndContentType', return_value=True), \
            patch.object(uf, 'copy_s3_object', return_value=True), \
            patch.object(uf, 'delete_s3_object', MagicMock()), \
            patch.object(uf, 'determine_asset_type', return_value=asset_type), \
            patch.object(uf, 'send_subscription_email', MagicMock()), \
            patch.object(uf, 'to_update_expr', _real_to_update_expr), \
            patch.object(uf, 'asset_table', table):
        mock_s3.head_object.return_value = {
            'ContentLength': 1024,
            'Metadata': {uf.UPLOAD_ID_METADATA_KEY: 'up-1'},
        }
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        return uf.complete_upload("up-1", request_model, {})


@pytest.mark.unit
class TestInternalMultipartCompletionWrite:
    """The same two writes on the multipart path, which is the one the web client uses."""

    def test_concurrent_description_edit_survives_the_multipart_completion(self):
        table = FakeAssetTable(_asset(description='edited'))
        response = _complete_multipart(
            _multipart_request("assetFile", "/out/scan.laz"), table, _asset(description='original'))

        assert response.overallSuccess is True
        assert table.stored()['description'] == 'edited'

    def test_multipart_completion_writes_only_the_asset_type(self):
        table = FakeAssetTable(_asset())
        _complete_multipart(_multipart_request("assetFile", "/out/scan.laz"), table, _asset())

        assert table.put_item_calls == []
        assert table.updated_attributes == [{'assetType': 'folder'}]

    def test_multipart_completion_still_records_the_determined_asset_type(self):
        """POSITIVE CONTROL: the attribute the completion owns is still written."""
        table = FakeAssetTable(_asset())
        response = _complete_multipart(
            _multipart_request("assetFile", "/out/scan.laz"), table, _asset())

        assert table.stored()['assetType'] == 'folder'
        assert response.assetType == 'folder'

    def test_multipart_preview_completion_writes_only_the_preview_location(self):
        table = FakeAssetTable(_asset(tags=['edited']))
        response = _complete_multipart(
            _multipart_request("assetPreview", "thumb.png"), table, _asset(tags=['keep']))

        expected_key = f"{uploadFile.PREVIEW_PREFIX}{ASSET_ID}/thumb.png"
        assert response.overallSuccess is True
        assert table.updated_attributes == [{'previewLocation': {'Key': expected_key}}]
        assert table.put_item_calls == []
        assert table.stored()['tags'] == ['edited']


@pytest.mark.unit
class TestAssetPreviewCompletionWrite:
    def test_concurrent_tag_edit_survives_the_preview_completion(self):
        table = FakeAssetTable(_asset(tags=['edited']))
        response = _complete_external(_preview_request(), table, _asset(tags=['keep']))

        assert response.overallSuccess is True
        assert table.stored()['tags'] == ['edited']

    def test_preview_completion_still_sets_the_preview_location(self):
        """POSITIVE CONTROL: the attribute the preview completion owns is still written."""
        table = FakeAssetTable(_asset())
        _complete_external(_preview_request(), table, _asset())

        expected_key = f"{uploadFile.PREVIEW_PREFIX}{ASSET_ID}/thumb.png"
        assert table.stored()['previewLocation'] == {'Key': expected_key}

    def test_preview_completion_writes_only_the_preview_location(self):
        table = FakeAssetTable(_asset())
        _complete_external(_preview_request(), table, _asset())

        expected_key = f"{uploadFile.PREVIEW_PREFIX}{ASSET_ID}/thumb.png"
        assert table.updated_attributes == [{'previewLocation': {'Key': expected_key}}]
        assert table.put_item_calls == []
