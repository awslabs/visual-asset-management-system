# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender handler signs each asset bucket's requests for the bucket's own Region.

constructPipeline hands the state a bucket name -> Region map (``bucketRegions``) for the asset buckets
outside the deployment Region. The handler registers it before its first S3 call, and the primary file's
download and the referenced files' HeadObject calls and downloads for a registered bucket go through a
client for that Region; a bucket the map leaves out (the auxiliary bucket, a state from an older Lambda)
stays on the module's default client.
"""

from unittest.mock import MagicMock, patch

import pytest

from blenderTestSupport import FakeContext, FakeS3, load_handler

PRIMARY = "xid/test/pump.glb"
GLTF = "xid/test/pump.gltf"
REMOTE_BUCKET = "remote-assets"
REMOTE_REGION = "eu-west-1"


def _handler(tmp_path):
    fake = FakeS3()
    fake.sandbox = str(tmp_path)
    handler = load_handler(fake)
    handler.s3_client.meta = MagicMock()
    handler.s3_client.meta.region_name = "us-east-1"
    return handler, fake


@pytest.mark.unit
def test_a_registered_bucket_is_read_through_a_client_for_its_region(tmp_path):
    handler, default = _handler(tmp_path)
    gltf = b'{"asset": {"version": "2.0"}, "buffers": [{"uri": "pump.bin", "byteLength": 2}]}'
    regional = FakeS3(objects={GLTF: gltf, "xid/test/pump.bin": b"\x00\x00"})
    regional.sandbox = str(tmp_path)
    with patch.object(handler.boto3, "client", return_value=regional) as factory:
        handler.register_bucket_regions({REMOTE_BUCKET: REMOTE_REGION, "": "eu-west-2", "no-region": ""})
        assert handler.client_for_bucket(REMOTE_BUCKET) is regional
        assert handler.client_for_bucket(REMOTE_BUCKET) is regional, "one client per Region, built once"
        root, rel, local_path, local_root = handler.input_location({"assetId": "xid"}, GLTF, str(tmp_path / "input"))
        handler.download_primary(REMOTE_BUCKET, GLTF, "v1", local_path)
        resolver = handler.discover_dependencies(REMOTE_BUCKET, root, rel, local_path, local_root, ".gltf",
                                                 {"dependencyLimits": {}}, FakeContext([900_000]))
        resolver.fetch()
    factory.assert_called_once()
    assert factory.call_args.args == ("s3",) and factory.call_args.kwargs["region_name"] == REMOTE_REGION
    config = factory.call_args.kwargs["config"]
    assert config.retries["mode"] == "adaptive" and config.s3["us_east_1_regional_endpoint"] == "regional"
    assert [d["key"] for d in regional.downloads] == [GLTF, "xid/test/pump.bin"]
    assert regional.heads == ["xid/test/pump.bin"]
    assert default.downloads == [] and getattr(default, "heads", []) == []
    assert [entry["rel"] for entry in resolver.report().fetched] == ["test/pump.bin"]


@pytest.mark.unit
def test_an_unregistered_or_same_region_bucket_uses_the_default_client(tmp_path):
    handler, default = _handler(tmp_path)
    with patch.object(handler.boto3, "client") as factory:
        handler.register_bucket_regions({"local-assets": "us-east-1"})
        handler.register_bucket_regions(None)
        assert handler.client_for_bucket("aux-bucket") is default
        assert handler.client_for_bucket("local-assets") is default
    factory.assert_not_called()
