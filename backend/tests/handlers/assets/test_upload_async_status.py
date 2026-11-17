# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The outcome of an upload's files over the asynchronous threshold is user-visible (#1123).

Before, a completion whose every file "succeeded" deleted the upload record even though files over
1 GiB were still being ingested; an engine copy of an API upload over 5 GiB that then failed was
recorded only in the audit log. Now:

- completion keeps the record (processing_async) with one PENDING entry per file accepted for
  asynchronous ingestion (both completion paths; a copy job of a workflow ingestion batch reports
  through its execution and is not tracked here); a record kept this way cannot be completed again;
- the copy engine (kind upload) and the queue consumer record each file's outcome, a queue failure
  only on the last delivery; once no file is pending the upload is completed or completed_with_errors;
- GET /uploads/{uploadId} returns it, deciding Tier 1 and Tier 2 first and answering a missing,
  mismatched or refused upload with the same 404;
- a redelivered queue message whose file was already handed to the copy engine is not re-validated
  and writes no rejected audit entry (Low L4)."""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.common import uploadStatus as us  # noqa: E402
from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
DB = "db-a"
ASSET = "xasset-1"
UPLOAD = "y0b1c2d3-0000-4000-8000-000000000001"


@pytest.fixture
def uploads():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        table = ddb.create_table(
            TableName="t-uploads",
            KeySchema=[{"AttributeName": "uploadId", "KeyType": "HASH"},
                       {"AttributeName": "assetId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "uploadId", "AttributeType": "S"},
                                  {"AttributeName": "assetId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        table.put_item(Item={"uploadId": UPLOAD, "assetId": ASSET, "databaseId": DB,
                             "uploadType": "assetFile", "status": "processing", "expiresAt": 1})
        yield table


def _stored(table):
    return table.get_item(Key={"uploadId": UPLOAD, "assetId": ASSET})["Item"]


def _result(key, success=True, asynchronous=False, job=None):
    return SimpleNamespace(relativeKey=key, success=success, largeFileAsynchronousHandling=asynchronous,
                           ingestionJobId=job)


# --------------------------------------------------------------------------------------------- #
# The shared helper
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestUploadStatusHelper:
    def test_only_queued_async_files_are_tracked(self):
        results = [_result("/a.glb"), _result("/big.e57", asynchronous=True),
                   _result("/out.e57", asynchronous=True, job="E1#abc"),
                   _result("/bad.exe", success=False, asynchronous=True)]
        assert us.async_relative_keys(results) == ["/big.e57"]

    def test_all_succeeded_completes_the_upload(self, uploads):
        assert us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57", "/b.e57"], False)
        assert _stored(uploads)["status"] == us.STATUS_PROCESSING_ASYNC
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True)
        assert _stored(uploads)["status"] == us.STATUS_PROCESSING_ASYNC  # one still pending
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/b.e57", True)
        assert _stored(uploads)["status"] == us.STATUS_COMPLETED

    def test_a_failed_file_completes_the_upload_with_errors_and_names_the_reason(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", False, "The copy did not complete.")
        view = us.status_view(_stored(uploads))
        assert view["status"] == us.STATUS_COMPLETED_WITH_ERRORS
        assert view["asyncFiles"][0]["status"] == us.FILE_FAILED
        assert view["asyncFiles"][0]["error"] == "The copy did not complete."

    def test_a_synchronous_failure_is_remembered_for_the_final_status(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], True)
        us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True)
        assert _stored(uploads)["status"] == us.STATUS_COMPLETED_WITH_ERRORS

    def test_an_untracked_file_or_record_is_left_alone(self, uploads):
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", False) is False
        assert "asyncFiles" not in _stored(uploads)
        assert us.record_async_file_outcome(uploads, "other", ASSET, "/a.e57", True) is False

    def test_a_file_the_record_does_not_track_is_not_added(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/other.e57", True) is False
        stored = _stored(uploads)
        assert set(stored["asyncFiles"]) == {"/a.e57"}
        assert stored["status"] == us.STATUS_PROCESSING_ASYNC

    def test_a_dotted_relative_key_is_one_map_entry(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/dir.v2/scan.part.e57"], False)
        us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/dir.v2/scan.part.e57", True)
        assert _stored(uploads)["asyncFiles"]["/dir.v2/scan.part.e57"]["status"] == us.FILE_SUCCEEDED

    def test_writers_never_raise(self):
        broken = MagicMock()
        broken.update_item.side_effect = RuntimeError("ddb down")
        assert us.begin_async_tracking(broken, UPLOAD, ASSET, ["/a"], False) is False
        assert us.record_async_file_outcome(broken, UPLOAD, ASSET, "/a", True) is False

    def test_a_writer_asked_to_raise_raises_so_its_caller_retries(self):
        """#1123 round 3 L10: the engine and the queue consumer retry a failed outcome write."""
        broken = MagicMock()
        broken.update_item.side_effect = RuntimeError("ddb down")
        with pytest.raises(RuntimeError):
            us.record_async_file_outcome(broken, UPLOAD, ASSET, "/a", True, raise_errors=True)

    def test_an_untracked_file_does_not_raise_even_when_asked(self, uploads):
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True, raise_errors=True) is False

    def test_a_repeated_outcome_is_written_again_and_settles_once(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True)
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True)  # a retry
        assert _stored(uploads)["status"] == us.STATUS_COMPLETED

    def test_a_recorded_outcome_is_never_replaced_by_another(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", False, "The copy did not complete.")
        assert us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True) is False
        assert _stored(uploads)["asyncFiles"]["/a.e57"]["status"] == us.FILE_FAILED

    def test_the_audit_claim_is_won_once_and_only_while_pending(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        assert us.claim_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57") is True
        assert us.claim_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57") is False
        assert us.async_file_status(uploads, UPLOAD, ASSET, "/a.e57") == us.FILE_PENDING
        # The claim stamp is not part of the API view
        assert set(us.status_view(_stored(uploads))["asyncFiles"][0]) == {
            "relativeKey", "status", "error", "updatedAt"}

    def test_the_audit_claim_after_the_outcome_is_lost(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True)
        assert us.claim_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57") is False

    def test_a_released_audit_claim_can_be_claimed_again(self, uploads):
        """#1123 round 4 (L14): a receive whose audit write failed releases its claim."""
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        assert us.claim_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57") is True
        us.release_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57")
        assert "auditClaimedAt" not in _stored(uploads)["asyncFiles"]["/a.e57"]
        assert us.claim_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57") is True

    def test_releasing_never_raises_and_leaves_a_recorded_outcome_alone(self, uploads):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/a.e57"], False)
        us.claim_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57")
        us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/a.e57", True)
        us.release_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57")  # condition fails quietly
        assert _stored(uploads)["asyncFiles"]["/a.e57"]["status"] == us.FILE_SUCCEEDED
        broken = MagicMock()
        broken.update_item.side_effect = RuntimeError("ddb down")
        us.release_async_file_audit(broken, UPLOAD, ASSET, "/a.e57")

    def test_the_audit_claim_of_an_untracked_file_is_none(self, uploads):
        assert us.claim_async_file_audit(uploads, UPLOAD, ASSET, "/a.e57") is None
        assert us.async_file_status(uploads, UPLOAD, ASSET, "/a.e57") is None


# --------------------------------------------------------------------------------------------- #
# Completion keeps the record
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestCompletionKeepsTheRecord:
    def test_an_upload_with_async_files_is_kept_processing_async(self, uploads):
        with patch.object(uf, "asset_upload_table", uploads):
            uf.record_completion_status(UPLOAD, ASSET, [_result("/a.glb"), _result("/big.e57", asynchronous=True)],
                                        False)
        stored = _stored(uploads)
        assert stored["status"] == us.STATUS_PROCESSING_ASYNC
        assert stored["asyncFiles"]["/big.e57"]["status"] == us.FILE_PENDING
        assert int(stored["expiresAt"]) > 1

    def test_a_fully_synchronous_success_is_deleted_as_before(self, uploads):
        with patch.object(uf, "asset_upload_table", uploads):
            uf.record_completion_status(UPLOAD, ASSET, [_result("/a.glb")], False)
        assert "Item" not in uploads.get_item(Key={"uploadId": UPLOAD, "assetId": ASSET})

    def test_a_synchronous_failure_is_marked_as_before(self, uploads):
        with patch.object(uf, "asset_upload_table", uploads):
            uf.record_completion_status(UPLOAD, ASSET, [_result("/a.glb", success=False)], True)
        assert _stored(uploads)["status"] == "completed_with_errors"

    def test_both_completion_paths_use_it(self):
        import inspect
        assert "record_completion_status(uploadId, assetId, file_results, has_failures)" in \
            inspect.getsource(uf.complete_upload)
        # The external completion keeps its record for a repeated call (#1205) and tracks its
        # asynchronous files the same way.
        assert "record_external_completion_status(uploadId, assetId, file_results, has_failures)" in \
            inspect.getsource(uf._complete_claimed_external_upload)
        assert "uploadStatus.begin_async_tracking" in inspect.getsource(uf.record_external_completion_status)

    def test_a_record_kept_for_its_async_files_cannot_be_completed_again(self):
        details = {"uploadId": UPLOAD, "assetId": ASSET, "databaseId": DB, "uploadType": "assetFile",
                   "expiresAt": 9999999999, "asyncFiles": {"/big.e57": {"status": "PENDING"}}}
        model = uf.CompleteUploadRequestModel(**{
            "assetId": ASSET, "databaseId": DB, "uploadType": "assetFile",
            "files": [{"relativeKey": "/big.e57", "uploadIdS3": "s3", "parts": [{"PartNumber": 1, "ETag": "e"}]}]})
        with patch.object(uf, "get_upload_details", return_value=details), \
                patch.object(uf, "get_asset_details", return_value={"databaseId": DB, "assetId": ASSET}), \
                patch.object(uf, "log_file_upload"), \
                patch.object(uf, "claims_and_roles", {"tokens": ["user-1"]}, create=True), \
                patch.object(uf, "asset_upload_table") as table:
            with pytest.raises(uf.VAMSGeneralErrorResponse, match="already been completed"):
                uf.complete_upload(UPLOAD, model, {"requestContext": {"http": {}}})
        table.update_item.assert_not_called()


# --------------------------------------------------------------------------------------------- #
# GET /uploads/{uploadId}
# --------------------------------------------------------------------------------------------- #

class _Enforcer:
    api = True
    obj = True
    calls = []

    def __init__(self, claims_and_roles):
        pass

    def enforceAPI(self, event):
        return _Enforcer.api

    def enforce(self, obj, act):
        _Enforcer.calls.append((obj.get("object__type"), obj.get("databaseId"), obj.get("assetId"), act))
        return _Enforcer.obj


def _get(monkeypatch, table, asset=True, api=True, obj=True, database=DB, tokens=("user-1",)):
    _Enforcer.api, _Enforcer.obj, _Enforcer.calls = api, obj, []
    monkeypatch.setattr(uf, "CasbinEnforcer", _Enforcer)
    monkeypatch.setattr(uf, "request_to_claims", lambda event: {"tokens": list(tokens), "roles": ["r"]})
    monkeypatch.setattr(uf, "get_asset_details",
                        lambda d, a: {"databaseId": d, "assetId": a} if asset else None)
    monkeypatch.setattr(uf, "asset_upload_table", table)
    event = {"requestContext": {"http": {"path": f"/uploads/{UPLOAD}", "method": "GET"}},
             "pathParameters": {"uploadId": UPLOAD},
             "queryStringParameters": {"assetId": ASSET, "databaseId": database}, "headers": {}}
    return uf.lambda_handler(event, MagicMock())


@pytest.mark.unit
class TestUploadStatusRoute:
    def test_an_allowed_caller_reads_each_async_file(self, uploads, monkeypatch):
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/big.e57"], False)
        us.record_async_file_outcome(uploads, UPLOAD, ASSET, "/big.e57", False, "The copy did not complete.")
        response = _get(monkeypatch, uploads)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "completed_with_errors"
        assert body["asyncFiles"] == [{"relativeKey": "/big.e57", "status": "FAILED",
                                       "error": "The copy did not complete.",
                                       "updatedAt": body["asyncFiles"][0]["updatedAt"]}]
        assert _Enforcer.calls == [("asset", DB, ASSET, "GET")]

    @pytest.mark.parametrize("case", ["refused", "missing-asset", "missing-record", "other-database"])
    def test_every_negative_answer_is_the_same_404(self, uploads, monkeypatch, case):
        if case == "missing-record":
            uploads.delete_item(Key={"uploadId": UPLOAD, "assetId": ASSET})
        response = _get(monkeypatch, uploads, obj=case != "refused", asset=case != "missing-asset",
                        database="db-b" if case == "other-database" else DB)
        assert response["statusCode"] == 404
        assert json.loads(response["body"]) == {"message": "Upload not found"}

    def test_a_read_records_its_request_and_a_refusal_records_nothing(self, uploads, monkeypatch):
        # Audit contract: a read records what was asked, after it succeeded.
        us.begin_async_tracking(uploads, UPLOAD, ASSET, ["/big.e57"], False)
        recorder = MagicMock()
        monkeypatch.setattr(uf, "log_action", recorder)
        assert _get(monkeypatch, uploads)["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.args[1] == "uploadStatusGet"
        assert recorder.call_args.kwargs == {"database_id": DB, "asset_id": ASSET,
                                             "request_parameters": {"uploadId": UPLOAD}}
        recorder.reset_mock()
        assert _get(monkeypatch, uploads, obj=False)["statusCode"] == 404
        recorder.assert_not_called()

    def test_the_object_check_runs_before_the_record_is_read(self, uploads, monkeypatch):
        table = MagicMock()
        _get(monkeypatch, table, obj=False)
        table.get_item.assert_not_called()

    def test_a_route_refusal_and_no_identity_are_403(self, uploads, monkeypatch):
        assert _get(monkeypatch, uploads, api=False)["statusCode"] == 403
        assert _get(monkeypatch, uploads, tokens=())["statusCode"] == 403

    def test_ids_are_validated(self, uploads, monkeypatch):
        _Enforcer.calls = []
        monkeypatch.setattr(uf, "CasbinEnforcer", _Enforcer)
        monkeypatch.setattr(uf, "request_to_claims", lambda event: {"tokens": ["u"], "roles": ["r"]})
        event = {"requestContext": {"http": {"path": "/uploads/x", "method": "GET"}},
                 "pathParameters": {"uploadId": "bad id!"},
                 "queryStringParameters": {"assetId": ASSET, "databaseId": DB}, "headers": {}}
        assert uf.lambda_handler(event, MagicMock())["statusCode"] == 400
        assert _Enforcer.calls == []
