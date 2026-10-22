# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The upload-initialize response-size model keeps an accepted response under the Lambda limit.

initialize_upload refuses a request whose estimated response is over
MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES. The estimate is a sum over files: a file overhead plus the
relativeKey as it serializes, and for each part a fixed overhead plus the temporary S3 key
percent-encoded, as the part's presigned URL carries it. These tests check that:

- a botocore-signed upload_part URL fits url_length_budget(key) in a Region of every partition,
  signed with fake credentials at realistic maximum lengths: a 1,300-character session token, a
  63-character bucket, a 256-character uploadId and a 7-day expiry;
- the estimate covers the serialized response, which is what the Lambda runtime returns: the
  proxy envelope with the body as a JSON string. It is checked exactly on responses small enough
  to build here, and arithmetically at the file and part caps;
- the budget is under the 6 MB AWS Lambda synchronous response limit;
- a request at the file and part caps with ordinary keys fits the budget.
"""

import base64
import functools
import json
import os
import random

import boto3
import pytest
from botocore.config import Config
from botocore.utils import percent_encode

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

LAMBDA_RESPONSE_LIMIT = 6 * 1024 * 1024
UPLOAD_ID_S3_BUDGET = 256

# Fake signing inputs at realistic maximum lengths. Presigning is local; no request is sent.
_rng = random.Random(612)
SESSION_TOKEN = base64.b64encode(bytes(_rng.randrange(256) for _ in range(975))).decode()[:1300]
S3_UPLOAD_ID = base64.urlsafe_b64encode(
    bytes(_rng.randrange(256) for _ in range(192))).decode()[:UPLOAD_ID_S3_BUDGET]
BUCKET = ("vams-assets-" + "b" * 63)[:63]
EXPIRES_IN = 7 * 24 * 3600
# One Region per partition, plus the longest commercial Region name.
REGIONS = ["us-east-1", "ap-southeast-5", "cn-northwest-1", "us-gov-west-1", "us-iso-east-1",
           "us-isob-east-1"]
SIGNING_CONFIG = Config(signature_version="s3v4",
                        s3={"addressing_style": "path", "us_east_1_regional_endpoint": "regional"})

ASSET_PREFIX = "temp-uploads/0b7f3c52-9d0e-4f6a-8a51-2c4e7d9b1a63/"
# relativeKeys as the request carries them; the temporary key adds ASSET_PREFIX.
KEY_SHAPES = {
    "ascii-short": "/scans/pump.glb",
    "ascii-long": "/scans/" + "a" * 900 + ".glb",
    "two-byte": "/scans/" + "\u00e9" * 400 + ".glb",
    "three-byte": "/scans/" + "\u6f22" * 300 + ".glb",
    "four-byte": "/scans/" + "\U0001F600" * 200 + ".glb",
    "punctuation": "/scans/sp ace+plus~tilde!bang'q(p)*star&amp=eq;semi,comma$d@at.glb",
}


def _temp_key(relative_key):
    return ASSET_PREFIX + relative_key.lstrip("/")


@functools.lru_cache(maxsize=None)
def _signing_client(region):
    session = boto3.session.Session(aws_access_key_id="testing".ljust(20, "0"),
                                    aws_secret_access_key="testing".ljust(40, "0"),
                                    aws_session_token=SESSION_TOKEN)
    return session.client("s3", region_name=region, config=SIGNING_CONFIG)


def _signed_url(region, temp_key, part_number=10000):
    return _signing_client(region).generate_presigned_url(
        ClientMethod="upload_part",
        Params={"Bucket": BUCKET, "Key": temp_key, "PartNumber": part_number,
                "UploadId": S3_UPLOAD_ID},
        ExpiresIn=EXPIRES_IN)


def url_length_budget(temp_key):
    """Serialized bytes the model allows one part: the fixed overhead, which covers the URL's
    host, bucket and query string and the part's JSON wrapping, plus the key as the URL carries it."""
    from backend.backend.models.assetsV3 import UPLOAD_PART_URL_FIXED_OVERHEAD_BYTES
    return UPLOAD_PART_URL_FIXED_OVERHEAD_BYTES + len(percent_encode(temp_key, safe="/~"))


def _estimate(files):
    """The estimate initialize_upload compares with the budget, for (relativeKey, parts) pairs."""
    from backend.backend.handlers.assets import uploadFile as uf
    from backend.backend.models.assetsV3 import UPLOAD_RESPONSE_BASE_OVERHEAD_BYTES
    return UPLOAD_RESPONSE_BASE_OVERHEAD_BYTES + sum(
        uf.estimate_initialize_response_file_bytes(key, _temp_key(key), parts) for key, parts in files)


