#  SPDX-License-Identifier: Apache-2.0

"""simulation/isaacLabTraining container: S3 requests are signed for each asset bucket's Region (D3).

The job config carries a bucket name -> Region map (``bucketRegions``, built by openPipeline from the
manifest's bucket Regions); ``run_pipeline`` registers it on the ``S3Client`` before any S3 call, and
every request for a registered bucket is signed with a client for that Region (adaptive retries,
regional us-east-1 endpoint, no endpoint_url). Buckets the map leaves out stay on the default client."""

import os
import sys
from unittest.mock import MagicMock

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder

from utils.aws.s3 import S3Client  # noqa: E402


@pytest.mark.unit
class TestClientForBucket:
    def test_an_unregistered_bucket_uses_the_default_client(self):
        s3 = S3Client()
        assert s3.client_for_bucket("aux-bucket") is s3.client
        assert s3.client_for_bucket("") is s3.client

    def test_a_registered_bucket_gets_one_client_for_its_region(self):
        s3 = S3Client()
        s3.register_bucket_regions({"remote-bkt": "eu-west-1", "other-bkt": "eu-west-1"})
        regional = s3.client_for_bucket("remote-bkt")
        assert regional is not s3.client
        assert regional.meta.region_name == "eu-west-1"
        assert regional.meta.endpoint_url == "https://s3.eu-west-1.amazonaws.com"
        retries = regional.meta.config.retries
        # botocore normalises max_attempts=5 into total_max_attempts=6 (the first try plus 5 retries)
        assert (retries["mode"], retries["total_max_attempts"]) == ("adaptive", 6)
        assert regional.meta.config.s3 == {"us_east_1_regional_endpoint": "regional"}
        assert s3.client_for_bucket("other-bkt") is regional

    def test_a_bucket_in_the_default_region_keeps_the_default_client(self):
        s3 = S3Client()
        s3.register_bucket_regions({"local-bkt": s3.client.meta.region_name})
        assert s3.client_for_bucket("local-bkt") is s3.client

    def test_empty_names_regions_and_a_missing_map_register_nothing(self):
        s3 = S3Client()
        s3.register_bucket_regions(None)
        s3.register_bucket_regions({"": "eu-west-1", "bkt": ""})
        assert s3._bucket_regions == {}


@pytest.mark.unit
class TestTransfersRouteByBucketRegion:
    @pytest.fixture
    def routed(self):
        s3 = S3Client()
        default_client, regional = MagicMock(name="default"), MagicMock(name="eu-west-1")
        s3.client = default_client
        s3._clients_by_region["eu-west-1"] = regional
        s3.register_bucket_regions({"remote-bkt": "eu-west-1"})
        return s3, default_client, regional

    def test_download_directory_lists_and_downloads_on_the_bucket_region_client(self, routed, tmp_path):
        s3, default_client, regional = routed
        regional.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "runs/a/model.pt"}]}]

        s3.download_directory("s3://remote-bkt/runs/a", str(tmp_path / "out"))

        regional.get_paginator.assert_called_once_with("list_objects_v2")
        regional.download_file.assert_called_once_with(
            "remote-bkt", "runs/a/model.pt", str(tmp_path / "out" / "model.pt"))
        default_client.get_paginator.assert_not_called()
        default_client.download_file.assert_not_called()

    def test_uploads_route_on_the_target_bucket(self, routed, tmp_path):
        s3, default_client, regional = routed
        local = tmp_path / "policy.pt"
        local.write_bytes(b"x")

        s3.upload_file(str(local), "s3://remote-bkt/out/policy.pt")
        s3.upload_file(str(local), "s3://aux-bkt/out/policy.pt")

        regional.upload_file.assert_called_once_with(str(local), "remote-bkt", "out/policy.pt")
        default_client.upload_file.assert_called_once_with(str(local), "aux-bkt", "out/policy.pt")
