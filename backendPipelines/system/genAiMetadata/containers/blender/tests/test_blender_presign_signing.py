# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender image's S3 clients sign with Signature Version 4, for the bucket's own Region (D111).

The image presigns nothing today, but its clients are configured like the media image's, so a presigned URL one
of them would hand out carries `X-Amz-Algorithm=AWS4-HMAC-SHA256` on the bucket Region's regional endpoint:
without an explicit signature version botocore presigns with Signature Version 2 in us-east-1 and us-west-2, which
Amazon S3 refuses for an object encrypted with an AWS KMS key. Real botocore clients with static example
credentials and an empty AWS config file; nothing is sent.
"""

import importlib.util
import os
import re
import sys
from unittest.mock import patch

import boto3
import pytest

from blenderTestSupport import HANDLER_PATH

REGIONS = ("us-east-1", "us-west-2", "us-east-2")


@pytest.fixture
def isolated_aws(monkeypatch, tmp_path):
    empty = tmp_path / "aws-config"
    empty.write_text("")
    for name in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.setenv(name, str(empty))
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(name, raising=False)

    def real_client(service, region_name=None, **kwargs):
        session = boto3.session.Session(
            aws_access_key_id="AKIDEXAMPLE", aws_secret_access_key="example-secret-key",
            aws_session_token="example-session-token", region_name=region_name or os.environ["AWS_DEFAULT_REGION"])
        return session.client(service, **kwargs)

    with patch("boto3.client", side_effect=real_client):
        yield


def _handler(monkeypatch, region):
    monkeypatch.setenv("AWS_DEFAULT_REGION", region)
    monkeypatch.setenv("AWS_REGION", region)
    spec = importlib.util.spec_from_file_location(f"genai_blender_handler_signing_{region}", HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_sigv4_for(url, region, bucket):
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url and "AWSAccessKeyId=" not in url, url
    assert re.search(rf"X-Amz-Credential=[^&]*%2F{re.escape(region)}%2Fs3%2Faws4_request", url), url
    assert url.startswith(f"https://s3.{region}.amazonaws.com/{bucket}/"), url


@pytest.mark.unit
@pytest.mark.parametrize("region", REGIONS)
def test_the_default_client_presigns_with_sigv4_for_its_region(isolated_aws, monkeypatch, region):
    module = _handler(monkeypatch, region)
    url = module.s3_client.generate_presigned_url(
        "get_object", Params={"Bucket": "vams-assets", "Key": "a1/pump.glb"}, ExpiresIn=900)
    _assert_sigv4_for(url, region, "vams-assets")


@pytest.mark.unit
@pytest.mark.parametrize("region", REGIONS)
def test_a_cross_region_bucket_client_presigns_for_the_bucket_region(isolated_aws, monkeypatch, region):
    module = _handler(monkeypatch, region)
    module.register_bucket_regions({"remote-assets": "eu-west-1"})
    url = module.client_for_bucket("remote-assets").generate_presigned_url(
        "get_object", Params={"Bucket": "remote-assets", "Key": "a1/pump.glb"}, ExpiresIn=900)
    _assert_sigv4_for(url, "eu-west-1", "remote-assets")
