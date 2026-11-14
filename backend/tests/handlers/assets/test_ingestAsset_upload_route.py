# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /ingest-asset forwards each stage to uploadFile on a route uploadFile dispatches.

ingest-asset invokes the uploadFile Lambda with an API Gateway-shaped event. uploadFile picks the
operation by matching ``requestContext.http.path`` against the ApiRoute constants in
``common/apiRoutes.py``, so the
forwarded path must be the real route with its parameters filled in: ``/uploads`` to initialize and
``/uploads/{uploadId}/complete`` to complete. These tests hand the event ingest-asset builds to the
real uploadFile ``lambda_handler``, with only the work after the dispatch and the checks replaced,
so a path uploadFile does not route answers "Invalid API path or method" and fails the test.
"""

import json
import os
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
ASSET_ID = "xasset-1"
RELATIVE_KEY = f"{ASSET_ID}/a.glb"
UPLOAD_ID = "y0b6f1c2e-1111-2222-3333-444455556666"
CLAIMS = {"tokens": ["u1"], "roles": ["asset-writer"], "mfaEnabled": False}
STORED_ASSET = {"databaseId": DATABASE_ID, "assetId": ASSET_ID, "assetName": "Asset 1", "bucketId": "bucket-1"}


def _ingest_event(complete, upload_id=UPLOAD_ID):
    body = {
        "databaseId": DATABASE_ID,
        "assetId": ASSET_ID,
        "assetName": "Asset 1",
        "description": "an asset",
        "isDistributable": True,
        "tags": [],
    }
    if complete:
        body["uploadId"] = upload_id
        body["files"] = [{"relativeKey": RELATIVE_KEY, "uploadIdS3": "s3-up-1",
                          "parts": [{"PartNumber": 1, "ETag": "e1"}]}]
    else:
        body["files"] = [{"relativeKey": RELATIVE_KEY, "file_size": 1024}]
    return {
        "requestContext": {"http": {"method": "POST", "path": "/ingest-asset"},
                           "authorizer": {"jwt": {"claims": {"sub": "u1"}}}},
        "pathParameters": None,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body),
    }


class _UploadEnforcer:
    """uploadFile's Casbin checks; records the route path the API check was asked about."""

    api_paths = []

    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        return True

    def enforceAPI(self, event):
        _UploadEnforcer.api_paths.append(
            (event["requestContext"]["http"]["method"], event["requestContext"]["http"]["path"]))
        return True


@pytest.fixture
def forwarded(monkeypatch):
    """Run ingest-asset with its uploadFile invocation answered by the real uploadFile handler."""
    _UploadEnforcer.api_paths = []
    work = []

    # uploadFile: the dispatch and its checks run as written; only the work after them is recorded.
    monkeypatch.setattr(_uploadFile, "CasbinEnforcer", _UploadEnforcer)
    monkeypatch.setattr(_uploadFile, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(_uploadFile, "get_asset_details", lambda database_id, asset_id: dict(STORED_ASSET))
    monkeypatch.setattr(_uploadFile, "log_file_upload", lambda *args, **kwargs: None)

    def initialize_upload(request_model, claims_and_roles):
        work.append(("initialize", None, request_model))
        return SimpleNamespace(uploadId=UPLOAD_ID, dict=lambda: {"uploadId": UPLOAD_ID, "files": []})

    def complete_upload(upload_id, request_model, event):
        work.append(("complete", upload_id, request_model))
        return {"statusCode": 200, "headers": {},
                "body": json.dumps({"fileResults": [{"relativeKey": RELATIVE_KEY, "uploadIdS3": "s3-up-1",
                                                     "success": True}],
                                    "overallSuccess": True})}

    monkeypatch.setattr(_uploadFile, "initialize_upload", initialize_upload)
    monkeypatch.setattr(_uploadFile, "complete_upload", complete_upload)

    invocations = []

    def invoke_lambda(function_name, payload, invocation_type="RequestResponse"):
        invocations.append(function_name)
        assert function_name == os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]
        # The invocation carries the payload as JSON, as lambda_client.invoke does.
        return _uploadFile.lambda_handler(json.loads(json.dumps(payload)), MagicMock())

    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    enforcer.return_value.enforceAPI.return_value = True
    monkeypatch.setattr(_ingestAsset, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(_ingestAsset, "CasbinEnforcer", enforcer)
    monkeypatch.setattr(_ingestAsset, "verify_database_exists", lambda database_id: {})
    monkeypatch.setattr(_ingestAsset, "verify_asset_exists", lambda database_id, asset_id: True)
    monkeypatch.setattr(_ingestAsset, "invoke_lambda", invoke_lambda)

    def run(complete, upload_id=UPLOAD_ID):
        return _ingestAsset.lambda_handler(_ingest_event(complete, upload_id), MagicMock())

    return SimpleNamespace(run=run, work=work, invocations=invocations)


@pytest.mark.unit
class TestIngestForwardsOnARoutedPath:
    def test_complete_stage_reaches_upload_completion(self, forwarded):
        response = forwarded.run(complete=True)

        assert response["statusCode"] == 200, response["body"]
        body = json.loads(response["body"])
        assert body["uploadId"] == UPLOAD_ID
        assert body["overallSuccess"] is True
        assert [(stage, upload_id) for stage, upload_id, _ in forwarded.work] == [("complete", UPLOAD_ID)]
        assert forwarded.work[0][2].databaseId == DATABASE_ID
        assert forwarded.work[0][2].assetId == ASSET_ID

    def test_complete_stage_is_route_checked_on_the_completion_route(self, forwarded):
        forwarded.run(complete=True)

        assert _UploadEnforcer.api_paths == [("POST", f"/uploads/{UPLOAD_ID}/complete")]

    def test_initialize_stage_reaches_upload_initialization(self, forwarded):
        response = forwarded.run(complete=False)

        assert response["statusCode"] == 200, response["body"]
        assert [stage for stage, _, _ in forwarded.work] == ["initialize"]
        assert _UploadEnforcer.api_paths == [("POST", "/uploads")]

    @pytest.mark.parametrize("upload_id", ["a/b", "x/complete/external", ""], ids=["slash", "external-suffix", "empty"])
    def test_an_upload_id_that_is_not_one_path_segment_is_refused_before_any_call(self, forwarded, upload_id):
        response = forwarded.run(complete=True, upload_id=upload_id)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == "Invalid uploadId"
        assert forwarded.invocations == []
        assert forwarded.work == []
