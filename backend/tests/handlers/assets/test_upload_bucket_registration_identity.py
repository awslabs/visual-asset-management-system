# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The internal external-upload completion and the large-file queue identify buckets by bucketId (#1891).

- The completion resolves the bucket a workflow execution's write-back reads from (the VAMS default
  asset bucket, where the pipelines staged the outputs) itself. A `sourceBucket` the caller sends is
  logged when it differs and never chooses the bucket.
- The large-file queue message carries the destination registration (`bucketId`) and, for a staged
  workflow output, the source registration (`sourceBucketId`) beside the bucket names.
- The queue consumer writes only when the asset is in the named registration and each bucketId resolves
  to the bucket name the message carries; a message from an earlier release (no bucketId) is processed
  as before.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402
from backend.backend.handlers.assets import sqsUploadFileLarge as sq  # noqa: E402

ASSET_BUCKET = {'bucketId': 'reg-asset', 'bucketName': 'asset-bucket', 'baseAssetsPrefix': '',
                'bucketRegion': 'us-east-1'}
EXEC = "0123456789abcdef0123456789abcdef"
RUN_BUCKET = {'bucketId': 'reg-run', 'bucketName': 'run-bucket', 'baseAssetsPrefix': 'vams/'}


def _request(**overrides):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    body = {"assetId": "asset-1", "databaseId": "db-1", "uploadType": "assetFile",
            "files": [{"relativeKey": "/out/scan.laz", "tempKey": "vams/temp/scan.laz"}]}
    body.update(overrides)
    return CompleteExternalUploadRequestModel(**body)


def _queue(request_model, run_bucket=RUN_BUCKET):
    captured = {}

    def _capture(file_info, queue_url):
        captured.update(file_info)
        return True

    with patch.object(uf, 'get_upload_details', return_value={
        'assetId': 'asset-1', 'databaseId': 'db-1', 'uploadType': 'assetFile',
        'isExternalUpload': True, 'temporaryPrefix': 'vams/temp/'}), \
            patch.object(uf, 'get_asset_details', return_value={
                'assetId': 'asset-1', 'databaseId': 'db-1', 'bucketId': 'reg-asset',
                'assetLocation': {'Key': 'asset-1/'}}), \
            patch.object(uf, 'get_default_bucket_details', return_value=ASSET_BUCKET), \
            patch.object(uf, 'resolve_default_bucket', return_value=run_bucket), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': 'db-1'}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'bucket_region_for_name', return_value='us-west-2'), \
            patch.object(uf, 'queue_large_file_for_processing', side_effect=_capture):
        mock_s3.head_object.return_value = {'ContentLength': uf.LARGE_FILE_THRESHOLD_BYTES + 1}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        uf.complete_external_upload("up-1", request_model, {})
    return captured


@pytest.mark.unit
class TestCompletionResolvesItsSource:
    def test_a_workflow_write_back_reads_from_the_resolved_run_bucket_not_the_named_one(self):
        file_info = _queue(_request(workflowId="wf1", workflowExecutionId=EXEC, sourceBucket="named-bucket"))
        assert file_info["sourceBucketName"] == "run-bucket"
        assert (file_info["bucketId"], file_info["sourceBucketId"]) == ("reg-asset", "reg-run")
        assert file_info["bucketName"] == "asset-bucket"

    def test_a_completion_without_an_execution_reads_from_the_asset_bucket(self):
        file_info = _queue(_request(sourceBucket="named-bucket"))
        assert file_info["sourceBucketName"] == "asset-bucket"
        assert file_info["sourceBucketId"] == "reg-asset"

    def test_a_run_bucket_that_is_the_asset_bucket_reads_from_the_asset_bucket(self):
        with patch.object(uf, 'resolve_default_bucket', return_value=dict(ASSET_BUCKET)):
            source = uf.external_upload_source(EXEC, ASSET_BUCKET)
        assert source is ASSET_BUCKET

    def test_an_unresolvable_run_bucket_fails_the_completion(self):
        with patch.object(uf, 'resolve_default_bucket', side_effect=uf.DefaultBucketNotFoundError("none")):
            with pytest.raises(uf.VAMSGeneralErrorResponse):
                uf.external_upload_source(EXEC, ASSET_BUCKET)


def _message(**extra):
    info = {"relativeKey": "/out/scan.laz", "uploadIdS3": "external", "parts": [],
            "tempS3Key": "vams/temp/scan.laz", "finalS3Key": "asset-1/out/scan.laz",
            "bucketName": "asset-bucket", "databaseId": "db-1", "assetId": "asset-1",
            "uploadId": "up-1", "uploadType": "assetFile"}
    info.update(extra)
    return info


REGISTRATIONS = {'reg-asset': {'bucketId': 'reg-asset', 'bucketName': 'asset-bucket'},
                 'reg-run': {'bucketId': 'reg-run', 'bucketName': 'run-bucket'}}


def _process(file_info, asset_bucket_id='reg-asset'):
    complete = MagicMock(return_value=True)
    with patch.object(sq, 'register_bucket_region', MagicMock()), \
            patch.object(sq, 'already_handed_off', return_value=''), \
            patch.object(sq, 'read_registration', side_effect=REGISTRATIONS.get), \
            patch.object(sq, 'get_asset_details', return_value={'bucketId': asset_bucket_id}), \
            patch.object(sq, 'complete_multipart_upload_for_large_file', complete), \
            patch.object(sq, 'validate_and_move_large_file', return_value=True), \
            patch.object(sq, 'cleanup_failed_processing', MagicMock()) as cleanup, \
            patch.object(sq, 'audit_rejected_large_file', MagicMock()), \
            patch.object(sq, 'record_file_outcome', MagicMock()) as outcome:
        result = sq.process_large_file(file_info, {})
    return result, complete, cleanup, outcome


@pytest.mark.unit
class TestConsumerChecksTheRegistrations:
    def test_a_message_whose_registrations_match_is_processed(self):
        result, complete, _, _ = _process(_message(bucketId='reg-asset', sourceBucketId='reg-run',
                                                   sourceBucketName='run-bucket'))
        assert result is True and complete.called

    @pytest.mark.parametrize('message, asset_bucket_id', [
        (dict(bucketId='reg-asset', bucketName='other-bucket'), 'reg-asset'),
        (dict(bucketId='reg-asset'), 'reg-other'),
        (dict(bucketId='reg-missing'), 'reg-missing'),
        (dict(bucketId='reg-asset', sourceBucketId='reg-run', sourceBucketName='other-bucket'), 'reg-asset'),
    ], ids=['destination-name', 'asset-registration', 'unknown-registration', 'source-name'])
    def test_a_mismatch_is_refused_before_anything_is_written(self, message, asset_bucket_id):
        result, complete, cleanup, outcome = _process(_message(**message), asset_bucket_id=asset_bucket_id)
        assert result is False
        complete.assert_not_called()
        cleanup.assert_not_called()
        assert outcome.call_args.args[1] is False

    def test_a_message_from_an_earlier_release_is_processed_as_before(self):
        result, complete, _, _ = _process(_message(), asset_bucket_id='reg-other')
        assert result is True and complete.called
