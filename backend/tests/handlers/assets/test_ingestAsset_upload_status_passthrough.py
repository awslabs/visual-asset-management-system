# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /ingest-asset returns uploadFile's 403, 404 and 429 to the caller unchanged.

ingest-asset invokes uploadFile for both stages. A rate-limited initialization comes back from
uploadFile as a 429 with Retry-After, and a refused or missing target as 403 or 404; the caller
gets that response, headers included. Any other non-200 from uploadFile is still a 400.
"""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("CREATE_ASSET_LAMBDA_FUNCTION_NAME", "test-create-asset")
os.environ.setdefault("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "test-file-upload")

from backend.backend.handlers.assets import ingestAsset  # noqa: F401,E402

RATE_LIMITED = {
    "statusCode": 429,
    "headers": {"Content-Type": "application/json", "Retry-After": "60"},
    "body": json.dumps({"message": "VAMS General Error: Rate limit exceeded. Maximum 20 upload "
                                   "initializations per user per minute allowed."}),
}


def _event(complete=False):
    body = {
        "databaseId": "db-one",
        "assetId": "asset-1",
        "assetName": "Asset 1",
        "description": "an asset",
        "isDistributable": True,
        "tags": [],
    }
    if complete:
        body["uploadId"] = "up-1"
        body["files"] = [{"relativeKey": "asset-1/a.glb", "uploadIdS3": "s3-up-1",
                          "parts": [{"PartNumber": 1, "ETag": "e1"}]}]
    else:
        body["files"] = [{"relativeKey": "asset-1/a.glb", "file_size": 1024}]
    return {
        "requestContext": {"http": {"method": "POST", "path": "/ingest-asset"},
                           "authorizer": {"jwt": {"claims": {"sub": "u1"}}}},
        "pathParameters": None,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body),
    }


def _run(upload_response, complete=False):
    from backend.backend.handlers.assets import ingestAsset as ia

    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    enforcer.return_value.enforceAPI.return_value = True
    with patch.object(ia, "request_to_claims", return_value={"tokens": ["u1"], "roles": []}), \
            patch.object(ia, "CasbinEnforcer", enforcer), \
            patch.object(ia, "verify_database_exists", return_value=True), \
            patch.object(ia, "verify_asset_exists", return_value=True), \
            patch.object(ia, "invoke_lambda", return_value=upload_response):
        return ia.lambda_handler(_event(complete), None)


@pytest.mark.unit
@pytest.mark.parametrize("complete", [False, True], ids=["initialize", "complete"])
class TestUploadStatusesPassThrough:
    def test_a_rate_limited_upload_returns_429_with_retry_after(self, complete):
        response = _run(RATE_LIMITED, complete)

        assert response["statusCode"] == 429
        assert response["headers"]["Retry-After"] == "60"
        assert response["body"] == RATE_LIMITED["body"]

    @pytest.mark.parametrize("status", [403, 404])
    def test_a_refused_or_missing_target_keeps_its_status(self, complete, status):
        inner = {"statusCode": status, "headers": {}, "body": json.dumps({"message": "x"})}
        response = _run(inner, complete)

        assert response["statusCode"] == status
        assert response["body"] == inner["body"]

    def test_any_other_upload_failure_is_still_a_400(self, complete):
        inner = {"statusCode": 500, "headers": {}, "body": json.dumps({"message": "Internal Server Error"})}
        response = _run(inner, complete)

        assert response["statusCode"] == 400
