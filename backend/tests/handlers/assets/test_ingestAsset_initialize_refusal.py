# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The ingest-asset initialize stage creates a missing asset only for files the upload step accepts.

When the asset of an initialize call does not exist, ingest-asset creates it before it forwards the
request to uploadFile. A request the upload step refuses for the file names (a blocked extension, an
extension the database does not allow, a preview file with a disallowed extension) must be refused
before that, so it leaves no new, empty asset behind. These tests run ingest-asset's real
lambda_handler with its Lambda invocations answered by a recording createAsset stand-in and by the
real uploadFile lambda_handler, whose own checks run as written.
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
ASSET_ID = "xasset-new"
CLAIMS = {"tokens": ["u1"], "roles": ["asset-writer"], "mfaEnabled": False}
BLOCKED_EXTENSIONS = (".exe",)


def _blocked_extension_check(key_path, content_type):
    """The blocklist of common.s3 for the cases below (the test mock of common.s3 allows everything)."""
    return not key_path.lower().endswith(BLOCKED_EXTENSIONS)


def _event(*file_names):
    body = {
        "databaseId": DATABASE_ID, "assetId": ASSET_ID, "assetName": "New asset",
        "description": "an asset", "isDistributable": True, "tags": [],
        "files": [{"relativeKey": f"{ASSET_ID}/{name}", "file_size": 1024} for name in file_names],
    }
    return {
        "requestContext": {"http": {"method": "POST", "path": "/ingest-asset"},
                           "authorizer": {"jwt": {"claims": {"sub": "u1"}}}},
        "pathParameters": None, "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body),
    }


class _Allow:
    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        return True

    def enforceAPI(self, event):
        return True


@pytest.fixture
def ingest(monkeypatch):
    state = SimpleNamespace(assets={}, invocations=[], database={"databaseId": DATABASE_ID})

    monkeypatch.setattr(_uploadFile, "CasbinEnforcer", _Allow)
    monkeypatch.setattr(_uploadFile, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(_uploadFile, "get_asset_details",
                        lambda database_id, asset_id: state.assets.get((database_id, asset_id)))
    monkeypatch.setattr(_uploadFile, "get_database_details", lambda database_id: state.database)
    monkeypatch.setattr(_uploadFile, "check_user_rate_limit", lambda user_id: True)
    monkeypatch.setattr(_uploadFile, "get_default_bucket_details",
                        lambda bucket_id: {"bucketName": "b", "baseAssetsPrefix": ""})
    monkeypatch.setattr(_uploadFile, "validateUnallowedFileExtensionAndContentType", _blocked_extension_check)
    monkeypatch.setattr(_uploadFile, "save_upload_details", lambda record: None)
    monkeypatch.setattr(_uploadFile, "generate_presigned_url", lambda *args, **kwargs: "https://s3.example/part")
    monkeypatch.setattr(_uploadFile, "s3", MagicMock(create_multipart_upload=lambda **kw: {"UploadId": "s3-up"}))
    monkeypatch.setattr(_uploadFile, "log_file_upload", lambda *args, **kwargs: None)

    def invoke_lambda(function_name, payload, invocation_type="RequestResponse"):
        state.invocations.append(function_name)
        payload = json.loads(json.dumps(payload))
        if function_name == os.environ["CREATE_ASSET_LAMBDA_FUNCTION_NAME"]:
            body = payload["body"]
            state.assets[(body["databaseId"], body["assetId"])] = {
                "databaseId": body["databaseId"], "assetId": body["assetId"], "bucketId": "bucket-1"}
            return {"statusCode": 200, "body": json.dumps({"message": "Succeeded"})}
        return _uploadFile.lambda_handler(payload, MagicMock())

    monkeypatch.setattr(_ingestAsset, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(_ingestAsset, "CasbinEnforcer", _Allow)
    monkeypatch.setattr(_ingestAsset, "verify_database_exists", lambda database_id: state.database)
    monkeypatch.setattr(_ingestAsset, "verify_asset_exists",
                        lambda database_id, asset_id: (database_id, asset_id) in state.assets)
    # raising=False: the regression proof runs this file against the handler before it checked names.
    monkeypatch.setattr(_ingestAsset, "validateUnallowedFileExtensionAndContentType", _blocked_extension_check,
                        raising=False)
    monkeypatch.setattr(_ingestAsset, "invoke_lambda", invoke_lambda)

    state.run = lambda *names: _ingestAsset.lambda_handler(_event(*names), MagicMock())
    return state


def _message(response):
    return json.loads(response["body"])["message"]


# (files, database restriction, text the refusal carries)
REFUSED = [
    pytest.param(["tool.exe"], "", "Files contain an unsupported file extension", id="blocked-extension"),
    pytest.param(["ok.glb", "tool.exe"], "", "Files contain an unsupported file extension", id="blocked-second-file"),
    pytest.param(["a.txt"], ".glb,.png", "Database does not allow this file extension. Allowed extensions: .glb, .png",
                 id="database-restriction"),
    pytest.param(["a.glb.previewFile.exe2"], "", "Preview files must have one of the allowed extensions",
                 id="preview-extension"),
]


@pytest.mark.unit
class TestInitializeStageChecksTheFilesBeforeCreatingTheAsset:
    @pytest.mark.parametrize("files,restriction,reason", REFUSED)
    def test_a_refused_initialize_creates_no_asset(self, ingest, files, restriction, reason):
        ingest.database = {"databaseId": DATABASE_ID, "restrictFileUploadsToExtensions": restriction}

        response = ingest.run(*files)

        assert response["statusCode"] == 400, response["body"]
        assert reason in _message(response), _message(response)
        assert ingest.assets == {}
        assert ingest.invocations == []

    @pytest.mark.parametrize("files,restriction,reason", REFUSED)
    def test_the_refusal_is_the_one_the_upload_step_gives_for_an_existing_asset(
            self, ingest, files, restriction, reason):
        ingest.database = {"databaseId": DATABASE_ID, "restrictFileUploadsToExtensions": restriction}
        ingest.assets[(DATABASE_ID, ASSET_ID)] = {"databaseId": DATABASE_ID, "assetId": ASSET_ID,
                                                   "bucketId": "bucket-1"}

        response = ingest.run(*files)

        assert response["statusCode"] == 400, response["body"]
        assert reason in _message(response), _message(response)
        assert ingest.invocations == [os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]]

    def test_an_acceptable_initialize_creates_the_asset_then_starts_the_upload(self, ingest):
        ingest.database = {"databaseId": DATABASE_ID, "restrictFileUploadsToExtensions": ".glb"}

        response = ingest.run("model.glb", "model.glb.previewFile.png")

        assert response["statusCode"] == 200, response["body"]
        assert ingest.invocations == [os.environ["CREATE_ASSET_LAMBDA_FUNCTION_NAME"],
                                      os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]]
        assert (DATABASE_ID, ASSET_ID) in ingest.assets
        assert json.loads(response["body"])["uploadId"].startswith("y")
