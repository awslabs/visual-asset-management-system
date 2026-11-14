# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Upload initialize and complete decide the object check before asset existence (issue #959).

These routes already answered an object-level refusal with 403, but they looked the asset up first
and answered a missing one with 400 "Asset not found" to any caller, so a caller refused the asset
could tell a missing asset id from an existing one. The object check now runs first, through the
request's identifiers for a missing asset, as on the asset GET: a refused caller gets the same 403
for both. A permitted caller asking for a missing asset keeps the 400 "Asset not found".
"""

import json
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile as _uploadFile  # noqa: E402

DATABASE_ID = "db-a"
ASSET_ID = "xasset-1"
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["database-user"], "mfaEnabled": False}
STORED_ASSET = {"databaseId": DATABASE_ID, "assetId": ASSET_ID, "assetName": "asset one", "bucketId": "bucket-1"}
REQUEST = {"assetId": ASSET_ID, "databaseId": DATABASE_ID, "uploadType": "assetFile"}

# (route id, path, files, the function that does the work after the check)
ROUTES = [
    ("initialize", "/uploads", [{"relativeKey": "/model.glb", "file_size": 2048}], "initialize_upload"),
    (
        "complete",
        "/uploads/upload-1/complete",
        [{"relativeKey": "/model.glb", "uploadIdS3": "s3-upload-1", "parts": [{"PartNumber": 1, "ETag": "etag-1"}]}],
        "complete_upload",
    ),
    (
        "complete_external",
        "/uploads/upload-1/complete/external",
        [{"relativeKey": "/model.glb", "tempKey": "temporary-upload/upload-1/model.glb"}],
        "complete_external_upload",
    ),
]
ROUTE_IDS = [route[0] for route in ROUTES]


class _PastTheGate(Exception):
    """Raised by the work after the object check, so the test needs no downstream fakes."""


class _Enforcer:
    object_verdict = True
    object_calls = []

    def __init__(self, claims_and_roles):
        pass

    def enforceAPI(self, event):
        return True

    def enforce(self, obj, act):
        _Enforcer.object_calls.append((obj.get("object__type"), obj.get("databaseId"), obj.get("assetId"), act))
        return _Enforcer.object_verdict


@pytest.fixture
def route(request, monkeypatch):
    route_id, path, files, work = request.param
    _Enforcer.object_verdict = True
    _Enforcer.object_calls = []
    monkeypatch.setattr(_uploadFile, "CasbinEnforcer", _Enforcer)
    monkeypatch.setattr(_uploadFile, "request_to_claims", lambda event: AUTHENTICATED)
    reached = []

    def past_the_gate(*args, **kwargs):
        reached.append(args)
        raise _PastTheGate()

    monkeypatch.setattr(_uploadFile, work, past_the_gate)
    event = {
        "body": json.dumps({**REQUEST, "files": files}),
        "requestContext": {"http": {"path": path, "method": "POST"}},
        "pathParameters": {"uploadId": "upload-1"} if route_id != "initialize" else None,
        "headers": {},
    }

    def call(stored_asset):
        # The record is handed over as is, so a test can see whether the handler annotated it
        monkeypatch.setattr(_uploadFile, "get_asset_details", lambda databaseId, assetId: stored_asset)
        try:
            return _uploadFile.lambda_handler(json.loads(json.dumps(event)), MagicMock())
        except _PastTheGate:
            return None

    return call, reached


@pytest.mark.unit
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS, indirect=True)
class TestUploadDeniedBeforeExistence:
    def test_authorized_caller_passes_the_object_check(self, route):
        call, reached = route

        call(dict(STORED_ASSET))

        assert len(reached) == 1
        assert ("asset", DATABASE_ID, ASSET_ID, "POST") in _Enforcer.object_calls

    def test_refused_existing_asset_answers_403(self, route):
        call, reached = route
        _Enforcer.object_verdict = False

        response = call(dict(STORED_ASSET))

        assert response["statusCode"] == 403
        assert reached == []

    def test_refused_missing_asset_answers_exactly_as_a_refused_existing_one(self, route):
        call, reached = route
        _Enforcer.object_verdict = False

        existing = call(dict(STORED_ASSET))
        missing = call(None)

        assert missing["statusCode"] == existing["statusCode"] == 403
        assert missing["body"] == existing["body"]
        assert reached == []
        assert ("asset", DATABASE_ID, ASSET_ID, "POST") in _Enforcer.object_calls

    def test_missing_asset_keeps_its_not_found_answer_for_a_permitted_caller(self, route):
        call, reached = route

        response = call(None)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == "Asset not found"
        assert reached == []

    def test_the_stored_record_is_not_annotated(self, route):
        call, _ = route
        record = dict(STORED_ASSET)

        call(record)

        assert "object__type" not in record
