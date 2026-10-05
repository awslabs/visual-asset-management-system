# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A GET on either stream endpoint answers 404 for a file that does not exist.

Both stream handlers read the object with S3 GetObject before deciding between the presigned redirect
and inline delivery, so a missing key surfaces there as ``NoSuchKey`` (``NoSuchVersion`` for a
``versionId`` S3 does not hold). Those answer ``404 File not found``, the status the HEAD path of the
same handlers and the API reference give for a missing file. Any other S3 error keeps its ``400``.
The streaming CORS headers are on every error response, so a browser viewer can read the status.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets import test_streamAsset_range_header_casing as asset_stream
from backend.tests.handlers.assets import test_streamAuxiliaryPreviewAsset_range_header_casing as aux_stream


def _s3_error(code, status):
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "GetObject",
    )


def _asset_get(error):
    m = asset_stream._load()
    asset_stream._wire(m).get_object.side_effect = error
    return m.lambda_handler(asset_stream._rest_event({"Accept": "*/*"}), MagicMock())


def _aux_get(error):
    m = aux_stream._load()
    aux_stream._wire(m).get_object.side_effect = error
    return m.lambda_handler(aux_stream._rest_event({"Accept": "*/*"}), MagicMock())


_HANDLERS = [pytest.param(_asset_get, id="streamAsset"), pytest.param(_aux_get, id="streamAuxiliaryPreviewAsset")]


@pytest.mark.unit
class TestStreamMissingFileStatus:

    @pytest.mark.parametrize("get", _HANDLERS)
    @pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchVersion"])
    def test_a_missing_file_answers_404(self, get, code):
        response = get(_s3_error(code, 404))

        assert response["statusCode"] == 404
        assert json.loads(response["body"])["message"] == "File not found"
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    @pytest.mark.parametrize("get", _HANDLERS)
    def test_any_other_s3_error_keeps_its_400(self, get):
        response = get(_s3_error("AccessDenied", 403))

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].startswith("Error Fetching ")
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"
