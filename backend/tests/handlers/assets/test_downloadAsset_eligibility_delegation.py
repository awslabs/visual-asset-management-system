# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""downloadAsset delegates its eligibility checks to common.downloadEligibility.

The handler keeps `get_distributable_asset_context`, `resolve_and_sign_file_key` and
`check_s3_object_exists` as thin wrappers, and re-exports `is_file_archived` and `normalize_s3_path`
from the shared module. The wrappers pass the handler's own S3 client, getters, resolvers, blocklist
check, object listing and worker pool to the shared module at CALL time, so patching one of those
names on downloadAsset changes what the shared checks call. The whole-asset prefix check has no
handler wrapper: `check_file_eligibility` calls the shared `validate_prefix_content_types` with the
handler's client, listing, blocklist check and worker pool. Signing stays in the handler. These tests pin that wiring and the two outcomes the existing suites
do not reach through the HTTP response: a delete-marker version answers 410, and a missing file in
a bulk request is a soft failure carrying "File not found in S3".
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

from tests.handlers.assets.test_downloadAsset_bulk import _load, _wire_asset_context  # noqa: E402

de = sys.modules['common.downloadEligibility']


def _msg(message):
    """The message as it reaches the response: VAMSGeneralErrorResponse prefixes it."""
    return f"VAMS General Error: {message}"


def _event(body):
    return {
        'body': json.dumps(body),
        'pathParameters': {'databaseId': 'db1', 'assetId': 'asset1'},
        'requestContext': {'http': {'method': 'POST', 'path': '/database/db1/assets/asset1/download'}},
    }


def _head_error(code):
    def _head(Bucket, Key, **kwargs):
        raise ClientError({"Error": {"Code": code}}, "HeadObject")
    return _head


def _parse(body):
    from aws_lambda_powertools.utilities.parser import parse
    from models.assetsV3 import DownloadAssetRequestModel
    return parse(body, model=DownloadAssetRequestModel)


@pytest.mark.unit
class TestDelegation:
    def test_handler_uses_the_shared_module(self):
        m = _load()
        assert m.check_file_eligibility is de.check_file_eligibility
        assert m.is_file_archived is de.is_file_archived
        assert m.normalize_s3_path is de.normalize_s3_path
        assert m.VAMSGeneralErrorResponse is de.VAMSGeneralErrorResponse

    def test_wrappers_read_patched_names_at_call_time(self):
        """A name patched on downloadAsset is what the shared checks call."""
        m = _load()
        _wire_asset_context(m)
        m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=False)

        with pytest.raises(m.VAMSGeneralErrorResponse) as e:
            m.download_asset_file('db1', 'asset1', _parse({'downloadType': 'assetFile', 'key': '/a.txt'}))
        assert str(e.value) == _msg("Unallowed file extension or content type in asset file")
        m.validateUnallowedFileExtensionAndContentType.assert_called_once_with('asset1/a.txt', 'text/plain')
        assert m.s3.generate_presigned_url.call_count == 0

    def test_prefix_existence_goes_through_the_handler_helper(self):
        m = _load()
        _wire_asset_context(m)
        m.list_all_objects = MagicMock(return_value=[])
        m.check_s3_object_exists = MagicMock(return_value=False)

        with pytest.raises(m.VAMSGeneralErrorResponse) as e:
            m.download_asset_file('db1', 'asset1', _parse({'downloadType': 'assetFile', 'key': '/'}))
        assert str(e.value) == _msg("File not found in S3")
        m.check_s3_object_exists.assert_called_once_with('test-bucket', 'asset1/')

    def test_prefix_check_gets_the_handler_settings(self):
        """A whole-asset download runs the shared prefix check with the handler's settings."""
        m = _load()
        _wire_asset_context(m)
        m.list_all_objects = MagicMock(return_value=[])
        captured = {}
        real = de.validate_prefix_content_types

        def _spy(client, bucket, prefix, **kwargs):
            captured.update(kwargs, client=client, bucket=bucket, prefix=prefix)
            return real(client, bucket, prefix, **kwargs)
        de.validate_prefix_content_types = _spy
        try:
            response = m.download_asset_file('db1', 'asset1', _parse({'downloadType': 'assetFile', 'key': '/'}))
        finally:
            de.validate_prefix_content_types = real
        assert response.downloadUrl == "https://signed.example/asset1/"
        assert captured['client'] is m.s3
        assert (captured['bucket'], captured['prefix']) == ('test-bucket', 'asset1/')
        assert captured['max_workers'] == m.MAX_PARALLEL_S3_WORKERS
        assert captured['list_objects'] is m.list_all_objects
        assert captured['is_allowed'] is m.validateUnallowedFileExtensionAndContentType


@pytest.mark.unit
class TestOutcomesThroughTheHandler:
    def test_delete_marker_version_answers_410(self):
        m = _load()
        _wire_asset_context(m)
        m.s3.head_object.side_effect = _head_error('405')

        response = m.lambda_handler(_event({'downloadType': 'assetFile', 'key': '/a.txt',
                                            'versionId': 'v-dm'}), None)

        assert response['statusCode'] == 410
        assert json.loads(response['body'])['message'] == \
            _msg("File version has been archived and cannot be downloaded")
        assert m.s3.generate_presigned_url.call_count == 0

    def test_missing_single_file_answers_400(self):
        m = _load()
        _wire_asset_context(m)
        m.s3.head_object.side_effect = _head_error('404')

        response = m.lambda_handler(_event({'downloadType': 'assetFile', 'key': '/a.txt'}), None)

        assert response['statusCode'] == 400
        assert json.loads(response['body'])['message'] == _msg("File not found in S3")

    def test_bulk_missing_file_is_a_soft_failure_with_its_reason(self):
        m = _load()
        _wire_asset_context(m)

        def _head(Bucket, Key, **kwargs):
            if 'missing' in Key:
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {'ContentType': 'text/plain', 'ContentLength': 10}
        m.s3.head_object.side_effect = _head

        response = m.download_asset_files_bulk(
            'db1', 'asset1', _parse({'downloadType': 'assetFile', 'keys': ['/good.txt', '/missing.txt']}))

        by_key = {f.key: f for f in response.files}
        assert by_key['/good.txt'].success is True
        assert by_key['/missing.txt'].success is False
        assert by_key['/missing.txt'].error == _msg("File not found in S3")
        assert response.message == ("Generated 1 of 2 download URLs. Warning: 1 file path(s) do not "
                                    "exist or are not downloadable and were skipped.")

    def test_bulk_delete_marker_is_a_soft_failure(self):
        m = _load()
        _wire_asset_context(m)

        def _head(Bucket, Key, **kwargs):
            if kwargs.get('VersionId') == 'v-dm':
                raise ClientError({"Error": {"Code": "405"}}, "HeadObject")
            return {'ContentType': 'text/plain', 'ContentLength': 10}
        m.s3.head_object.side_effect = _head

        response = m.download_asset_files_bulk('db1', 'asset1', _parse({
            'downloadType': 'assetFile',
            'keys': [{'key': '/a.txt', 'versionId': 'v-dm'}, '/b.txt']}))

        by_key = {f.key: f for f in response.files}
        assert by_key['/a.txt'].error == _msg("File version has been archived and cannot be downloaded")
        assert by_key['/b.txt'].success is True
