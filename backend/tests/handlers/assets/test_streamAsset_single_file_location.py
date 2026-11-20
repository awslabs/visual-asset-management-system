# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The stream route serves the keys a single-file asset holds, and no key that only shares its characters.

A single-file asset's location key has no trailing ``/``; the asset holds its object, its companion
folder ``{file key}/`` and its own previews. Appending ``/`` and joining the requested path named a key
that does not exist, so the asset's own file could not be streamed, and a version pin was looked up
under the wrong relative key. Each case drives the real handler through a REST-shaped event (GET and
HEAD) and checks the key S3 is asked for.
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
    @pytest.mark.parametrize("proxy,expected", [
        ("sub/part.obj", "p1/pump.glb/sub/part.obj"),
        ("pump.glb.previewFile.png", "p1/pump.glb.previewFile.png"),
        ("pump.glb.bak", "p1/pump.glb/pump.glb.bak"),
        ("p1/pump.glb.bak", "p1/pump.glb/p1/pump.glb.bak"),
    ])
    def test_every_other_path_stays_inside_the_asset(self, stream, method, proxy, expected):
        m, s3 = stream
        _stream(m, proxy, method=method)
        reader = s3.get_object if method == "GET" else s3.head_object
        assert reader.call_args.kwargs["Key"] == expected

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_version_pin_of_the_asset_object_uses_its_file_name(self, stream, method):
        m, s3 = stream
        m.resolve_file_version_from_asset_version = MagicMock(return_value="v-pinned")
        event = _rest_event({"Range": "bytes=0-9"}, method=method)
        event["pathParameters"]["proxy"] = "pump.glb"
        event["queryStringParameters"] = {"assetVersionId": "2"}
        m.lambda_handler(event, None)
        m.resolve_file_version_from_asset_version.assert_called_once_with("db1", "asset1", "2", "pump.glb")

    def test_control_a_folder_asset_streams_the_joined_key(self, stream):
        m, s3 = stream
        m.get_asset_details.return_value = dict(m.get_asset_details.return_value,
                                                assetLocation={"Key": "asset1/"})
        _stream(m, "scans/pump.glb")
        assert s3.get_object.call_args.kwargs["Key"] == "asset1/scans/pump.glb"
