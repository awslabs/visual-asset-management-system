# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S3 Transfer Acceleration for presigned URLs (common.s3, #1610).

A bucket row carrying ``isAccelerated: true`` makes the presigned URLs a browser or the CLI uses
(get_object, upload_part, put_object) carry ``<bucket>.s3-accelerate.amazonaws.com`` and a SigV4
credential scope for the bucket's Region. Everything else -- a bucket without the flag, every
server-side call -- is byte-for-byte what it was. The shipped module is loaded by file path because
``tests/conftest.py`` replaces ``common.s3`` with the mock; the mock is loaded too and the parity
class at the end asserts the two agree.
"""

import importlib.util
import os
import re
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
MOCK_PATH = os.path.join(os.path.dirname(__file__), "..", "mocks", "common", "s3.py")


def _load_by_path(module_name, path):
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def shipped(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("VAMS_LAMBDAS_IN_VPC", raising=False)
    return _load_by_path("_shipped_common_s3_accel", os.path.join(BACKEND, "common", "s3.py"))


@pytest.fixture
def mock_module(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("VAMS_LAMBDAS_IN_VPC", raising=False)
    return _load_by_path("_mock_common_s3_accel", MOCK_PATH)


def _presign_get(router, bucket, key="k"):
    return router.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60)


def _presign_part(router, bucket, key="k", upload_id="u1", part=1):
    return router.generate_presigned_url(
        ClientMethod="upload_part",
        Params={"Bucket": bucket, "Key": key, "PartNumber": part, "UploadId": upload_id},
        ExpiresIn=60)


def _table(items):
    table = MagicMock()
    table.query.return_value = {"Items": items}
    return table


@pytest.mark.unit
class TestFlagOff:
    """R8: a bucket without the flag signs exactly as before."""

    def test_unflagged_bucket_url_is_the_path_style_url(self, shipped):
        shipped.register_bucket_region("plain-b", "us-west-2")
        shipped.register_bucket_accelerated("plain-b", False)
        url = _presign_get(shipped.region_routing_s3_client(), "plain-b")
        assert url.startswith("https://s3.us-west-2.amazonaws.com/plain-b/k?")

    def test_unflagged_url_matches_the_plain_region_client(self, shipped):
        # The same URL as the per-Region client the router used before this change; only the
        # time-dependent query parameters (date, signature) are left out of the comparison.
        shipped.register_bucket_region("plain-b", "eu-west-1")
        shipped.register_bucket_accelerated("plain-b", False)
        routed = _presign_get(shipped.region_routing_s3_client(), "plain-b")
        direct = shipped.s3_client_for_region("eu-west-1").generate_presigned_url(
            "get_object", Params={"Bucket": "plain-b", "Key": "k"}, ExpiresIn=60)

        def stable(url):
            base, _, query = url.partition("?")
            kept = [p for p in query.split("&") if not p.startswith(("X-Amz-Date=", "X-Amz-Signature="))]
            return base, sorted(kept)

        assert stable(routed) == stable(direct)

    def test_the_non_accelerated_clients_keep_path_style(self, shipped):
        client = shipped.s3_client_for_region("eu-west-1")
        assert client.meta.config.s3["addressing_style"] == "path"
        assert "use_accelerate_endpoint" not in client.meta.config.s3

    def test_a_bucket_row_without_the_attribute_is_not_accelerated(self, shipped):
        shipped._buckets_table = _table([{"bucketName": "old-b", "bucketRegion": "us-west-2"}])
        assert shipped.bucket_is_accelerated("old-b") is False

    def test_bucket_details_without_the_flag_keep_their_shape(self, shipped):
        fields = shipped.bucket_region_fields(
            {"bucketName": "x", "bucketRegion": "ap-south-1", "bucketAccountId": "1"})
        assert fields == {"bucketRegion": "ap-south-1", "bucketAccountId": "1"}


@pytest.mark.unit
class TestFlagOn:
    def test_get_object_in_the_deployment_region_uses_the_accelerate_host(self, shipped):
        shipped.register_bucket_region("fast-b", "us-west-2")
        shipped.register_bucket_accelerated("fast-b", True)
        url = _presign_get(shipped.region_routing_s3_client(), "fast-b", "folder/a b.glb")
        assert url.startswith("https://fast-b.s3-accelerate.amazonaws.com/folder/a%20b.glb?")
        assert "us-west-2%2Fs3%2Faws4_request" in url

    def test_get_object_in_another_region_is_signed_for_that_region(self, shipped):
        shipped.register_bucket_region("far-b", "eu-west-1")
        shipped.register_bucket_accelerated("far-b", True)
        url = _presign_get(shipped.region_routing_s3_client(), "far-b")
        assert url.startswith("https://far-b.s3-accelerate.amazonaws.com/k?")
        assert "eu-west-1%2Fs3%2Faws4_request" in url
        assert "us-west-2%2Fs3" not in url

    @pytest.mark.parametrize("region", ["us-west-2", "eu-west-1"])
    def test_upload_part_uses_the_accelerate_host_and_the_bucket_region(self, shipped, region):
        shipped.register_bucket_region("fast-b", region)
        shipped.register_bucket_accelerated("fast-b", True)
        url = _presign_part(shipped.region_routing_s3_client(), "fast-b")
        assert url.startswith("https://fast-b.s3-accelerate.amazonaws.com/k?")
        assert f"{region}%2Fs3%2Faws4_request" in url
        assert "partNumber=1" in url and "uploadId=u1" in url

    def test_put_object_is_accelerated_too(self, shipped):
        shipped.register_bucket_region("fast-b", "us-west-2")
        shipped.register_bucket_accelerated("fast-b", True)
        url = shipped.region_routing_s3_client().generate_presigned_url(
            "put_object", Params={"Bucket": "fast-b", "Key": "k"}, ExpiresIn=60)
        assert ".s3-accelerate.amazonaws.com/k?" in url

    def test_the_svg_response_overrides_survive_acceleration(self, shipped):
        shipped.register_bucket_region("fast-b", "us-west-2")
        shipped.register_bucket_accelerated("fast-b", True)
        url = shipped.region_routing_s3_client().generate_presigned_url(
            "get_object",
            Params=shipped.presigned_get_object_params({"Bucket": "fast-b", "Key": "logo.svg"}),
            ExpiresIn=60)
        assert "response-content-type=image%2Fsvg%2Bxml" in url
        assert "response-content-disposition=attachment" in url

    def test_a_presign_of_another_operation_is_not_accelerated(self, shipped):
        shipped.register_bucket_region("fast-b", "us-west-2")
        shipped.register_bucket_accelerated("fast-b", True)
        url = shipped.region_routing_s3_client().generate_presigned_url(
            "head_object", Params={"Bucket": "fast-b", "Key": "k"}, ExpiresIn=60)
        assert url.startswith("https://s3.us-west-2.amazonaws.com/fast-b/k?")

    def test_the_accelerated_client_is_built_once_per_region(self, shipped):
        a = shipped.s3_accelerated_client_for_region("eu-west-1")
        assert a is shipped.s3_accelerated_client_for_region("eu-west-1")
        assert a is not shipped.s3_accelerated_client_for_region("us-west-2")
        assert a.meta.region_name == "eu-west-1"
        assert a.meta.config.s3["use_accelerate_endpoint"] is True
        assert a.meta.config.signature_version == "s3v4"

    def test_presigned_post_follows_the_flag(self, shipped):
        shipped.register_bucket_region("fast-b", "us-west-2")
        shipped.register_bucket_accelerated("fast-b", True)
        post = shipped.region_routing_s3_client().generate_presigned_post(
            Bucket="fast-b", Key="k", ExpiresIn=60)
        assert post["url"].startswith("https://fast-b.s3-accelerate.amazonaws.com")


@pytest.mark.unit
class TestServerSideCallsUnchanged:
    def test_other_operations_run_on_the_ordinary_client_even_for_a_flagged_bucket(self, shipped):
        shipped.register_bucket_region("fast-b", "eu-west-1")
        shipped.register_bucket_accelerated("fast-b", True)
        router = shipped.region_routing_s3_client()
        assert router._client_for("fast-b") is shipped.s3_client_for_region("eu-west-1")
        assert router._client_for("fast-b").meta.config.s3.get("use_accelerate_endpoint") is None

    def test_head_get_put_list_and_paginators_never_touch_the_accelerated_client(self, shipped, monkeypatch):
        shipped.register_bucket_region("fast-b", "us-west-2")
        shipped.register_bucket_accelerated("fast-b", True)
        ordinary = MagicMock()
        accelerated = MagicMock()
        router = shipped.region_routing_s3_client()
        monkeypatch.setattr(router, "_default_client", ordinary)
        monkeypatch.setattr(shipped, "s3_accelerated_client_for_region", lambda r=None: accelerated)
        router.head_object(Bucket="fast-b", Key="k")
        router.get_object(Bucket="fast-b", Key="k")
        router.put_object(Bucket="fast-b", Key="k", Body=b"")
        router.list_objects_v2(Bucket="fast-b")
        router.get_paginator("list_objects_v2").paginate(Bucket="fast-b")
        ordinary.head_object.assert_called_once()
        ordinary.get_object.assert_called_once()
        ordinary.put_object.assert_called_once()
        ordinary.list_objects_v2.assert_called_once()
        assert accelerated.mock_calls == []

    def test_an_unregistered_name_with_no_row_is_not_accelerated(self, shipped):
        shipped._buckets_table = _table([])
        url = _presign_get(shipped.region_routing_s3_client(), "artefacts-b")
        assert url.startswith("https://s3.us-west-2.amazonaws.com/artefacts-b/k?")


@pytest.mark.unit
class TestDottedBucketName:
    def test_the_helper_raises_a_clear_error(self, shipped):
        with pytest.raises(shipped.AccelerateNotSupportedError, match="period in its name"):
            shipped.accelerated_presign_client_for_bucket("my.bucket.name")

    def test_a_download_for_a_dotted_flagged_bucket_still_gets_a_url_and_the_fault_is_logged(self, shipped, monkeypatch):
        # Never a 500 on a download: the URL is signed without acceleration and the error is logged.
        shipped.register_bucket_region("my.dotted.b", "us-west-2")
        shipped.register_bucket_accelerated("my.dotted.b", True)
        logged = MagicMock()
        monkeypatch.setattr(shipped, "logger", logged)
        url = _presign_get(shipped.region_routing_s3_client(), "my.dotted.b")
        assert "s3-accelerate" not in url
        assert url.startswith("https://s3.us-west-2.amazonaws.com/my.dotted.b/k?")
        assert "period in its name" in logged.error.call_args.args[0]


@pytest.mark.unit
class TestFlagCache:
    def test_the_flag_is_read_through_the_bucket_name_gsi_and_cached(self, shipped):
        table = _table([{"bucketName": "fast-b", "bucketRegion": "us-west-2", "isAccelerated": True}])
        shipped._buckets_table = table
        assert shipped.bucket_is_accelerated("fast-b") is True
        assert shipped.bucket_is_accelerated("fast-b") is True
        assert table.query.call_count == 1
        assert table.query.call_args.kwargs["IndexName"] == "bucketNameGSI"

    def test_the_flag_expires_so_a_toggle_reaches_a_warm_lambda(self, shipped, monkeypatch):
        table = _table([{"bucketName": "fast-b", "isAccelerated": True}])
        shipped._buckets_table = table
        clock = {"now": 1000.0}
        monkeypatch.setattr(shipped.time, "monotonic", lambda: clock["now"])
        assert shipped.bucket_is_accelerated("fast-b") is True
        table.query.return_value = {"Items": [{"bucketName": "fast-b", "isAccelerated": False}]}
        clock["now"] += shipped.BUCKET_ACCELERATE_CACHE_TTL_SECONDS - 1
        assert shipped.bucket_is_accelerated("fast-b") is True  # still inside the window
        clock["now"] += 2
        assert shipped.bucket_is_accelerated("fast-b") is False  # expired, re-read
        assert table.query.call_count == 2

    def test_the_region_never_expires_but_the_flag_does(self, shipped):
        assert shipped.BUCKET_ACCELERATE_CACHE_TTL_SECONDS == 600

    def test_the_region_lookup_seeds_the_flag_from_the_same_row(self, shipped):
        table = _table([{"bucketName": "fast-b", "bucketRegion": "eu-west-1", "isAccelerated": True}])
        shipped._buckets_table = table
        assert shipped.bucket_region_for_name("fast-b") == "eu-west-1"
        assert shipped.bucket_is_accelerated("fast-b") is True
        assert table.query.call_count == 1

    def test_bucket_details_carry_and_register_the_flag(self, shipped):
        fields = shipped.bucket_region_fields(
            {"bucketName": "fast-b", "bucketRegion": "eu-west-1", "isAccelerated": True})
        assert fields == {"bucketRegion": "eu-west-1", "isAccelerated": True}
        shipped._buckets_table = None  # a table read here would fail the test
        assert shipped.bucket_is_accelerated("fast-b") is True


@pytest.mark.unit
class TestLookupFailure:
    def test_a_denied_read_is_logged_with_its_code_signs_without_acceleration_and_retries(self, shipped, monkeypatch):
        table = MagicMock()
        table.query.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Query")
        shipped._buckets_table = table
        shipped.register_bucket_region("fast-b", "us-west-2")
        logged = MagicMock()
        monkeypatch.setattr(shipped, "logger", logged)
        url = _presign_get(shipped.region_routing_s3_client(), "fast-b")
        assert url.startswith("https://s3.us-west-2.amazonaws.com/fast-b/k?")  # download still works
        assert "AccessDeniedException" in logged.error.call_args.args[0]
        # Not cached: the next call reads again and, once the read works, accelerates.
        table.query.side_effect = None
        table.query.return_value = {"Items": [{"bucketName": "fast-b", "isAccelerated": True}]}
        assert ".s3-accelerate.amazonaws.com/k?" in _presign_get(shipped.region_routing_s3_client(), "fast-b")
        assert table.query.call_count == 2

    def test_a_non_client_failure_is_logged_and_not_cached(self, shipped, monkeypatch):
        table = MagicMock()
        table.query.side_effect = RuntimeError("boom")
        shipped._buckets_table = table
        logged = MagicMock()
        monkeypatch.setattr(shipped, "logger", logged)
        assert shipped.bucket_is_accelerated("fast-b") is False
        assert "RuntimeError" in logged.error.call_args.args[0]
        assert "fast-b" not in shipped._bucket_accelerated_by_name


@pytest.mark.unit
class TestAuxiliaryBucketFollowsDefault:
    AUX = "vams-aux-bucket"

    def _wire(self, shipped, monkeypatch, default_row, default_flag):
        rows = {self.AUX: [], "default-b": default_row}
        table = MagicMock()

        def query(**kwargs):
            name = kwargs["KeyConditionExpression"]._values[1]
            return {"Items": rows.get(name, [])}

        table.query.side_effect = query
        shipped._buckets_table = table
        monkeypatch.setattr(shipped, "_is_auxiliary_bucket", lambda name: name == self.AUX)
        import common.workflows.defaultBucket as default_bucket
        monkeypatch.setattr(default_bucket, "resolve_default_bucket",
                            lambda t: {"bucketId": "b", "bucketName": "default-b", "baseAssetsPrefix": ""})
        return table

    def test_aux_is_accelerated_when_the_effective_default_bucket_is(self, shipped, monkeypatch):
        self._wire(shipped, monkeypatch, [{"bucketName": "default-b", "isAccelerated": True}], True)
        assert shipped.bucket_is_accelerated(self.AUX) is True

    def test_aux_is_not_accelerated_when_the_default_bucket_is_not(self, shipped, monkeypatch):
        self._wire(shipped, monkeypatch, [{"bucketName": "default-b"}], False)
        assert shipped.bucket_is_accelerated(self.AUX) is False

    def test_aux_presigned_download_uses_the_accelerate_host_in_the_deployment_region(self, shipped, monkeypatch):
        self._wire(shipped, monkeypatch, [{"bucketName": "default-b", "isAccelerated": True}], True)
        url = _presign_get(shipped.region_routing_s3_client(), self.AUX, "vams.preparedDownloads/z.zip")
        assert url.startswith(f"https://{self.AUX}.s3-accelerate.amazonaws.com/vams.preparedDownloads/z.zip?")
        assert "us-west-2%2Fs3" in url

    def test_a_name_that_is_neither_registered_nor_aux_is_not_accelerated(self, shipped, monkeypatch):
        self._wire(shipped, monkeypatch, [{"bucketName": "default-b", "isAccelerated": True}], True)
        assert shipped.bucket_is_accelerated("artefacts-or-staging-b") is False

    def test_no_default_bucket_row_means_not_accelerated(self, shipped, monkeypatch):
        self._wire(shipped, monkeypatch, [], False)
        import common.workflows.defaultBucket as default_bucket

        def none_flagged(t):
            raise default_bucket.DefaultBucketNotFoundError("none")

        monkeypatch.setattr(default_bucket, "resolve_default_bucket", none_flagged)
        assert shipped.bucket_is_accelerated(self.AUX) is False


@pytest.mark.unit
class TestUploadPartUrlBound:
    def test_the_fixed_overhead_constant_still_bounds_an_accelerated_upload_part_url(self, shipped, monkeypatch):
        """models/assetsV3.UPLOAD_PART_URL_FIXED_OVERHEAD_BYTES is measured for a path-style URL with
        a 1,300-character session token, a 63-character bucket, a 256-character uploadId and a
        7-day expiry. The accelerate host is shorter than the regional path-style host."""
        import boto3
        from botocore.config import Config
        sys.path.insert(0, BACKEND)
        with open(os.path.join(BACKEND, "models", "assetsV3.py")) as handle:
            constant = int(re.search(r"^UPLOAD_PART_URL_FIXED_OVERHEAD_BYTES = (\d+)", handle.read(), re.M).group(1))
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ASIA" + "A" * 16)
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s" * 40)
        monkeypatch.setenv("AWS_SESSION_TOKEN", "t" * 1300)
        bucket = "b" * 63
        shipped.register_bucket_region(bucket, "ap-southeast-2")
        shipped.register_bucket_accelerated(bucket, True)
        accelerated = shipped.s3_accelerated_client_for_region("ap-southeast-2")
        key = "k"
        url = accelerated.generate_presigned_url(
            "upload_part", Params={"Bucket": bucket, "Key": key, "PartNumber": 10000, "UploadId": "u" * 256},
            ExpiresIn=7 * 24 * 3600)
        assert ".s3-accelerate.amazonaws.com/" in url
        assert len(url) - len(key) <= constant
        plain = shipped.s3_client_for_region("ap-southeast-2").generate_presigned_url(
            "upload_part", Params={"Bucket": bucket, "Key": key, "PartNumber": 10000, "UploadId": "u" * 256},
            ExpiresIn=7 * 24 * 3600)
        assert len(url) <= len(plain)


@pytest.mark.unit
class TestMockParity:
    SURFACE = ["bucket_is_accelerated", "register_bucket_accelerated", "s3_accelerated_client_for_region",
               "accelerated_presign_client_for_bucket", "AccelerateNotSupportedError",
               "BUCKET_ACCELERATE_CACHE_TTL_SECONDS", "ACCELERATED_PRESIGN_METHODS"]

    def test_the_mock_exposes_the_surface(self, mock_module):
        assert [n for n in self.SURFACE if not hasattr(mock_module, n)] == []

    def test_the_constants_match_the_shipped_module(self, mock_module, shipped):
        assert mock_module.BUCKET_ACCELERATE_CACHE_TTL_SECONDS == shipped.BUCKET_ACCELERATE_CACHE_TTL_SECONDS
        assert mock_module.ACCELERATED_PRESIGN_METHODS == shipped.ACCELERATED_PRESIGN_METHODS

    def test_the_mock_presigns_like_the_shipped_module(self, mock_module, shipped):
        for module in (mock_module, shipped):
            module.register_bucket_region("far-b", "eu-west-1")
            module.register_bucket_accelerated("far-b", True)
            module.register_bucket_region("plain-b", "eu-west-1")
            module.register_bucket_accelerated("plain-b", False)
            fast = _presign_get(module.region_routing_s3_client(), "far-b")
            assert fast.startswith("https://far-b.s3-accelerate.amazonaws.com/k?")
            assert "eu-west-1%2Fs3" in fast
            slow = _presign_get(module.region_routing_s3_client(), "plain-b")
            assert slow.startswith("https://s3.eu-west-1.amazonaws.com/plain-b/k?")

    def test_the_mock_falls_back_for_a_dotted_name(self, mock_module):
        mock_module.register_bucket_region("a.b", "us-west-2")
        mock_module.register_bucket_accelerated("a.b", True)
        assert "s3-accelerate" not in _presign_get(mock_module.region_routing_s3_client(), "a.b")

    def test_bucket_details_agree(self, mock_module, shipped):
        row = {"bucketName": "fast-b", "bucketRegion": "eu-west-1", "isAccelerated": True}
        assert mock_module.bucket_region_fields(row) == shipped.bucket_region_fields(row)
        plain = {"bucketName": "p", "bucketRegion": "eu-west-1"}
        assert mock_module.bucket_region_fields(plain) == shipped.bucket_region_fields(plain)
