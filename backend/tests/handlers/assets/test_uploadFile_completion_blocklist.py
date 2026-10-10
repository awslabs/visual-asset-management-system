# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Upload completion applies the blocklist to every object it creates and accepts only the
files recorded at initialization.

``complete_upload`` has three object-creating branches: a zero-byte file (``uploadIdS3 ==
"zero-byte"``), an abandoned multipart upload (no parts, written as an empty file), and a
regular multipart completion. Only the third used to reach the blocklist, and only after the
object existed; the first two wrote an object under whatever ``relativeKey`` the request
carried. The request's keys were also never compared with the keys the upload was
initialized with, so a valid ``uploadId`` could create zero-byte objects under names that
had passed no check at all.

The blocklist's own rule (which extensions, how case is treated) lives in
``common.s3.validateUnallowedFileExtensionAndContentType`` and is pinned by that module's
tests. Here the helper is stubbed with a small predicate so these tests assert the WIRING:
completion hands the helper the submitted name and honours its verdict before any object is
created, whichever branch the file would take.
"""

import os
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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
UPLOAD_ID = "up-1"
BUCKET = "asset-bucket"
CLAIMS = {"tokens": ["alice@corp"]}

BLOCKLIST_ERROR = "File contains a potentially malicious executable type object"
NOT_INITIALIZED_ERROR = "File was not part of the initialized upload"


def _blocklist_stub(key, _content_type):
    """Stand-in for common.s3.validateUnallowedFileExtensionAndContentType.

    Rejects .exe and .bat whatever their case. The real helper owns the case rule; the stub
    answers the same way so the tests can show completion defers to it rather than re-deriving
    an extension rule of its own.
    """
    return os.path.splitext(key)[1].lower() not in {".exe", ".bat"}


def _rest_event():
    """A REST API (v1) shaped event with the no-params nulls (backend/tests/CLAUDE.md)."""
    return {
        'requestContext': {'http': {'method': 'POST', 'path': f'/uploads/{UPLOAD_ID}/complete'}},
        'pathParameters': None,
        'queryStringParameters': None,
        'headers': {'authorization': 'Bearer test-token'},
        'body': None,
    }


def _asset():
    return {
        'databaseId': DATABASE_ID,
        'assetId': ASSET_ID,
        'bucketId': 'bucket-1',
        'assetLocation': {'Key': 'asset-1/'},
    }


def _upload_record(files, uploadType="assetFile"):
    """The record get_upload_details returns. files=None models a record saved before the list."""
    record = {
        'uploadId': UPLOAD_ID,
        'assetId': ASSET_ID,
        'databaseId': DATABASE_ID,
        'uploadType': uploadType,
    }
    if files is not None:
        record['files'] = list(files)
    return record


def _zero_byte(relativeKey):
    return {"relativeKey": relativeKey, "uploadIdS3": "zero-byte", "parts": []}


def _abandoned(relativeKey, uploadIdS3="s3-upload-1"):
    return {"relativeKey": relativeKey, "uploadIdS3": uploadIdS3, "parts": []}


def _multipart(relativeKey, uploadIdS3="s3-upload-1"):
    return {"relativeKey": relativeKey, "uploadIdS3": uploadIdS3,
            "parts": [{"PartNumber": 1, "ETag": "etag-1"}]}


def _request(*files, uploadType="assetFile"):
    from backend.backend.models.assetsV3 import CompleteUploadRequestModel
    return CompleteUploadRequestModel(
        assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType=uploadType, files=list(files))


def _temp_key(relativeKey):
    """The temporary key completion writes a zero-byte object to (baseAssetsPrefix is '')."""
    return f"{uploadFile.TEMPORARY_UPLOAD_PREFIX}asset-1/{relativeKey.lstrip('/')}"


@contextmanager
def _completion_env(upload_record):
    """Yield (module, s3 mock, copy mock) with everything complete_upload touches stubbed.

    The blocklist helper is replaced by _blocklist_stub; the post-completion content check on
    the multipart branch is stubbed to pass so a rejection can only come from the name checks.
    `to_update_expr` is re-bound to the test-time `common.dynamodb` implementation because the
    handler bound the name at import, before the root conftest registered the mock module (the
    same step `test_uploadFile_asset_record_targeted_update.py` takes).
    """
    from backend.backend.handlers.assets import uploadFile as uf
    from common.dynamodb import to_update_expr

    with patch.object(uf, 'get_upload_details', return_value=upload_record), \
            patch.object(uf, 'get_asset_details', return_value=_asset()), \
            patch.object(uf, 'get_default_bucket_details', return_value={
                'bucketId': 'bucket-1', 'bucketName': BUCKET, 'baseAssetsPrefix': ''}), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': DATABASE_ID}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'asset_table', MagicMock()), \
            patch.object(uf, 'to_update_expr', to_update_expr), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 'validateUnallowedFileExtensionAndContentType',
                         MagicMock(side_effect=_blocklist_stub)), \
            patch.object(uf, 'validateS3AssetExtensionsAndContentType', return_value=True), \
            patch.object(uf, 'calculate_total_file_size_from_parts', return_value=(1024, True, None)), \
            patch.object(uf, 'copy_s3_object', return_value=True) as mock_copy, \
            patch.object(uf, 'delete_s3_object', MagicMock()), \
            patch.object(uf, 'determine_asset_type', return_value='folder'), \
            patch.object(uf, 'send_subscription_email', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3:
        mock_s3.head_object.return_value = {
            'ContentLength': 1024,
            'Metadata': {uf.UPLOAD_ID_METADATA_KEY: UPLOAD_ID},
        }
        uf.claims_and_roles = CLAIMS
        yield uf, mock_s3, mock_copy


def _complete(request_model, upload_record):
    with _completion_env(upload_record) as (uf, mock_s3, mock_copy):
        response = uf.complete_upload(UPLOAD_ID, request_model, _rest_event())
    return response, mock_s3, mock_copy


def _result(response, relativeKey):
    matches = [r for r in response.fileResults if r.relativeKey == relativeKey]
    assert len(matches) == 1, [r.relativeKey for r in response.fileResults]
    return matches[0]


@pytest.mark.unit
class TestZeroByteAndAbandonedBranchesApplyTheBlocklist:
    def test_zero_byte_blocklisted_file_is_rejected_and_creates_no_object(self):
        response, mock_s3, mock_copy = _complete(
            _request(_zero_byte("/payload.exe")), _upload_record(["/payload.exe"]))

        result = _result(response, "/payload.exe")
        assert result.success is False
        assert result.error == BLOCKLIST_ERROR
        assert result.uploadIdS3 == "zero-byte"
        assert response.overallSuccess is False
        mock_s3.put_object.assert_not_called()
        mock_copy.assert_not_called()

    def test_abandoned_blocklisted_file_is_rejected_and_creates_no_object(self):
        response, mock_s3, mock_copy = _complete(
            _request(_abandoned("/payload.bat")), _upload_record(["/payload.bat"]))

        result = _result(response, "/payload.bat")
        assert result.success is False
        assert result.error == BLOCKLIST_ERROR
        assert response.overallSuccess is False
        mock_s3.put_object.assert_not_called()
        mock_copy.assert_not_called()
        # The multipart upload the client abandoned is cleaned up, as on the other rejection paths.
        mock_s3.abort_multipart_upload.assert_called_once_with(
            Bucket=BUCKET, Key=_temp_key("/payload.bat"), UploadId="s3-upload-1")

    def test_completion_defers_the_case_rule_to_the_blocklist_helper(self):
        """The helper owns the case rule; completion hands it the submitted name unchanged."""
        with _completion_env(_upload_record(["/payload.EXE"])) as (uf, mock_s3, _):
            response = uf.complete_upload(
                UPLOAD_ID, _request(_zero_byte("/payload.EXE")), _rest_event())
            uf.validateUnallowedFileExtensionAndContentType.assert_any_call("/payload.EXE", "")

        assert _result(response, "/payload.EXE").success is False
        assert _result(response, "/payload.EXE").error == BLOCKLIST_ERROR
        mock_s3.put_object.assert_not_called()

    def test_the_rejection_echoes_no_file_name_in_its_error(self):
        response, _, _ = _complete(
            _request(_zero_byte("/payload.exe")), _upload_record(["/payload.exe"]))

        assert "payload" not in _result(response, "/payload.exe").error

    def test_zero_byte_preview_companion_with_a_non_image_extension_is_rejected(self):
        """The preview-extension rule also runs before the zero-byte branch writes anything."""
        response, mock_s3, _ = _complete(
            _request(_zero_byte("/scan.laz.previewFile.pdf")),
            _upload_record(["/scan.laz.previewFile.pdf"]))

        result = _result(response, "/scan.laz.previewFile.pdf")
        assert result.success is False
        assert ".png" in result.error
        mock_s3.put_object.assert_not_called()

    def test_multipart_blocklisted_file_is_rejected_before_the_upload_is_completed(self):
        """The regular branch gets the same early check and aborts instead of completing."""
        response, mock_s3, mock_copy = _complete(
            _request(_multipart("/payload.exe")), _upload_record(["/payload.exe"]))

        assert _result(response, "/payload.exe").success is False
        mock_s3.complete_multipart_upload.assert_not_called()
        mock_s3.abort_multipart_upload.assert_called_once_with(
            Bucket=BUCKET, Key=_temp_key("/payload.exe"), UploadId="s3-upload-1")
        mock_copy.assert_not_called()

    def test_legit_zero_byte_file_that_was_initialized_is_created(self):
        """POSITIVE CONTROL: an allowed, recorded zero-byte file is still written and moved."""
        response, mock_s3, mock_copy = _complete(
            _request(_zero_byte("/empty.txt")), _upload_record(["/empty.txt"]))

        result = _result(response, "/empty.txt")
        assert result.success is True
        assert response.overallSuccess is True
        mock_s3.put_object.assert_called_once()
        assert mock_s3.put_object.call_args.kwargs['Key'] == _temp_key("/empty.txt")
        mock_copy.assert_called_once()


@pytest.mark.unit
class TestCompletionIsBoundToTheInitializedFiles:
    def test_a_key_the_upload_was_not_initialized_with_is_rejected(self):
        response, mock_s3, mock_copy = _complete(
            _request(_zero_byte("/other.txt")), _upload_record(["/empty.txt"]))

        result = _result(response, "/other.txt")
        assert result.success is False
        assert result.error == NOT_INITIALIZED_ERROR
        assert response.overallSuccess is False
        # Nothing was created under this uploadId for that key, so S3 is not touched for it.
        mock_s3.put_object.assert_not_called()
        mock_s3.abort_multipart_upload.assert_not_called()
        mock_copy.assert_not_called()

    def test_an_unrecorded_key_is_rejected_even_when_it_is_not_blocklisted(self):
        """Membership is an allowlist: a harmless-looking name is still refused."""
        response, mock_s3, _ = _complete(
            _request(_abandoned("/notes.txt")), _upload_record(["/empty.txt"]))

        assert _result(response, "/notes.txt").error == NOT_INITIALIZED_ERROR
        mock_s3.put_object.assert_not_called()

    def test_a_mixed_request_rejects_only_the_unrecorded_file(self):
        response, mock_s3, mock_copy = _complete(
            _request(_zero_byte("/empty.txt"), _zero_byte("/other.txt")),
            _upload_record(["/empty.txt"]))

        assert _result(response, "/empty.txt").success is True
        assert _result(response, "/other.txt").success is False
        assert _result(response, "/other.txt").error == NOT_INITIALIZED_ERROR
        mock_s3.put_object.assert_called_once()
        assert mock_s3.put_object.call_args.kwargs['Key'] == _temp_key("/empty.txt")
        assert mock_copy.call_count == 1

    def test_a_record_without_the_file_list_skips_only_the_membership_check(self):
        """An upload initialized before the list was stored still completes, and the
        blocklist still applies to it; the skipped check is logged."""
        from backend.backend.handlers.assets import uploadFile as uf

        with _completion_env(_upload_record(None)) as (uf, mock_s3, mock_copy), \
                patch.object(uf, 'logger') as mock_logger:
            response = uf.complete_upload(
                UPLOAD_ID,
                _request(_zero_byte("/empty.txt"), _zero_byte("/payload.exe")),
                _rest_event())

        assert _result(response, "/empty.txt").success is True
        assert _result(response, "/payload.exe").success is False
        assert _result(response, "/payload.exe").error == BLOCKLIST_ERROR
        mock_s3.put_object.assert_called_once()
        assert mock_s3.put_object.call_args.kwargs['Key'] == _temp_key("/empty.txt")
        assert any("membership" in str(call) for call in mock_logger.warning.call_args_list)

    def test_a_record_with_the_file_list_logs_no_skipped_check(self):
        """NEGATIVE CONTROL for the warning above, asserted on the helper that emits it."""
        from backend.backend.handlers.assets import uploadFile as uf

        with patch.object(uf, 'logger') as mock_logger:
            keys = uf.get_initialized_file_keys(_upload_record(["/empty.txt"]), UPLOAD_ID)

        assert keys == {"/empty.txt"}
        mock_logger.warning.assert_not_called()


def _initialize_request(*relativeKeys, uploadType="assetFile"):
    from backend.backend.models.assetsV3 import InitializeUploadRequestModel
    return InitializeUploadRequestModel(
        assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType=uploadType,
        files=[{"relativeKey": k, "file_size": 0 if k.endswith("empty.txt") else 1024}
               for k in relativeKeys])


@pytest.mark.unit
class TestInitializationRecordsTheFiles:
    def test_the_upload_record_carries_every_initialized_relative_key(self):
        from backend.backend.handlers.assets import uploadFile as uf

        with patch.object(uf, 'check_user_rate_limit', return_value=True), \
                patch.object(uf, 'get_asset_details', return_value=_asset()), \
                patch.object(uf, 'get_database_details', return_value={'databaseId': DATABASE_ID}), \
                patch.object(uf, 'get_default_bucket_details', return_value={
                    'bucketId': 'bucket-1', 'bucketName': BUCKET, 'baseAssetsPrefix': ''}), \
                patch.object(uf, 'validateUnallowedFileExtensionAndContentType', return_value=True), \
                patch.object(uf, 'save_upload_details', MagicMock()) as mock_save, \
                patch.object(uf, 'generate_presigned_url', return_value='https://example.invalid/part'), \
                patch.object(uf, 's3', MagicMock()) as mock_s3:
            mock_s3.create_multipart_upload.return_value = {'UploadId': 's3-upload-1'}
            uf.initialize_upload(
                _initialize_request("/out/scan.laz", "/out/empty.txt"), CLAIMS)

        mock_save.assert_called_once()
        record = mock_save.call_args.args[0]
        assert record.files == ["/out/scan.laz", "/out/empty.txt"]
        # The zero-byte file is in the list although no multipart upload was created for it.
        assert record.to_dict()['files'] == ["/out/scan.laz", "/out/empty.txt"]

    def test_a_record_without_files_serializes_without_the_attribute(self):
        """External (workflow-output) records never carry a list; the attribute stays absent so
        completion can tell 'no list' from 'empty list'."""
        from backend.backend.models.assetsV3 import AssetUploadTableModel

        record = AssetUploadTableModel(
            uploadId=UPLOAD_ID, assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType="assetFile",
            createdAt="2026-01-01T00:00:00", expiresAt=1, totalFiles=1, totalParts=0)

        assert 'files' not in record.to_dict()


def _external_request(relativeKey, tempKey="temp/up-1/scan.laz", uploadType="assetFile"):
    from backend.backend.models.assetsV3 import CompleteExternalUploadRequestModel
    return CompleteExternalUploadRequestModel(
        assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType=uploadType,
        files=[{"relativeKey": relativeKey, "tempKey": tempKey}])


def _complete_external(request_model):
    from backend.backend.handlers.assets import uploadFile as uf
    from common.dynamodb import to_update_expr

    with patch.object(uf, 'get_upload_details', return_value={
                'assetId': ASSET_ID, 'databaseId': DATABASE_ID,
                'uploadType': request_model.uploadType,
                'isExternalUpload': True, 'temporaryPrefix': 'temp/up-1/'}), \
            patch.object(uf, 'get_asset_details', return_value=_asset()), \
            patch.object(uf, 'get_default_bucket_details', return_value={
                'bucketId': 'bucket-1', 'bucketName': BUCKET, 'baseAssetsPrefix': ''}), \
            patch.object(uf, 'get_database_details', return_value={'databaseId': DATABASE_ID}), \
            patch.object(uf, 'asset_upload_table', MagicMock()), \
            patch.object(uf, 'asset_table', MagicMock()), \
            patch.object(uf, 'to_update_expr', to_update_expr), \
            patch.object(uf, 'delete_upload_details', MagicMock()), \
            patch.object(uf, 'validateUnallowedFileExtensionAndContentType',
                         MagicMock(side_effect=_blocklist_stub)), \
            patch.object(uf, 'validateS3AssetExtensionsAndContentType', return_value=True), \
            patch.object(uf, 'copy_s3_object', return_value=True) as mock_copy, \
            patch.object(uf, 'delete_s3_object', MagicMock()), \
            patch.object(uf, 'determine_asset_type', return_value='folder'), \
            patch.object(uf, 'send_subscription_email', MagicMock()), \
            patch.object(uf, 's3', MagicMock()) as mock_s3:
        mock_s3.head_object.return_value = {'ContentLength': 1024}
        uf.claims_and_roles = CLAIMS
        response = uf.complete_external_upload(UPLOAD_ID, request_model, _rest_event())
    return response, mock_copy


@pytest.mark.unit
class TestExternalCompletionAppliesTheBlocklistToTheDestinationName:
    """The external path's content check inspects the staged tempKey object; the object it
    creates is named after relativeKey, which was never checked."""

    def test_a_blocklisted_destination_name_is_rejected_and_nothing_is_copied(self):
        response, mock_copy = _complete_external(_external_request("/payload.exe"))

        result = _result(response, "/payload.exe")
        assert result.success is False
        assert result.error == BLOCKLIST_ERROR
        assert result.uploadIdS3 == "external"
        mock_copy.assert_not_called()

    def test_an_allowed_destination_name_is_still_copied(self):
        """POSITIVE CONTROL."""
        response, mock_copy = _complete_external(_external_request("/out/scan.laz"))

        assert _result(response, "/out/scan.laz").success is True
        mock_copy.assert_called_once()
