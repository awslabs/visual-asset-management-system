#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The media image presigns with Signature Version 4, for the bucket's own Region (D111).

The extract handler reads a video or audio file over the download bound over a presigned URL, and the segment
handler reads each video window's frames over one. Their S3 clients set no signature version, so botocore
presigned with Signature Version 2 in the Regions that still accept it (us-east-1, us-west-2), and Amazon S3
refused the URL for an object encrypted with an AWS KMS key: ffmpeg got `400 Bad Request`. Every S3 client of the
image is now built from `s3_regions.S3_CLIENT_CONFIG` (Signature Version 4, path-style addressing on the regional
endpoint), so each URL carries `X-Amz-Algorithm=AWS4-HMAC-SHA256` and a credential scoped to the bucket's Region,
whichever Region the function runs in, and a cross-Region bucket's URL is signed for that bucket's Region.

The clients here are real botocore clients with static example credentials and an empty AWS config file, so the
host's own configuration and credentials take no part; nothing is sent, a presign is computed locally.
"""

import importlib.util
import os
import re
import sys
from unittest.mock import patch

import boto3
import pytest

import media_extractors.s3_regions as s3_regions
from test_system_genai_media_segment_handler import _ENV as SEGMENT_ENV

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SYSTEM_DIR = os.path.abspath(os.path.join(_CONTAINER_DIR, "..", "..", ".."))
REGIONS = ("us-east-1", "us-west-2", "us-east-2")
REMOTE_REGION = "eu-west-1"
BUCKET = "vams-assets"


@pytest.fixture
def isolated_aws(monkeypatch, tmp_path):
    """An empty AWS config and credentials file and no named profile, so only the arguments below configure a
    client; boto3.client builds real clients with static example credentials."""
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
    s3_regions._bucket_regions.clear()
    s3_regions._clients_by_region.clear()


def _in_region(monkeypatch, region):
    monkeypatch.setenv("AWS_DEFAULT_REGION", region)
    monkeypatch.setenv("AWS_REGION", region)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract_handler(monkeypatch, region):
    _in_region(monkeypatch, region)
    return _load(os.path.join(_CONTAINER_DIR, "handler.py"), f"system_genai_media_handler_signing_{region}")


def _segment_handler(monkeypatch, region):
    for key, value in SEGMENT_ENV.items():
        monkeypatch.setenv(key, value)
    _in_region(monkeypatch, region)
    return _load(os.path.join(_CONTAINER_DIR, "segment_handler.py"), f"system_genai_media_segment_signing_{region}")


def _assert_sigv4_for(url, region, bucket=BUCKET):
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url, url
    assert "AWSAccessKeyId=" not in url and "Signature=" not in url.replace("X-Amz-Signature=", "")
    assert re.search(rf"X-Amz-Credential=[^&]*%2F{re.escape(region)}%2Fs3%2Faws4_request", url), url
    # The bucket Region's regional endpoint, path-style, never the global s3.amazonaws.com name.
    assert url.startswith(f"https://s3.{region}.amazonaws.com/{bucket}/"), url


@pytest.mark.unit
@pytest.mark.parametrize("region", REGIONS)
def test_the_extract_handler_presigns_with_sigv4_for_its_region(isolated_aws, monkeypatch, region):
    module = _extract_handler(monkeypatch, region)
    _assert_sigv4_for(module.presign_input(BUCKET, "a1/long-shift.mp4", "v1"), region)


@pytest.mark.unit
@pytest.mark.parametrize("region", REGIONS)
def test_the_segment_handler_presigns_with_sigv4_for_its_region(isolated_aws, monkeypatch, region):
    module = _segment_handler(monkeypatch, region)
    _assert_sigv4_for(module.presign_input(BUCKET, "a1/long-shift.mp4", "v1"), region)


@pytest.mark.unit
@pytest.mark.parametrize("region", REGIONS)
@pytest.mark.parametrize("loader", [_extract_handler, _segment_handler], ids=["extract", "segment"])
def test_a_cross_region_bucket_is_presigned_for_its_own_region(isolated_aws, monkeypatch, region, loader):
    module = loader(monkeypatch, region)
    module.register_bucket_regions({"remote-assets": REMOTE_REGION})
    _assert_sigv4_for(module.presign_input("remote-assets", "a1/clip.mp4", "v1"), REMOTE_REGION, "remote-assets")


@pytest.mark.unit
def test_every_media_client_config_is_sigv4_with_regional_path_addressing():
    config = s3_regions.S3_CLIENT_CONFIG
    assert config.signature_version == "s3v4"
    assert config.s3 == {"addressing_style": "path", "us_east_1_regional_endpoint": "regional"}
    assert config.retries["mode"] == "adaptive" and config.retries["total_max_attempts"] == 6
    assert s3_regions._regional_retry_config is config


@pytest.mark.unit
def test_only_the_two_media_handlers_presign():
    """A new presign site in the pipeline must use a Signature Version 4 client like these two; the list is pinned
    so adding one is a reviewed change."""
    sites = []
    for root, _dirs, files in os.walk(_SYSTEM_DIR):
        if os.sep + "tests" in root or "__pycache__" in root:
            continue
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as handle:
                    if "generate_presigned_url" in handle.read():
                        sites.append(os.path.relpath(path, _SYSTEM_DIR))
    assert sorted(sites) == [os.path.join("genAiMetadata", "containers", "media", "handler.py"),
                             os.path.join("genAiMetadata", "containers", "media", "segment_handler.py")]