def _response_bytes(files):
    """Serialized size of the Lambda response for (relativeKey, [part URLs]) pairs."""
    from backend.backend.handlers.assets import uploadFile as uf
    from backend.backend.models.assetsV3 import (
        InitializeUploadResponseModel, MAX_PARTS_PER_FILE, UploadFileResponseModel, UploadPartModel,
    )

    body = InitializeUploadResponseModel(
        uploadId="y" + "i" * 36,
        files=[
            UploadFileResponseModel(
                relativeKey=key,
                uploadIdS3=S3_UPLOAD_ID,
                numParts=MAX_PARTS_PER_FILE,
                partUploadUrls=[UploadPartModel(PartNumber=MAX_PARTS_PER_FILE, UploadUrl=url)
                                for url in urls],
            )
            for key, urls in files
        ],
        message="Upload initialized successfully",
    ).dict()
    return len(json.dumps(dict(uf.success(body=body))))


def _bound(key, url, files, parts):
    """Serialized size of `files` files with relativeKey `key` holding `parts` parts at `url`.

    Exact when every file holds at least one part. A file with no parts serializes its empty part
    list without the separator a further part would carry, so each file is given 2 bytes of slack.
    """
    one = _response_bytes([(key, [url])])
    part_cost = _response_bytes([(key, [url, url])]) - one
    file_cost = _response_bytes([(key, [url]), (key, [url])]) - one - part_cost
    return one + (files - 1) * file_cost + (parts - 1) * part_cost + 2 * files


@pytest.mark.unit
class TestInitializeResponseSize:
    @pytest.mark.parametrize("parts_per_file", [[4, 1, 2], [1, 1, 1, 1, 1], [7], [0, 0, 7], [0, 3, 0, 4]])
    def test_the_bound_covers_a_serialized_response(self, parts_per_file):
        """The arithmetic bound is checked against responses small enough to serialize here."""
        key, url = KEY_SHAPES["ascii-long"], "u" * 2048
        files, parts = len(parts_per_file), sum(parts_per_file)
        actual = _response_bytes([(key, [url] * count) for count in parts_per_file])

        assert actual <= _bound(key, url, files, parts) <= actual + 2 * files

    @pytest.mark.parametrize("region", REGIONS)
    @pytest.mark.parametrize("shape", sorted(KEY_SHAPES))
    def test_a_signed_part_url_fits_its_budget(self, region, shape):
        key = KEY_SHAPES[shape]
        url = _signed_url(region, _temp_key(key))
        part_cost = _response_bytes([(key, [url, url])]) - _response_bytes([(key, [url])])

        assert part_cost <= url_length_budget(_temp_key(key))

    @pytest.mark.parametrize("shape", sorted(KEY_SHAPES))
    def test_the_estimate_covers_a_response_with_signed_urls(self, shape):
        key = KEY_SHAPES[shape]
        parts_per_file = [3, 0, 2]
        files = [(f"{key[:-4]}-{i}.glb", count) for i, count in enumerate(parts_per_file)]
        response = [
            (rel, [_signed_url("ap-southeast-5", _temp_key(rel), n) for n in range(1, count + 1)])
            for rel, count in files
        ]

        assert _response_bytes(response) <= _estimate(files)

    @pytest.mark.parametrize("shape", sorted(KEY_SHAPES))
    def test_the_estimate_covers_the_response_at_the_caps(self, shape):
        from backend.backend.models.assetsV3 import (
            MAX_FILES_PER_UPLOAD_REQUEST, MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST,
        )
        key = KEY_SHAPES[shape]
        url = _signed_url("ap-southeast-5", _temp_key(key))
        files, parts = MAX_FILES_PER_UPLOAD_REQUEST, MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST
        # Every file holds at least one part; the rest go to the first file.
        layout = [(key, parts - files + 1)] + [(key, 1)] * (files - 1)

        assert _bound(key, url, files, parts) <= _estimate(layout)

    def test_the_budget_is_under_the_lambda_response_limit(self):
        from backend.backend.models.assetsV3 import MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES

        assert MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES < LAMBDA_RESPONSE_LIMIT

    def test_ordinary_keys_at_the_file_and_part_caps_fit_the_budget(self):
        """The size refusal does not bite a request at the caps whose keys are of ordinary length."""
        from backend.backend.models.assetsV3 import (
            MAX_FILES_PER_UPLOAD_REQUEST, MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST,
            MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES,
        )
        files, parts = MAX_FILES_PER_UPLOAD_REQUEST, MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST
        keys = [f"/site-survey/building-a/level-02/scan-{i:04d}.e57" for i in range(files)]
        layout = [(keys[0], parts - files + 1)] + [(k, 1) for k in keys[1:]]

        assert _estimate(layout) <= MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES
