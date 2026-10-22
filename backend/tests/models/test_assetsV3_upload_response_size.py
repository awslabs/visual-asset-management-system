# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The worst-case upload-initialize response stays within the response-size budget.

The initialize response carries one part-upload URL per part, so its size is bounded by
MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST and MAX_FILES_PER_UPLOAD_REQUEST. The bound is computed
arithmetically from the serialized size of one file and one part, under these stated budgets:

- every part-upload URL is URL_LENGTH_BUDGET bytes;
- every relativeKey is MAX_S3_KEY_LENGTH ASCII characters;
- every S3 multipart upload id is UPLOAD_ID_S3_BUDGET characters;
- part numbers and part counts are written with five digits (MAX_PARTS_PER_FILE).

The response is what the Lambda runtime serializes: the proxy envelope with the body as a JSON
string. RESPONSE_SIZE_BUDGET leaves headroom under the 6 MB AWS Lambda synchronous response limit.
"""

import json
import os

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

URL_LENGTH_BUDGET = 2048
UPLOAD_ID_S3_BUDGET = 256
RESPONSE_SIZE_BUDGET = 5 * 1024 * 1024
LAMBDA_RESPONSE_LIMIT = 6 * 1024 * 1024


def _response_bytes(parts_per_file):
    """Serialized size of the Lambda response for files holding the given part counts."""
    from backend.backend.handlers.assets import uploadFile as uf
    from backend.backend.models.assetsV3 import (
        InitializeUploadResponseModel, MAX_PARTS_PER_FILE, MAX_S3_KEY_LENGTH,
        UploadFileResponseModel, UploadPartModel,
    )

    files = [
        UploadFileResponseModel(
            relativeKey="k" * MAX_S3_KEY_LENGTH,
            uploadIdS3="s" * UPLOAD_ID_S3_BUDGET,
            numParts=MAX_PARTS_PER_FILE,
            partUploadUrls=[UploadPartModel(PartNumber=MAX_PARTS_PER_FILE, UploadUrl="u" * URL_LENGTH_BUDGET)
                            for _ in range(count)],
        )
        for count in parts_per_file
    ]
    body = InitializeUploadResponseModel(
        uploadId="i" * 36, files=files, message="Upload initialized successfully").dict()
    return len(json.dumps(dict(uf.success(body=body))))


def _bound(files, parts):
    """Worst-case response size for `files` files holding `parts` parts in all.

    Exact when every file holds at least one part. A file with no parts serializes its empty part
    list without the separator a further part would carry, so each file is given 2 bytes of slack.
    """
    one = _response_bytes([1])
    part_cost = _response_bytes([2]) - one
    file_cost = _response_bytes([1, 1]) - one - part_cost
    return one + (files - 1) * file_cost + (parts - 1) * part_cost + 2 * files


@pytest.mark.unit
class TestInitializeResponseSize:
    @pytest.mark.parametrize("parts_per_file", [[4, 1, 2], [1, 1, 1, 1, 1], [7], [0, 0, 7], [0, 3, 0, 4]])
    def test_the_bound_covers_a_serialized_response(self, parts_per_file):
        """The arithmetic bound is checked against responses small enough to serialize here."""
        files, parts = len(parts_per_file), sum(parts_per_file)
        actual = _response_bytes(parts_per_file)

        assert actual <= _bound(files, parts) <= actual + 2 * files

    def test_the_worst_case_response_fits_the_response_size_budget(self):
        from backend.backend.models.assetsV3 import (
            MAX_FILES_PER_UPLOAD_REQUEST, MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST,
        )

        bound = _bound(MAX_FILES_PER_UPLOAD_REQUEST, MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST)

        assert bound <= RESPONSE_SIZE_BUDGET, (
            f"{MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST} parts at {URL_LENGTH_BUDGET}-byte URLs and "
            f"{MAX_FILES_PER_UPLOAD_REQUEST} files serialize to {bound} bytes")
        assert RESPONSE_SIZE_BUDGET < LAMBDA_RESPONSE_LIMIT
