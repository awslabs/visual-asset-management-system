#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail container: S3 requests are signed for each asset bucket's Region (D3).

The pipeline definition carries a bucket name -> Region map (``bucketRegions``); ``core.run``
registers it before any S3 call, and every request for a registered bucket is signed with a client
for that Region (adaptive retries, regional us-east-1 endpoint, no endpoint_url). Buckets the map
leaves out -- the auxiliary bucket, a definition from an older Lambda -- stay on the default client."""

import os
from unittest.mock import ANY, MagicMock

import pytest

from preview_pipeline.utils import s3_utils as s3  # noqa: E402


@pytest.fixture
def s3mod():
    s3.register_bucket_regions({})
    saved_regions, saved_clients = dict(s3._bucket_regions), dict(s3._clients_by_region)
    s3._bucket_regions.clear()
    s3._clients_by_region.clear()
    yield s3
    s3._bucket_regions.clear()
    s3._bucket_regions.update(saved_regions)
    s3._clients_by_region.clear()
    s3._clients_by_region.update(saved_clients)


@pytest.mark.unit
class TestClientForBucket:
    def test_an_unregistered_bucket_uses_the_default_client(self, s3mod):
        assert s3mod.client_for_bucket("aux-bucket") is s3mod.client
        assert s3mod.client_for_bucket("") is s3mod.client
        assert s3mod.client_for_bucket(None) is s3mod.client

    def test_a_registered_bucket_gets_one_client_for_its_region(self, s3mod):
        s3mod.register_bucket_regions({"remote-bkt": "eu-west-1"})
        regional = s3mod.client_for_bucket("remote-bkt")
        assert regional is not s3mod.client
        assert regional.meta.region_name == "eu-west-1"
        assert regional.meta.endpoint_url == "https://s3.eu-west-1.amazonaws.com"
        retries = regional.meta.config.retries
        # botocore normalises max_attempts=5 into total_max_attempts=6 (the first try plus 5 retries)
        assert (retries["mode"], retries["total_max_attempts"]) == ("adaptive", 6)
        assert regional.meta.config.s3 == {"us_east_1_regional_endpoint": "regional"}
        # One client per Region, reused across buckets and calls
        s3mod.register_bucket_regions({"other-bkt": "eu-west-1"})
        assert s3mod.client_for_bucket("remote-bkt") is regional
        assert s3mod.client_for_bucket("other-bkt") is regional

    def test_a_bucket_in_the_default_region_keeps_the_default_client(self, s3mod):
        s3mod.register_bucket_regions({"local-bkt": s3mod.client.meta.region_name})
        assert s3mod.client_for_bucket("local-bkt") is s3mod.client

    def test_empty_names_regions_and_a_missing_map_register_nothing(self, s3mod):
        s3mod.register_bucket_regions(None)
        s3mod.register_bucket_regions({"": "eu-west-1", "bkt": "", "bkt2": None})
        assert s3mod._bucket_regions == {}


@pytest.mark.unit
class TestDownloadRoutesByBucketRegion:
    def test_download_runs_on_the_bucket_region_client(self, s3mod, tmp_path, monkeypatch):
        default_client, regional = MagicMock(name="default"), MagicMock(name="eu-west-1")
        monkeypatch.setattr(s3mod, "client", default_client)
        s3mod._clients_by_region["eu-west-1"] = regional
        s3mod.register_bucket_regions({"remote-bkt": "eu-west-1"})
        target = str(tmp_path / "scan.bin")

        assert s3mod.download("remote-bkt", "x/scan.bin", target) == target
        regional.download_fileobj.assert_called_once_with("remote-bkt", "x/scan.bin", ANY)
        default_client.download_fileobj.assert_not_called()

    def test_download_from_an_unregistered_bucket_stays_on_the_default_client(
            self, s3mod, tmp_path, monkeypatch):
        default_client = MagicMock(name="default")
        monkeypatch.setattr(s3mod, "client", default_client)
        target = str(tmp_path / "scan.bin")

        assert s3mod.download("aux-bkt", "x/scan.bin", target) == target
        default_client.download_fileobj.assert_called_once_with("aux-bkt", "x/scan.bin", ANY)
