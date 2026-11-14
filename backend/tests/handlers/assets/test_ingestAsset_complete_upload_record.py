# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The ingest-asset complete stage creates a missing asset only for an upload it can complete.

When the asset of a complete call does not exist, ingest-asset creates it before it forwards the
completion to uploadFile. A complete call whose upload record cannot be completed (no record, for
an unknown or an already completed upload; a record of another database; another upload type;
expired; a workflow-output record) must be refused before that, so it leaves no new, empty asset
behind. These tests run ingest-asset's real lambda_handler with its Lambda invocations answered by a
recording createAsset stand-in and by the real uploadFile lambda_handler, over one in-memory upload
table both handlers read.
"""

import json
import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("CREATE_ASSET_LAMBDA_FUNCTION_NAME", "test-create-asset")
os.environ.setdefault("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "test-file-upload")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import ingestAsset as _ingestAsset  # noqa: E402
from backend.backend.handlers.assets import uploadFile as _uploadFile  # noqa: E402

DATABASE_ID = "db-one"
ASSET_ID = "xasset-new"
UPLOAD_ID = "y0b6f1c2e-1111-2222-3333-444455556666"
RELATIVE_KEYS = ["/a.glb", "/b.bin"]
CLAIMS = {"tokens": ["u1"], "roles": ["asset-writer"], "mfaEnabled": False}
FRESH = int(time.time()) + 7 * 24 * 3600
EXPIRED = int(time.time()) - 60


def _record(**overrides):
    """An upload record as initialization writes it for this asset."""
    record = {"uploadId": UPLOAD_ID, "assetId": ASSET_ID, "databaseId": DATABASE_ID,
              "uploadType": "assetFile", "status": "initialized", "expiresAt": FRESH,
              "isExternalUpload": False, "files": list(RELATIVE_KEYS)}
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not None}


# Each refused record and the message the complete call answers with. A completed upload's record
# is deleted by the completion, so an already completed upload id finds no record, as an unknown one.
REFUSED = [
    pytest.param(None, "Upload record not found", id="unknown-or-completed"),
    pytest.param(_record(databaseId="db-other"), "Upload details do not match request", id="other-database"),
    pytest.param(_record(uploadType="assetPreview"), "Upload type mismatch.", id="other-upload-type"),
    pytest.param(_record(expiresAt=EXPIRED), "Upload has expired", id="expired"),
    pytest.param(_record(expiresAt=None), "Upload has expired", id="no-expiry"),
    pytest.param(_record(isExternalUpload=True), "Upload type mismatch.", id="workflow-output"),
]

# The refusals the upload completion records in the file-upload audit log for every submitted file.
AUDITED = ("expired", "no-expiry", "workflow-output")


class _UploadTable:
    """The asset uploads table: get_item on the (uploadId, assetId) key."""

    def __init__(self, record):
        self.records = {}
        if record is not None:
            self.records[(record["uploadId"], record["assetId"])] = record

    def get_item(self, Key):
        item = self.records.get((Key["uploadId"], Key["assetId"]))
        return {"Item": dict(item)} if item is not None else {}


class _Allow:
    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        return True

    def enforceAPI(self, event):
        return True


def _complete_event():
    body = {
        "databaseId": DATABASE_ID,
        "assetId": ASSET_ID,
        "assetName": "New asset",
        "description": "an asset",
        "isDistributable": True,
        "tags": [],
        "uploadId": UPLOAD_ID,
        "files": [{"relativeKey": key, "uploadIdS3": f"s3-up-{i}", "parts": [{"PartNumber": 1, "ETag": "e1"}]}
                  for i, key in enumerate(RELATIVE_KEYS)],
    }
    return {
        "requestContext": {"http": {"method": "POST", "path": "/ingest-asset"},
                           "authorizer": {"jwt": {"claims": {"sub": "u1"}}}},
        "pathParameters": None,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body),
    }


@pytest.fixture
def ingest(monkeypatch):
    """ingest-asset over an asset store, an upload table and the real uploadFile dispatch."""
    state = SimpleNamespace(assets={}, invocations=[], completions=[], denials=[], upload_denials=[],
                            table=_UploadTable(None))

    # uploadFile: dispatch, authorization and the record checks of complete_upload run as written.
    monkeypatch.setattr(_uploadFile, "CasbinEnforcer", _Allow)
    monkeypatch.setattr(_uploadFile, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(_uploadFile, "get_asset_details",
                        lambda database_id, asset_id: state.assets.get((database_id, asset_id)))
    monkeypatch.setattr(_uploadFile, "asset_upload_table", state.table)
    monkeypatch.setattr(_uploadFile, "log_file_upload",
                        lambda event, db, asset, path, denied, reason=None, data=None: state.upload_denials.append(
                            (asset, path, denied, reason)))

    def invoke_lambda(function_name, payload, invocation_type="RequestResponse"):
        state.invocations.append(function_name)
        payload = json.loads(json.dumps(payload))  # as lambda_client.invoke carries it
        if function_name == os.environ["CREATE_ASSET_LAMBDA_FUNCTION_NAME"]:
            body = payload["body"]
            state.assets[(body["databaseId"], body["assetId"])] = {
                "databaseId": body["databaseId"], "assetId": body["assetId"], "bucketId": "bucket-1"}
            return {"statusCode": 200, "body": json.dumps({"message": "Succeeded"})}
        assert function_name == os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]
        return _uploadFile.lambda_handler(payload, MagicMock())

    monkeypatch.setattr(_ingestAsset, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(_ingestAsset, "CasbinEnforcer", _Allow)
    monkeypatch.setattr(_ingestAsset, "verify_database_exists", lambda database_id: True)
    monkeypatch.setattr(_ingestAsset, "verify_asset_exists",
                        lambda database_id, asset_id: (database_id, asset_id) in state.assets)
    monkeypatch.setattr(_ingestAsset, "invoke_lambda", invoke_lambda)
    monkeypatch.setattr(_ingestAsset, "dynamodb", SimpleNamespace(Table=lambda name: state.table))
    # raising=False: the regression proof runs this file against the handler before it audited here.
    monkeypatch.setattr(_ingestAsset, "log_file_upload",
                        lambda event, db, asset, path, denied, reason, data: state.denials.append(
                            (asset, path, denied, reason)), raising=False)

    def use_record(record):
        state.table.records.clear()
        if record is not None:
            state.table.records[(record["uploadId"], record["assetId"])] = record

    def run():
        return _ingestAsset.lambda_handler(_complete_event(), MagicMock())

    state.use_record = use_record
    state.run = run
    return state


def _message(response):
    return json.loads(response["body"])["message"]


def _is_completion_refusal(message, reason):
    """The ingest answer for a refused completion: it names the completion and ends with the reason.
    (The answer of the forwarded completion carries one more general-error prefix before the reason.)"""
    return message.startswith("VAMS General Error: Error completing upload: ") and message.endswith(f": {reason}")


@pytest.mark.unit
class TestCompleteStageChecksTheUploadBeforeCreatingTheAsset:
    @pytest.mark.parametrize("record,reason", REFUSED)
    def test_a_refused_upload_creates_no_asset(self, ingest, record, reason):
        ingest.use_record(record)

        response = ingest.run()

        assert response["statusCode"] == 400, response["body"]
        assert _message(response) == f"VAMS General Error: Error completing upload: {reason}"
        assert ingest.assets == {}
        assert ingest.invocations == []

    @pytest.mark.parametrize("record,reason", REFUSED)
    def test_the_refusal_is_the_one_the_upload_completion_gives_for_an_existing_asset(
            self, ingest, record, reason, request):
        ingest.use_record(record)
        ingest.assets[(DATABASE_ID, ASSET_ID)] = {
            "databaseId": DATABASE_ID, "assetId": ASSET_ID, "bucketId": "bucket-1"}

        response = ingest.run()

        assert response["statusCode"] == 400, response["body"]
        assert _is_completion_refusal(_message(response), reason), _message(response)
        assert ingest.invocations == [os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]]
        # The audit entries the ingest-side refusal writes are the ones the completion writes.
        audited = request.node.callspec.id in AUDITED
        expected_denials = [(ASSET_ID, key, True, reason) for key in RELATIVE_KEYS] if audited else []
        assert ingest.upload_denials == expected_denials

    @pytest.mark.parametrize("record,reason", [p for p in REFUSED if p.id in AUDITED])
    def test_a_refusal_the_completion_audits_is_audited_per_file(self, ingest, record, reason):
        ingest.use_record(record)

        ingest.run()

        assert ingest.denials == [(ASSET_ID, key, True, reason) for key in RELATIVE_KEYS]

    @pytest.mark.parametrize("record,reason", [p for p in REFUSED if p.id not in AUDITED])
    def test_other_refusals_write_no_file_upload_denial(self, ingest, record, reason):
        ingest.use_record(record)

        ingest.run()

        assert ingest.denials == []

    def test_a_completable_upload_creates_the_asset_then_completes(self, ingest, monkeypatch):
        ingest.use_record(_record())

        def complete_upload(upload_id, request_model, event):
            ingest.completions.append((upload_id, request_model.assetId))
            return {"statusCode": 200, "headers": {},
                    "body": json.dumps({"fileResults": [{"relativeKey": key, "uploadIdS3": "s3", "success": True}
                                                        for key in RELATIVE_KEYS],
                                        "overallSuccess": True})}

        monkeypatch.setattr(_uploadFile, "complete_upload", complete_upload)

        response = ingest.run()

        assert response["statusCode"] == 200, response["body"]
        assert ingest.invocations == [os.environ["CREATE_ASSET_LAMBDA_FUNCTION_NAME"],
                                      os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]]
        assert (DATABASE_ID, ASSET_ID) in ingest.assets
        assert ingest.completions == [(UPLOAD_ID, ASSET_ID)]
        assert ingest.denials == []

    def test_an_unreadable_upload_table_creates_no_asset(self, ingest, monkeypatch):
        def failing_get_item(Key):
            raise RuntimeError("table unavailable")

        monkeypatch.setattr(ingest.table, "get_item", failing_get_item)

        response = ingest.run()

        assert response["statusCode"] == 400, response["body"]
        assert _message(response) == "VAMS General Error: Error verifying upload."
        assert ingest.assets == {}
        assert ingest.invocations == []
