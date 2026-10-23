# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset bucket may be in another Region than the container.

The definition carries a bucket name -> Region map (`bucketRegions`, built by constructPipeline from
the manifest); `run()` registers it before the first S3 call and every request for a registered bucket
is signed with a client for that Region, built once per Region with the adaptive retry mode and the
regional us-east-1 endpoint. A bucket the map leaves out (the auxiliary bucket, an unregistered name,
a definition from an older Lambda) stays on the default client, so every existing fake-S3 test holds."""

import os
from unittest.mock import MagicMock, patch

import pytest

from conftest import FakeS3, make_clients, make_definition


class RegionalFakeS3(FakeS3):
    """A FakeS3 that reports a region_name through `meta`, as a boto3 client does."""

    def __init__(self, region):
        super().__init__()
        self.meta = MagicMock(region_name=region)


@pytest.mark.unit
class TestClientsRouteByBucketRegion:
    def test_an_unregistered_bucket_uses_the_default_client(self):
        clients = make_clients(s3=RegionalFakeS3("us-east-1"))
        assert clients.s3_for_bucket("aux-bucket") is clients.s3
        clients.register_bucket_regions({})
        clients.register_bucket_regions(None)
        assert clients.s3_for_bucket("asset-bucket") is clients.s3

    def test_a_bucket_registered_in_the_deployment_region_uses_the_default_client(self):
        clients = make_clients(s3=RegionalFakeS3("us-east-1"))
        clients.register_bucket_regions({"asset-bucket": "us-east-1"})
        assert clients.s3_for_bucket("asset-bucket") is clients.s3

    def test_a_bucket_in_another_region_gets_one_regional_client_per_region(self):
        clients = make_clients(s3=RegionalFakeS3("us-east-1"))
        clients.register_bucket_regions({"in-bkt": "eu-west-1", "out-bkt": "eu-west-1", "far-bkt": "ap-southeast-2"})
        built = []

        def fake_client(service, region_name=None, config=None):
            built.append((service, region_name, config))
            return MagicMock(name=f"s3-{region_name}")

        with patch("video_sop_bom_pipeline.clients.boto3.client", side_effect=fake_client):
            eu = clients.s3_for_bucket("in-bkt")
            assert clients.s3_for_bucket("out-bkt") is eu, "one client per Region, not per bucket"
            far = clients.s3_for_bucket("far-bkt")
        assert eu is not clients.s3 and far is not clients.s3 and far is not eu
        assert [(s, r) for s, r, _ in built] == [("s3", "eu-west-1"), ("s3", "ap-southeast-2")]
        for _, _, config in built:
            assert config.retries == {"max_attempts": 5, "mode": "adaptive"}
            assert config.s3 == {"us_east_1_regional_endpoint": "regional"}

    def test_empty_names_and_regions_are_ignored_at_registration(self):
        clients = make_clients(s3=RegionalFakeS3("us-east-1"))
        clients.register_bucket_regions({"": "eu-west-1", "asset-bucket": ""})
        assert clients.bucket_regions == {}


@pytest.mark.unit
class TestTheStagesAddressEachBucketInItsRegion:
    def test_run_registers_the_definition_map_before_the_first_s3_call(self, tmp_path):
        from video_sop_bom_pipeline import pipeline

        clients = make_clients(s3=RegionalFakeS3("us-east-1"))
        definition = make_definition(bucketRegions={"asset-bucket": "eu-west-1"})
        registered = {}

        def stop_at_first_stage(ctx):
            registered.update(ctx.clients.bucket_regions)
            raise RuntimeError("stop")

        with patch.object(pipeline, "_run_stages", side_effect=stop_at_first_stage), \
                patch.object(pipeline, "send_task_failure", MagicMock()):
            status = pipeline.run(definition, MagicMock(), clients, work_dir=str(tmp_path))
        assert status == "FAILED"
        assert registered == {"asset-bucket": "eu-west-1"}

    def test_upload_writes_through_the_output_buckets_client(self, tmp_path):
        from video_sop_bom_pipeline import upload
        from video_sop_bom_pipeline.upload import output_dirs

        default = RegionalFakeS3("us-east-1")
        regional = FakeS3()
        clients = make_clients(s3=default)
        clients.register_bucket_regions({"run-bucket": "us-west-2"})
        clients.s3_by_region["us-west-2"] = regional
        definition = make_definition()
        dirs = output_dirs(str(tmp_path))
        for d in dirs.values():
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(dirs["files"], "sop.md"), "w", encoding="utf-8") as handle:
            handle.write("# SOP\n")

        report = upload.upload_outputs(clients, definition, str(tmp_path))
        assert report.file_count == 1
        assert regional.uploads == [("run-bucket", definition["outputs"]["files"] + "sop-bom/sop.md")]
        assert default.uploads == [], "nothing for the output bucket went to the deployment-Region client"

    def test_a_definition_without_the_map_keeps_every_stage_on_the_default_client(self, tmp_path):
        from video_sop_bom_pipeline import upload
        from video_sop_bom_pipeline.upload import output_dirs

        default = RegionalFakeS3("us-east-1")
        clients = make_clients(s3=default)
        definition = make_definition()
        assert "bucketRegions" not in definition
        clients.register_bucket_regions(definition.get("bucketRegions"))
        dirs = output_dirs(str(tmp_path))
        for d in dirs.values():
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(dirs["files"], "sop.md"), "w", encoding="utf-8") as handle:
            handle.write("# SOP\n")
        upload.upload_outputs(clients, definition, str(tmp_path))
        assert default.uploads == [("run-bucket", definition["outputs"]["files"] + "sop-bom/sop.md")]
