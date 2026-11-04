# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Upload initialization refuses a request whose response would be too large, before any S3 call.

The initialize response carries one presigned URL per part, and each URL carries its file's
temporary S3 key percent-encoded. A request inside the total-parts cap can therefore still produce
a response over the Lambda response limit when its keys are long and non-ASCII. initialize_upload
estimates the response in its pre-loop validation block and answers a generic 400 that does not
echo the keys, so no multipart upload is created for any file of a refused batch. A request of
the same part count with ordinary keys still initializes.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.assets import test_uploadFile_preview_extension_init as upload_init

MESSAGE = "Upload request too large; split the files across several requests"

DATABASE_ID = "db-001"
ASSET_ID = "asset1"
CLAIMS = {"tokens": ["alice@corp"], "roles": []}
ASSET_BASE_KEY = "0b7f3c52-9d0e-4f6a-8a51-2c4e7d9b1a63/"

SHORT_KEY = "/scans/pump.glb"
# About 610 UTF-8 bytes of 2-byte characters, inside the S3 key limit once resolved. Each part's
# URL carries it percent-encoded, about 1,830 bytes.
LONG_NON_ASCII_KEY = "/scans/" + ("\u00e9" * 300) + ".glb"


def _asset():
    return {
        "databaseId": DATABASE_ID,
        "assetId": ASSET_ID,
        "bucketId": "bucket-1",
        "assetLocation": {"Key": ASSET_BASE_KEY},
    }


def _files(*key_parts):
    return [{"relativeKey": key, "num_parts": parts} for key, parts in key_parts]


def _request(*key_parts):
    from backend.backend.models.assetsV3 import InitializeUploadRequestModel
    return InitializeUploadRequestModel(
        assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType="assetFile", files=_files(*key_parts))


def _event(*key_parts):
    return {
        "body": json.dumps({
            "assetId": ASSET_ID,
            "databaseId": DATABASE_ID,
            "uploadType": "assetFile",
            "files": _files(*key_parts),
        }),
        "requestContext": {"http": {"path": "/uploads", "method": "POST"}},
        "headers": {},
    }


@pytest.mark.unit
class TestInitializeUploadResponseSizeLimit:
    def test_1500_parts_with_long_non_ascii_keys_are_refused_before_any_upload(self):
        # The short-key file comes first, so a refusal after its multipart upload would show here.
        request = _request((SHORT_KEY, 1), (LONG_NON_ASCII_KEY, 1499))
        with upload_init._initialize_upload_env(_asset()) as (uf, mock_s3):
            with pytest.raises(uf.VAMSGeneralErrorResponse) as err:
                uf.initialize_upload(request, CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_the_refusal_is_a_generic_400_through_the_handler(self):
        with upload_init._initialize_upload_env(_asset()) as (uf, mock_s3), \
                patch.object(uf, "request_to_claims", return_value=CLAIMS), \
                patch.object(uf, "CasbinEnforcer", MagicMock()):
            response = uf.lambda_handler(
                _event((SHORT_KEY, 1), (LONG_NON_ASCII_KEY, 1499)), MagicMock())

        assert response["statusCode"] == 400
        assert MESSAGE in json.loads(response["body"])["message"]
        assert "\\u00e9\\u00e9" not in response["body"]
        assert "scans" not in response["body"]
        mock_s3.create_multipart_upload.assert_not_called()

    def test_1500_parts_with_ordinary_keys_still_initialize(self):
        request = _request((SHORT_KEY, 1), ("/scans/pump-housing.e57", 1499))
        with upload_init._initialize_upload_env(_asset()) as (uf, mock_s3):
            response = uf.initialize_upload(request, CLAIMS)

        assert response.uploadId
        assert [f.numParts for f in response.files] == [1, 1499]
        assert mock_s3.create_multipart_upload.call_count == 2
