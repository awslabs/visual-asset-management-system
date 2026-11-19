# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The stream route serves a single-file asset's one object, and no key beside or under it.

A single-file asset's location key has no trailing ``/``. Appending one and joining the requested path
named a key that does not exist, so the asset's own file could not be streamed, while a path such as
``pump.glb.bak`` was joined into another key the asset does not hold. Each case drives the real handler
through a REST-shaped event (GET and HEAD) and checks the key S3 is asked for.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.assets.test_streamAsset_range_header_casing import _load, _rest_event, _wire

FILE_LOCATION = "p1/pump.glb"


def _stream(m, proxy, method="GET"):
    event = _rest_event({"Range": "bytes=0-9"}, method=method)
    event["pathParameters"]["proxy"] = proxy
    return m.lambda_handler(event, None)


@pytest.fixture
def stream():
    m = _load()
    s3 = _wire(m)
    s3.head_object.return_value = {"ContentType": "model/gltf-binary", "ContentLength": 1024,
                                   "ResponseMetadata": {"HTTPHeaders": {}}}
    m.get_asset_details = MagicMock(return_value={
        "databaseId": "db1", "assetId": "asset1", "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": FILE_LOCATION},
    })
    return m, s3


@pytest.mark.unit
class TestStreamSingleFileAsset:
    @pytest.mark.parametrize("proxy", ["pump.glb", FILE_LOCATION])
    def test_get_streams_the_asset_object(self, stream, proxy):
        m, s3 = stream
        _stream(m, proxy)
        assert s3.get_object.call_args.kwargs["Key"] == FILE_LOCATION

    @pytest.mark.parametrize("proxy", ["pump.glb", FILE_LOCATION])
    def test_head_reads_the_asset_object(self, stream, proxy):
        m, s3 = stream
        _stream(m, proxy, method="HEAD")
        assert s3.head_object.call_args.kwargs["Key"] == FILE_LOCATION

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    @pytest.mark.parametrize("proxy", ["pump.glb.bak", "pump.glb/inner.txt", "other.glb"])
    def test_no_other_key_reaches_s3(self, stream, method, proxy):
        m, s3 = stream
        response = _stream(m, proxy, method=method)
        assert response["statusCode"] == 400
        assert "not part of this asset" in json.loads(response["body"])["message"]
        s3.get_object.assert_not_called()
        s3.head_object.assert_not_called()

    def test_control_a_folder_asset_streams_the_joined_key(self, stream):
        m, s3 = stream
        m.get_asset_details.return_value = dict(m.get_asset_details.return_value,
                                                assetLocation={"Key": "asset1/"})
        _stream(m, "scans/pump.glb")
        assert s3.get_object.call_args.kwargs["Key"] == "asset1/scans/pump.glb"
