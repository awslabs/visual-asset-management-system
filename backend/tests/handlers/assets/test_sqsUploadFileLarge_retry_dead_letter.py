# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retry, dead-letter and cleanup behavior of the >1GB asynchronous completion (#587).

The processor runs on LargeFileProcessingQueue (batchSize 1, maxReceiveCount 3, with a
dead-letter queue). A retryable failure (S3 5xx, throttling, a failed copy) must fail the
invocation so SQS redelivers the message and finally dead-letters it, keeping the
multipart upload and staged object so a later receive can finish the file; only the final
receive aborts the multipart upload. A rejection that no retry can change (blocked type,
invalid parts, upload ID mismatch) is cleaned up, audited and consumed.

Both producers are covered: API user uploads (a real multipart upload completed by the
processor) and workflow external uploads (uploadIdS3 "external", staged in a source
bucket).
"""

import json
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402
from common.s3MetadataKeys import (  # noqa: E402
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    UPLOAD_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)

GIB = 1024 * 1024 * 1024
# SentTimestamp of the queued message, and times before and after it
SENT_MS = 1_793_000_000_000
STORED_AFTER_SEND = datetime.fromtimestamp(SENT_MS / 1000 + 30, tz=timezone.utc)
STORED_BEFORE_SEND = datetime.fromtimestamp(SENT_MS / 1000 - 3600, tz=timezone.utc)


def _client_error(code, operation="CompleteMultipartUpload", status=500):
    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, operation)


def _user_upload_file_info(**overrides):
    """A queued API user upload (complete_upload): a real multipart upload."""
    file_info = {
        "relativeKey": "/big/scan.laz",
        "uploadIdS3": "real-s3-upload-id",
        "parts": [{"PartNumber": 1, "ETag": "etag-1"}, {"PartNumber": 2, "ETag": "etag-2"}],
        "tempS3Key": "temp-uploads/up-1/scan.laz",
        "finalS3Key": "asset-1/big/scan.laz",
        "bucketName": "asset-bucket",
        "databaseId": "db-1",
        "assetId": "asset-1",
        "uploadId": "up-1",
        "uploadType": "assetFile",
        "changeUserId": "user-1",
    }
    file_info.update(overrides)
    return file_info


def _external_upload_file_info(**overrides):
    """A queued workflow external upload (complete_external_upload), staged in the run bucket."""
    file_info = _user_upload_file_info(
        uploadIdS3="external",
        parts=[],
        tempS3Key="temp/up-1/scan.laz",
        sourceBucketName="run-bucket",
        workflowId="wf-1",
        workflowExecutionId="exec-1",
    )
    file_info.update(overrides)
    return file_info


def _event(file_info, receive_count=1, sent_timestamp_ms=None):
    attributes = {"ApproximateReceiveCount": str(receive_count)}
    if sent_timestamp_ms is not None:
        attributes["SentTimestamp"] = str(sent_timestamp_ms)
    return {"Records": [{
        "body": json.dumps({"fileInfo": file_info}),
        "attributes": attributes,
    }]}


def _upload_final_metadata(user_id="user-1"):
    """Metadata copy_s3_object stamps on the final object of a user upload."""
    return {
        DATABASE_ID_METADATA_KEY: "db-1",
        ASSET_ID_METADATA_KEY: "asset-1",
        VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_UPLOAD,
        VAMS_CHANGE_USER_ID_METADATA_KEY: user_id,
    }


def _workflow_final_metadata():
    """Metadata copy_s3_object stamps on the final object of a workflow output."""
    return {
        DATABASE_ID_METADATA_KEY: "db-1",
        ASSET_ID_METADATA_KEY: "asset-1",
        VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
        VAMS_CHANGE_USER_ID_METADATA_KEY: "user-1",
        VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY: "wf-1",
        VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: "exec-1",
    }


def _head_after_finish(final_key, final_metadata=None, last_modified=STORED_AFTER_SEND):
    """head_object as S3 answers it once a file finished: the staged object is gone and the
    final object exists (when final_metadata is given)."""
    def head(Bucket, Key, **_):
        if Key == final_key and final_metadata is not None:
            return {"Metadata": final_metadata, "ContentLength": 2 * GIB, "LastModified": last_modified}
        raise _client_error("404", "HeadObject", status=404)
    return head


def _context():
    context = MagicMock()
    context.aws_request_id = "req-1"
    return context


class _Env:
    """Patches the processor's S3 and side effects; exposes the spies."""

    def __init__(self, complete_error=None, copy_result=True, extension_ok=True,
                 metadata_upload_id="up-1", head_error=None):
        from backend.backend.handlers.assets import sqsUploadFileLarge as sq
        self.sq = sq
        self.s3 = MagicMock()
        if complete_error is not None:
            self.s3.complete_multipart_upload.side_effect = complete_error
        head = {"Metadata": {UPLOAD_ID_METADATA_KEY: metadata_upload_id}, "ContentLength": 2 * GIB}
        if head_error is not None:
            self.s3.head_object.side_effect = head_error
        else:
            self.s3.head_object.return_value = head
        self.delete = MagicMock(return_value=True)
        self.copy = MagicMock(return_value=copy_result)
        self.audit = MagicMock()
        self.extension_ok = extension_ok
        self._patches = []

    def __enter__(self):
        sq = self.sq
        self._patches = [
            patch.object(sq, "s3", self.s3),
            patch.object(sq, "delete_s3_object", self.delete),
            patch.object(sq, "copy_s3_object", self.copy),
            patch.object(sq, "log_file_upload", self.audit),
            patch.object(sq, "validateS3AssetExtensionsAndContentType", return_value=self.extension_ok),
            patch.object(sq, "update_asset_after_file_processing", MagicMock()),
            patch.object(sq, "update_asset_preview_location", MagicMock()),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False

    def deleted_keys(self):
        return [(c.args[0], c.args[1]) for c in self.delete.call_args_list]


@pytest.mark.unit
class TestRetryableFailuresAreRedelivered:
    @pytest.mark.parametrize("code", ["InternalError", "SlowDown", "ServiceUnavailable"])
    def test_transient_complete_error_fails_invocation_and_keeps_upload(self, code):
        with _Env(complete_error=_client_error(code)) as env:
            with pytest.raises(Exception):
                env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.s3.abort_multipart_upload.assert_not_called()
        env.delete.assert_not_called()
        env.copy.assert_not_called()

    def test_failed_copy_fails_invocation_and_keeps_staged_object(self):
        with _Env(copy_result=False) as env:
            with pytest.raises(Exception):
                env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.s3.abort_multipart_upload.assert_not_called()
        env.delete.assert_not_called()

    def test_failed_copy_of_external_upload_fails_invocation_and_keeps_staged_object(self):
        with _Env(copy_result=False) as env:
            with pytest.raises(Exception):
                env.sq.lambda_handler(_event(_external_upload_file_info()), _context())

        env.s3.abort_multipart_upload.assert_not_called()
        env.delete.assert_not_called()

    def test_failed_metadata_read_fails_invocation_and_keeps_staged_object(self):
        with _Env(head_error=_client_error("InternalError", "HeadObject")) as env:
            with pytest.raises(Exception):
                env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.s3.abort_multipart_upload.assert_not_called()
        env.delete.assert_not_called()

    def test_redelivery_after_earlier_completion_finishes_the_file(self):
        """An earlier receive completed the upload and then failed: NoSuchUpload with the
        staged object present continues to the copy."""
        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.sq.lambda_handler(_event(_user_upload_file_info(), receive_count=2), _context())

        env.copy.assert_called_once()
        assert env.deleted_keys() == [("asset-bucket", "temp-uploads/up-1/scan.laz")]
        assert env.audit.call_args.args[4] is False


@pytest.mark.unit
class TestFinalReceive:
    def test_final_receive_aborts_upload_and_still_fails_invocation(self):
        with _Env(complete_error=_client_error("SlowDown", status=503)) as env:
            with pytest.raises(Exception):
                env.sq.lambda_handler(
                    _event(_user_upload_file_info(), receive_count=env.sq.LARGE_FILE_MAX_RECEIVE_COUNT),
                    _context())

        env.s3.abort_multipart_upload.assert_called_once_with(
            Bucket="asset-bucket", Key="temp-uploads/up-1/scan.laz", UploadId="real-s3-upload-id")
        # The staged object (if any) is kept for a redrive from the dead-letter queue
        env.delete.assert_not_called()

    def test_final_receive_of_external_upload_keeps_staged_object(self):
        with _Env(copy_result=False) as env:
            with pytest.raises(Exception):
                env.sq.lambda_handler(
                    _event(_external_upload_file_info(), receive_count=env.sq.LARGE_FILE_MAX_RECEIVE_COUNT),
                    _context())

        env.s3.abort_multipart_upload.assert_not_called()
        env.delete.assert_not_called()


@pytest.mark.unit
class TestDeterministicRejectionsAreConsumed:
    def test_blocked_type_is_cleaned_up_audited_and_consumed(self):
        with _Env(extension_ok=False) as env:
            env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.copy.assert_not_called()
        assert ("asset-bucket", "temp-uploads/up-1/scan.laz") in env.deleted_keys()
        env.s3.abort_multipart_upload.assert_called_once_with(
            Bucket="asset-bucket", Key="temp-uploads/up-1/scan.laz", UploadId="real-s3-upload-id")
        env.audit.assert_called_once()
        assert env.audit.call_args.args[4] is True  # denied

    def test_blocked_external_upload_deletes_staged_object_in_source_bucket(self):
        with _Env(extension_ok=False) as env:
            env.sq.lambda_handler(_event(_external_upload_file_info()), _context())

        env.copy.assert_not_called()
        assert ("run-bucket", "temp/up-1/scan.laz") in env.deleted_keys()
        assert ("asset-bucket", "temp/up-1/scan.laz") not in env.deleted_keys()
        env.s3.abort_multipart_upload.assert_not_called()
        assert env.audit.call_args.args[4] is True

    def test_invalid_part_is_rejected_and_consumed(self):
        with _Env(complete_error=_client_error("InvalidPart", status=400)) as env:
            env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.copy.assert_not_called()
        env.s3.abort_multipart_upload.assert_called_once()
        assert env.audit.call_args.args[4] is True

    def test_no_such_upload_without_staged_object_is_consumed(self):
        with _Env(complete_error=_client_error("NoSuchUpload", status=404),
                  head_error=_client_error("404", "HeadObject", status=404)) as env:
            env.sq.lambda_handler(_event(_user_upload_file_info(), receive_count=2), _context())

        env.copy.assert_not_called()

    def test_upload_id_mismatch_deletes_staged_object(self):
        with _Env(metadata_upload_id="someone-else") as env:
            env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.copy.assert_not_called()
        assert ("asset-bucket", "temp-uploads/up-1/scan.laz") in env.deleted_keys()

    def test_rejected_preview_extension_deletes_staged_object(self):
        """A False return from validate_and_move_large_file cleans up the staged object."""
        file_info = _external_upload_file_info(relativeKey="/preview.exe", uploadType="assetPreview")
        with _Env() as env:
            env.s3.head_object.return_value = {"Metadata": {UPLOAD_ID_METADATA_KEY: "up-1"}, "ContentLength": 10}
            env.sq.lambda_handler(_event(file_info), _context())

        env.copy.assert_not_called()
        assert ("run-bucket", "temp/up-1/scan.laz") in env.deleted_keys()

    def test_validate_and_move_false_return_cleans_up_staged_object(self):
        from backend.backend.handlers.assets import sqsUploadFileLarge as sq
        with patch.object(sq, "complete_multipart_upload_for_large_file", return_value=True), \
                patch.object(sq, "validate_and_move_large_file", return_value=False), \
                patch.object(sq, "s3", MagicMock()) as mock_s3, \
                patch.object(sq, "delete_s3_object", MagicMock(return_value=True)) as mock_delete, \
                patch.object(sq, "log_file_upload", MagicMock()):
            assert sq.process_large_file(_user_upload_file_info(), {}) is False

        mock_delete.assert_called_once_with("asset-bucket", "temp-uploads/up-1/scan.laz")
        mock_s3.abort_multipart_upload.assert_called_once()


@pytest.mark.unit
class TestDuplicateDeliveryOfAStoredFile:
    """SQS delivers at least once: a second delivery of a message whose file an earlier
    receive already stored is consumed with no cleanup and no audit entry."""

    def _assert_consumed_quietly(self, env):
        env.copy.assert_not_called()
        env.delete.assert_not_called()
        env.s3.abort_multipart_upload.assert_not_called()
        env.audit.assert_not_called()

    def test_no_such_upload_with_file_already_stored_writes_no_denied_entry(self):
        file_info = _user_upload_file_info()
        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.s3.head_object.side_effect = _head_after_finish("asset-1/big/scan.laz", _upload_final_metadata())
            env.sq.lambda_handler(_event(file_info, receive_count=1, sent_timestamp_ms=SENT_MS), _context())

        self._assert_consumed_quietly(env)

    def test_duplicate_on_final_receive_is_consumed_without_abort(self):
        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.s3.head_object.side_effect = _head_after_finish("asset-1/big/scan.laz", _upload_final_metadata())
            env.sq.lambda_handler(
                _event(_user_upload_file_info(), receive_count=env.sq.LARGE_FILE_MAX_RECEIVE_COUNT,
                       sent_timestamp_ms=SENT_MS),
                _context())

        self._assert_consumed_quietly(env)

    def test_repeat_complete_accepted_with_file_already_stored_is_consumed(self):
        """S3 can accept a repeat CompleteMultipartUpload on a completed upload; the staged
        object is gone because the earlier receive moved it."""
        with _Env() as env:
            env.s3.head_object.side_effect = _head_after_finish("asset-1/big/scan.laz", _upload_final_metadata())
            env.sq.lambda_handler(_event(_user_upload_file_info(), sent_timestamp_ms=SENT_MS), _context())

        self._assert_consumed_quietly(env)

    def test_external_upload_already_stored_is_consumed(self):
        with _Env() as env:
            env.s3.head_object.side_effect = _head_after_finish("asset-1/big/scan.laz", _workflow_final_metadata())
            env.sq.lambda_handler(_event(_external_upload_file_info(), sent_timestamp_ms=SENT_MS), _context())

        self._assert_consumed_quietly(env)

    def test_final_object_from_an_earlier_upload_is_still_rejected(self):
        """An older version at the final key (an earlier upload of the same path) does not
        make a vanished upload a duplicate: the rejection is audited as before."""
        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.s3.head_object.side_effect = _head_after_finish(
                "asset-1/big/scan.laz", _upload_final_metadata(), last_modified=STORED_BEFORE_SEND)
            env.sq.lambda_handler(_event(_user_upload_file_info(), sent_timestamp_ms=SENT_MS), _context())

        env.copy.assert_not_called()
        env.audit.assert_called_once()
        assert env.audit.call_args.args[4] is True  # denied

    def test_final_object_with_other_provenance_is_still_rejected(self):
        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.s3.head_object.side_effect = _head_after_finish(
                "asset-1/big/scan.laz", _upload_final_metadata(user_id="someone-else"))
            env.sq.lambda_handler(_event(_user_upload_file_info(), sent_timestamp_ms=SENT_MS), _context())

        env.audit.assert_called_once()
        assert env.audit.call_args.args[4] is True

    def test_no_final_object_is_still_rejected(self):
        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.s3.head_object.side_effect = _head_after_finish("asset-1/big/scan.laz", None)
            env.sq.lambda_handler(_event(_user_upload_file_info(), sent_timestamp_ms=SENT_MS), _context())

        env.s3.abort_multipart_upload.assert_called_once()
        env.audit.assert_called_once()
        assert env.audit.call_args.args[4] is True

    def test_without_sent_timestamp_the_file_is_not_treated_as_stored(self):
        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.s3.head_object.side_effect = _head_after_finish("asset-1/big/scan.laz", _upload_final_metadata())
            env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.audit.assert_called_once()
        assert env.audit.call_args.args[4] is True

    def test_failed_read_of_final_object_is_retryable(self):
        def head(Bucket, Key, **_):
            if Key == "asset-1/big/scan.laz":
                raise _client_error("InternalError", "HeadObject")
            raise _client_error("404", "HeadObject", status=404)

        with _Env(complete_error=_client_error("NoSuchUpload", status=404)) as env:
            env.s3.head_object.side_effect = head
            with pytest.raises(Exception):
                env.sq.lambda_handler(_event(_user_upload_file_info(), sent_timestamp_ms=SENT_MS), _context())

        env.audit.assert_not_called()
        env.s3.abort_multipart_upload.assert_not_called()


@pytest.mark.unit
class TestSuccessIsUnchanged:
    def test_user_upload_success_returns_normally(self):
        with _Env() as env:
            env.sq.lambda_handler(_event(_user_upload_file_info()), _context())

        env.s3.complete_multipart_upload.assert_called_once()
        env.copy.assert_called_once()
        env.s3.abort_multipart_upload.assert_not_called()
        assert env.deleted_keys() == [("asset-bucket", "temp-uploads/up-1/scan.laz")]
        assert env.audit.call_args.args[4] is False

    def test_external_upload_success_returns_normally(self):
        with _Env() as env:
            env.sq.lambda_handler(_event(_external_upload_file_info()), _context())

        env.s3.complete_multipart_upload.assert_not_called()
        env.copy.assert_called_once()
        assert env.deleted_keys() == [("run-bucket", "temp/up-1/scan.laz")]

    def test_message_without_receive_count_attribute_is_processed(self):
        """A record without attributes (older test shapes) defaults to the first receive."""
        with _Env(complete_error=_client_error("InternalError")) as env:
            event = {"Records": [{"body": json.dumps({"fileInfo": _user_upload_file_info()})}]}
            with pytest.raises(Exception):
                env.sq.lambda_handler(event, _context())

        env.s3.abort_multipart_upload.assert_not_called()
