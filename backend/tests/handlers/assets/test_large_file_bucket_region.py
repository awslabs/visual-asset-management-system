# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The >1 GB asynchronous upload path carries each bucket's Region in its SQS message.

`sqsUploadFileLarge` learns its buckets only by name from the message, so without the Region it
would resolve every name through the buckets table before its first S3 call. `uploadFile` already
holds the bucket details when it queues the message, so the message names the destination bucket's
Region and, for a workflow output staged in the default run bucket, the source bucket's Region. The
processor registers both with the Region-routing client before anything else, so the hot path makes
no lookup; a message queued without the fields (one in flight across a deployment) still resolves
through the lookup.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402

from tests.handlers.assets.test_large_file_workflow_provenance import _external_request  # noqa: E402


def _queue_external_upload(request_model, file_size, bucket_details, source_region_lookup=None):
    from backend.backend.handlers.assets import uploadFile as uf

    captured = {}

    def _capture(file_info, queue_url):
        captured.update(file_info)
        return True

    lookup = MagicMock(side_effect=source_region_lookup or (lambda name: "us-west-2"))
    with patch.object(uf, 'get_upload_details', return_value={
        'assetId': 'asset-1', 'databaseId': 'db-1', 'uploadType': 'assetFile',
        'isExternalUpload': True, 'temporaryPrefix': 'temp/up-1/',
    }), patch.object(uf, 'get_asset_details', return_value={
        'assetId': 'asset-1', 'databaseId': 'db-1', 'bucketId': 'bucket-1',
        'assetLocation': {'Key': 'asset-1/'},
    }), patch.object(uf, 'get_default_bucket_details', return_value=bucket_details), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': 'db-1'}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3, \
            patch.object(uf, 'bucket_region_for_name', lookup), \
            patch.object(uf, 'queue_large_file_for_processing', side_effect=_capture):
        mock_s3.head_object.return_value = {'ContentLength': file_size}
        uf.claims_and_roles = {"tokens": ["alice@corp"]}
        uf.complete_external_upload("up-1", request_model, {})
    return captured, lookup


REMOTE_BUCKET = {'bucketId': 'bucket-1', 'bucketName': 'remote-bucket', 'baseAssetsPrefix': '',
                 'bucketRegion': 'us-east-1'}


@pytest.mark.unit
class TestQueuedMessageNamesTheRegions:
    def test_destination_region_comes_from_the_bucket_details(self):
        file_info, lookup = _queue_external_upload(
            _external_request(), uploadFile.LARGE_FILE_THRESHOLD_BYTES + 1, REMOTE_BUCKET)
        assert file_info["bucketName"] == "remote-bucket"
        assert file_info["bucketRegion"] == "us-east-1"
        # Source == destination: the same Region, with no lookup for it.
        assert file_info["sourceBucketName"] == "remote-bucket"
        assert file_info["sourceBucketRegion"] == "us-east-1"
        lookup.assert_not_called()

    def test_a_workflow_output_names_the_run_bucket_region_separately(self):
        file_info, lookup = _queue_external_upload(
            _external_request(sourceBucket="run-bucket"), uploadFile.LARGE_FILE_THRESHOLD_BYTES + 1,
            REMOTE_BUCKET)
        assert file_info["sourceBucketName"] == "run-bucket"
        assert file_info["sourceBucketRegion"] == "us-west-2"
        assert file_info["bucketRegion"] == "us-east-1"
        lookup.assert_called_once_with("run-bucket")


@pytest.mark.unit
class TestProcessorRegistersTheRegionsFirst:
    def _file_info(self, **extra):
        info = {
            "relativeKey": "/out/scan.laz", "uploadIdS3": "external", "parts": [],
            "tempS3Key": "temp/up-1/scan.laz", "finalS3Key": "asset-1/out/scan.laz",
            "bucketName": "remote-bucket", "databaseId": "db-1", "assetId": "asset-1",
            "uploadId": "up-1", "uploadType": "assetFile",
        }
        info.update(extra)
        return info

    def _run(self, file_info):
        from backend.backend.handlers.assets import sqsUploadFileLarge as sq
        order = []
        register = MagicMock(side_effect=lambda name, region: order.append(("register", name, region)))
        complete = MagicMock(side_effect=lambda *a, **k: order.append(("complete",)) or True)
        with patch.object(sq, 'register_bucket_region', register), \
                patch.object(sq, 'complete_multipart_upload_for_large_file', complete), \
                patch.object(sq, 'validate_and_move_large_file', return_value=True), \
                patch.object(sq, 'cleanup_failed_processing', MagicMock()):
            assert sq.process_large_file(file_info, {}) is True
        return order

    def test_both_regions_are_registered_before_the_first_s3_step(self):
        order = self._run(self._file_info(bucketRegion="us-east-1", sourceBucketName="run-bucket",
                                          sourceBucketRegion="us-west-2"))
        registers = [o for o in order if o[0] == "register"]
        assert registers == [("register", "remote-bucket", "us-east-1"),
                             ("register", "run-bucket", "us-west-2")]
        assert order.index(registers[-1]) < order.index(("complete",))

    def test_a_message_without_the_fields_registers_nothing_and_still_processes(self):
        # register_bucket_region ignores a missing Region (the real helper is a no-op on None), so an
        # in-flight message from before the field existed keeps resolving through the table lookup.
        order = self._run(self._file_info())
        assert [o for o in order if o[0] == "register"] == [
            ("register", "remote-bucket", None), ("register", None, None)]
        assert ("complete",) in order

    def test_the_real_register_helper_ignores_a_missing_region(self):
        from common import s3 as common_s3
        before = dict(common_s3._bucket_regions_by_name)
        common_s3.register_bucket_region("remote-bucket", None)
        common_s3.register_bucket_region(None, "us-east-1")
        assert common_s3._bucket_regions_by_name == before
