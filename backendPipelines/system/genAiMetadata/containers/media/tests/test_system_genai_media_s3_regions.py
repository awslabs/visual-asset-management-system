# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The media container signs each asset bucket's requests for the bucket's own Region.

constructPipeline hands the state a bucket name -> Region map (``bucketRegions``) for the asset buckets
outside the deployment Region. Both handlers register it before their first S3 call, and every request
for a registered bucket -- the extract handler's input download, the segment handler's presigned URL and
its fallback download -- goes through a client for that Region; a bucket the map leaves out (the
auxiliary bucket, the run bucket, a state from an older Lambda) stays on the handler's default client."""

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from system_genai_media_fixtures import FakeS3, png_bytes

import test_system_genai_media_handler as handler_suite

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

REMOTE_REGION = "eu-west-1"


def _registry():
    """A fresh copy of the registry module, so one test's registrations never reach another."""
    import media_extractors.s3_regions as module
    return importlib.reload(module)


def _default_client(region="us-east-1"):
    client = MagicMock(name="default")
    client.meta.region_name = region
    return client


@pytest.mark.unit
class TestRegistry:
    def test_a_registered_bucket_gets_a_client_for_its_region_built_once(self):
        reg = _registry()
        default = _default_client()
        built = []

        def factory(service, **kwargs):
            built.append((service, kwargs))
            client = MagicMock(name=kwargs["region_name"])
            client.meta.region_name = kwargs["region_name"]
            return client

        with patch.object(reg.boto3, "client", side_effect=factory):
            reg.register_bucket_regions({"remote-bkt": REMOTE_REGION, "": "eu-west-2", "no-region": ""})
            first = reg.client_for_bucket("remote-bkt", default)
            second = reg.client_for_bucket("remote-bkt", default)
        assert first is second and first is not default
        assert first.meta.region_name == REMOTE_REGION
        # Adaptive retries and the regional us-east-1 endpoint, as every pipeline client.
        assert len(built) == 1 and built[0][0] == "s3" and built[0][1]["region_name"] == REMOTE_REGION
        config = built[0][1]["config"]
        assert config.retries["mode"] == "adaptive" and config.retries["max_attempts"] == 5
        assert config.s3["us_east_1_regional_endpoint"] == "regional"
        assert reg.registered_region("remote-bkt") == REMOTE_REGION
        # An empty name or an empty Region registers nothing.
        assert reg.registered_region("") == "" and reg.registered_region("no-region") == ""

    def test_an_unregistered_or_same_region_bucket_uses_the_default_client(self):
        reg = _registry()
        default = _default_client("us-east-1")
        with patch.object(reg.boto3, "client") as factory:
            reg.register_bucket_regions({"local-bkt": "us-east-1"})
            reg.register_bucket_regions(None)
            assert reg.client_for_bucket("aux-bkt", default) is default
            assert reg.client_for_bucket("local-bkt", default) is default
        factory.assert_not_called()


@pytest.mark.unit
class TestExtractHandlerDownloadsThroughTheRegionClient:
    def test_the_input_download_goes_to_the_bucket_region_client(self, monkeypatch):
        s3 = handler_suite.FakeS3()
        s3.objects[(handler_suite._AUX, handler_suite._MANIFEST_KEY)] = json.dumps(handler_suite._pre_manifest()).encode()
        s3.objects[(handler_suite._RUN, handler_suite._CONFIG_KEY)] = json.dumps(handler_suite._CONFIG_BODY).encode()
        module = handler_suite._load(s3)
        regional = FakeS3()
        regional.objects[(handler_suite._ASSETS, "a1/photo.png")] = png_bytes(300, 100)
        s3_regions = sys.modules[module.client_for_bucket.__module__]
        monkeypatch.setattr(s3_regions, "_bucket_regions", {})
        monkeypatch.setattr(s3_regions, "_clients_by_region", {})
        monkeypatch.setattr(s3_regions.boto3, "client", MagicMock(return_value=regional))
        event = handler_suite._event("photo.png", "image")
        event["bucketRegions"] = {handler_suite._ASSETS: REMOTE_REGION}
        module.lambda_handler(event, None)
        # The input came from the Region client; the manifest and renders stayed on the default client.
        assert regional.downloads == [(handler_suite._ASSETS, "a1/photo.png", "v1")]
        assert s3.downloads == []
        assert (handler_suite._AUX, handler_suite._MANIFEST_KEY, "application/json") in s3.puts
        s3_regions.boto3.client.assert_called_once()
        assert s3_regions.boto3.client.call_args.kwargs["region_name"] == REMOTE_REGION

    def test_a_state_without_the_map_downloads_through_the_default_client(self, monkeypatch):
        s3 = handler_suite.FakeS3()
        s3.objects[(handler_suite._AUX, handler_suite._MANIFEST_KEY)] = json.dumps(handler_suite._pre_manifest()).encode()
        s3.objects[(handler_suite._RUN, handler_suite._CONFIG_KEY)] = json.dumps(handler_suite._CONFIG_BODY).encode()
        s3.objects[(handler_suite._ASSETS, "a1/photo.png")] = png_bytes(300, 100)
        module = handler_suite._load(s3)
        s3_regions = sys.modules[module.client_for_bucket.__module__]
        monkeypatch.setattr(s3_regions, "_bucket_regions", {})
        monkeypatch.setattr(s3_regions.boto3, "client", MagicMock())
        module.lambda_handler(handler_suite._event("photo.png", "image"), None)
        assert s3.downloads == [(handler_suite._ASSETS, "a1/photo.png", "v1")]
        s3_regions.boto3.client.assert_not_called()


@pytest.mark.unit
class TestSegmentHandlerSignsForTheBucketRegion:
    def _module(self, monkeypatch, regional):
        import test_system_genai_media_segment_handler as seg
        s3 = seg._seed(FakeS3())
        module = seg._load(monkeypatch, s3, seg.FakeBedrockRuntime([seg._reply()]), seg.FakeEvents())
        s3_regions = sys.modules[module.client_for_bucket.__module__]
        monkeypatch.setattr(s3_regions, "_bucket_regions", {})
        monkeypatch.setattr(s3_regions, "_clients_by_region", {})
        monkeypatch.setattr(s3_regions.boto3, "client", MagicMock(return_value=regional))
        return seg, module, s3, s3_regions

    def test_the_presigned_url_and_the_fallback_download_use_the_region_client(self, monkeypatch):
        regional = FakeS3()
        seg, module, s3, s3_regions = self._module(monkeypatch, regional)
        input_bucket = module.parse_s3_uri(seg._STATE["inputS3AssetFilePath"])[0]
        regional.objects[(input_bucket, module.parse_s3_uri(seg._STATE["inputS3AssetFilePath"])[1])] = b"\x00"
        state = dict(seg._STATE, bucketRegions={input_bucket: REMOTE_REGION})
        seg._run(module, state=state)
        assert regional.presigns, "the window's frames were presigned through the Region client"
        assert all(params["Bucket"] == input_bucket for _method, params, _expires in regional.presigns)
        assert s3.presigns == []
        s3_regions.boto3.client.assert_called_once()
        assert s3_regions.boto3.client.call_args.kwargs["region_name"] == REMOTE_REGION

    def test_a_state_without_the_map_presigns_through_the_default_client(self, monkeypatch):
        seg, module, s3, s3_regions = self._module(monkeypatch, FakeS3())
        seg._run(module)
        assert s3.presigns
        s3_regions.boto3.client.assert_not_called()
