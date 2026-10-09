# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-Region S3 clients for asset buckets in other Regions (common.s3).

A presigned URL or an API call signed for the deployment Region against a bucket in another
Region fails (``AuthorizationHeaderMalformed``; path-style addressing with the regional
us-east-1 endpoint makes the Region part of every request). ``common.s3`` therefore builds one
client per Region and routes every asset-bucket operation to the client for the Region of the
bucket it names, and the handlers hold a ``RegionRoutingS3Client`` instead of a plain client.

``tests/conftest.py`` replaces ``common.s3`` with ``tests/mocks/common/s3.py``, so the shipped
module is loaded here by file path (the same way ``test_shared_client_retry_config`` does) and the
mock is loaded alongside it: every handler test reaches this contract through the mock, so the two
have to agree on it, and the parity class at the end asserts that they do.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.config import Config

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
    return _load_by_path("_shipped_common_s3_regions", os.path.join(BACKEND, "common", "s3.py"))


@pytest.fixture
def mock_module(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("VAMS_LAMBDAS_IN_VPC", raising=False)
    return _load_by_path("_mock_common_s3_regions", MOCK_PATH)


@pytest.mark.unit
class TestClientFactory:
    def test_the_shipped_module_is_loaded_not_its_mock(self, shipped):
        assert shipped.__file__.replace("\\", "/").endswith("backend/backend/common/s3.py")

    def test_one_client_per_region_cached(self, shipped):
        a = shipped.s3_client_for_region("eu-west-1")
        b = shipped.s3_client_for_region("eu-west-1")
        c = shipped.s3_client_for_region("ap-south-1")
        assert a is b
        assert a is not c
        assert a.meta.region_name == "eu-west-1"
        assert c.meta.region_name == "ap-south-1"

    def test_the_default_region_is_the_deployment_region(self, shipped):
        assert shipped.s3_client_for_region().meta.region_name == "us-west-2"
        assert shipped.deployment_region() == "us-west-2"

    def test_clients_carry_sigv4_path_style_and_the_rule_6_retries(self, shipped):
        client = shipped.s3_client_for_region("eu-west-1")
        config = client.meta.config
        assert config.signature_version == "s3v4"
        assert config.s3 and config.s3.get("addressing_style") == "path"
        assert config.retries.get("mode") == "adaptive"
        assert config.retries.get("total_max_attempts") == 6
        assert config.max_pool_connections == shipped.MAX_PARALLEL_S3_WORKERS

    def test_no_endpoint_url_is_set(self, shipped):
        # Partition rule 2: the SDK resolves the endpoint per Region.
        client = shipped.s3_client_for_region("eu-west-1")
        assert client.meta.endpoint_url == "https://s3.eu-west-1.amazonaws.com"

    def test_us_east_1_uses_the_regional_endpoint(self, shipped):
        # Without AWS_S3_US_EAST_1_REGIONAL_ENDPOINT=regional boto3 builds s3.amazonaws.com, the
        # global name an interface endpoint's private DNS does not cover.
        assert os.environ.get("AWS_S3_US_EAST_1_REGIONAL_ENDPOINT") == "regional"
        client = shipped.s3_client_for_region("us-east-1")
        url = client.generate_presigned_url(
            "get_object", Params={"Bucket": "b", "Key": "k"}, ExpiresIn=60)
        assert url.startswith("https://s3.us-east-1.amazonaws.com/b/k")


@pytest.mark.unit
class TestBucketRegionHelpers:
    def test_bucket_region_reads_the_row_and_defaults_to_the_deployment_region(self, shipped):
        assert shipped.bucket_region({"bucketRegion": "eu-west-1"}) == "eu-west-1"
        assert shipped.bucket_region({"bucketRegion": ""}) == "us-west-2"
        assert shipped.bucket_region({}) == "us-west-2"
        assert shipped.bucket_region(None) == "us-west-2"

    def test_bucket_region_fields_shape(self, shipped):
        assert shipped.bucket_region_fields(
            {"bucketName": "b", "bucketRegion": "eu-west-1", "bucketAccountId": "222222222222"}
        ) == {"bucketRegion": "eu-west-1", "bucketAccountId": "222222222222"}
        assert shipped.bucket_region_fields({"bucketName": "b"}) == {"bucketRegion": "us-west-2"}

    def test_bucket_region_fields_registers_the_name(self, shipped):
        shipped.bucket_region_fields({"bucketName": "remote-b", "bucketRegion": "eu-west-1"})
        assert shipped.bucket_region_for_name("remote-b") == "eu-west-1"

    def test_s3_client_for_bucket_uses_the_details_region(self, shipped):
        client = shipped.s3_client_for_bucket({"bucketName": "remote-b", "bucketRegion": "eu-west-1"})
        assert client.meta.region_name == "eu-west-1"
        assert shipped.s3_client_for_bucket({"bucketName": "local-b"}).meta.region_name == "us-west-2"

    def test_an_unknown_name_falls_back_to_the_deployment_region_without_raising(self, shipped):
        # The table lookup fails here (no table); the miss is remembered, not retried per call.
        with patch.object(shipped, "_lookup_bucket_region", wraps=shipped._lookup_bucket_region) as lookup:
            with patch.object(shipped.boto3, "resource", side_effect=RuntimeError("no dynamodb")):
                assert shipped.bucket_region_for_name("never-registered") == "us-west-2"
                assert shipped.bucket_region_for_name("never-registered") == "us-west-2"
            assert lookup.call_count == 2
        assert "never-registered" in shipped._bucket_regions_misses

    def test_the_table_lookup_reads_the_bucket_name_gsi(self, shipped):
        table = MagicMock()
        table.query.return_value = {"Items": [{"bucketName": "gsi-b", "bucketRegion": "ap-south-1"}]}
        shipped._buckets_table = table
        assert shipped.bucket_region_for_name("gsi-b") == "ap-south-1"
        kwargs = table.query.call_args.kwargs
        assert kwargs["IndexName"] == "bucketNameGSI"
        assert kwargs["Limit"] == 1
        # Cached: a second resolution does not query again.
        assert shipped.bucket_region_for_name("gsi-b") == "ap-south-1"
        assert table.query.call_count == 1

    def test_presigned_urls_are_signed_for_the_bucket_region(self, shipped):
        details = {"bucketName": "remote-b", "bucketRegion": "eu-west-1"}
        url = shipped.s3_client_for_bucket(details).generate_presigned_url(
            "get_object", Params={"Bucket": "remote-b", "Key": "k"}, ExpiresIn=60)
        assert url.startswith("https://s3.eu-west-1.amazonaws.com/remote-b/k")
        assert "eu-west-1%2Fs3" in url  # X-Amz-Credential scope


@pytest.mark.unit
class TestRegionRoutingClient:
    def test_dispatches_by_bucket_kwarg(self, shipped):
        shipped.register_bucket_region("remote-b", "eu-west-1")
        router = shipped.region_routing_s3_client()
        remote = MagicMock(); local = MagicMock()
        with patch.object(shipped, "s3_client_for_region", side_effect=lambda r=None: remote if r == "eu-west-1" else local):
            router._default_client = local
            router.head_object(Bucket="remote-b", Key="k")
            router.head_object(Bucket="local-b", Key="k")
            router.list_buckets()
        remote.head_object.assert_called_once_with(Bucket="remote-b", Key="k")
        local.head_object.assert_called_once_with(Bucket="local-b", Key="k")
        local.list_buckets.assert_called_once_with()

    def test_dispatches_presigned_url_by_params_bucket(self, shipped):
        shipped.register_bucket_region("remote-b", "eu-west-1")
        router = shipped.region_routing_s3_client()
        url = router.generate_presigned_url(
            "get_object", Params={"Bucket": "remote-b", "Key": "k"}, ExpiresIn=60)
        assert url.startswith("https://s3.eu-west-1.amazonaws.com/remote-b/k")

    def test_paginator_dispatches_by_bucket(self, shipped):
        shipped.register_bucket_region("remote-b", "eu-west-1")
        router = shipped.region_routing_s3_client()
        remote = MagicMock()
        remote.get_paginator.return_value.paginate.return_value = iter([{"Contents": []}])
        with patch.object(shipped, "s3_client_for_region", return_value=remote):
            pages = list(router.get_paginator("list_objects_v2").paginate(Bucket="remote-b", Prefix="p/"))
        assert pages == [{"Contents": []}]
        remote.get_paginator.assert_called_once_with("list_objects_v2")

    def test_exceptions_and_meta_come_from_the_default_client(self, shipped):
        router = shipped.region_routing_s3_client()
        assert router.exceptions.NoSuchKey is router._default_client.exceptions.NoSuchKey
        assert router.meta.region_name == "us-west-2"

    def test_the_default_client_is_bound_at_construction(self, shipped):
        # A handler builds its router at import, where tests stub boto3.client; that stub must be
        # what the router holds, as a plain module-level client would.
        stub = MagicMock()
        with patch.object(shipped.boto3, "client", return_value=stub):
            router = shipped.region_routing_s3_client()
        router.head_object(Bucket="anything", Key="k")
        stub.head_object.assert_called_once_with(Bucket="anything", Key="k")

    def test_copy_goes_through_copy_s3_object(self, shipped):
        router = shipped.region_routing_s3_client()
        with patch.object(shipped, "copy_s3_object") as copy:
            router.copy(CopySource={"Bucket": "a", "Key": "k", "VersionId": "v1"}, Bucket="b", Key="k2",
                        ExtraArgs={"ACL": "bucket-owner-full-control"})
        copy.assert_called_once_with("a", "k", "b", "k2", extra_args={"ACL": "bucket-owner-full-control"},
                                     source_version_id="v1")

    def test_resource_object_copy_goes_through_copy_s3_object(self, shipped):
        resource = shipped.region_routing_s3_resource()
        with patch.object(shipped, "copy_s3_object") as copy:
            resource.Object("b", "k2").copy({"Bucket": "a", "Key": "k"}, ExtraArgs={"ContentType": "x/y"})
            resource.meta.client.copy(CopySource={"Bucket": "a", "Key": "k"}, Bucket="b", Key="k3")
        assert copy.call_args_list[0].args == ("a", "k", "b", "k2")
        assert copy.call_args_list[0].kwargs == {"extra_args": {"ContentType": "x/y"}, "source_version_id": None}
        assert copy.call_args_list[1].args == ("a", "k", "b", "k3")


@pytest.mark.unit
class TestCopySelection:
    """copy_s3_object: a managed copy by the destination Region's client, except an in-VPC
    cross-Region copy, which streams (S3 interface endpoints do not serve CopyObject across
    Regions)."""

    def _clients(self, shipped, source_region, dest_region):
        shipped.register_bucket_region("src", source_region)
        shipped.register_bucket_region("dst", dest_region)
        clients = {source_region: MagicMock(name=source_region), dest_region: MagicMock(name=dest_region)}
        return clients, patch.object(shipped, "s3_client_for_region", side_effect=lambda r=None: clients[r or "us-west-2"])

    def test_same_region_is_a_managed_copy_by_the_destination_client(self, shipped, monkeypatch):
        monkeypatch.setenv("VAMS_LAMBDAS_IN_VPC", "true")
        clients, p = self._clients(shipped, "us-west-2", "us-west-2")
        with p:
            shipped.copy_s3_object("src", "k", "dst", "k2", extra_args={"ACL": "bucket-owner-full-control"})
        clients["us-west-2"].copy.assert_called_once_with(
            CopySource={"Bucket": "src", "Key": "k"}, Bucket="dst", Key="k2",
            ExtraArgs={"ACL": "bucket-owner-full-control"}, SourceClient=clients["us-west-2"])
        clients["us-west-2"].get_object.assert_not_called()

    def test_cross_region_outside_the_vpc_is_a_managed_copy_with_the_source_client(self, shipped):
        clients, p = self._clients(shipped, "eu-west-1", "us-west-2")
        with p:
            shipped.copy_s3_object("src", "k", "dst", "k2", source_version_id="v9")
        clients["us-west-2"].copy.assert_called_once_with(
            CopySource={"Bucket": "src", "Key": "k", "VersionId": "v9"}, Bucket="dst", Key="k2",
            ExtraArgs=None, SourceClient=clients["eu-west-1"])
        clients["eu-west-1"].copy.assert_not_called()

    def test_cross_region_inside_the_vpc_streams_get_to_put(self, shipped, monkeypatch):
        monkeypatch.setenv("VAMS_LAMBDAS_IN_VPC", "true")
        clients, p = self._clients(shipped, "eu-west-1", "us-west-2")
        body = MagicMock(name="body")
        clients["eu-west-1"].get_object.return_value = {
            "Body": body, "Metadata": {"assetid": "a1"}, "ContentType": "model/gltf-binary",
            "ContentDisposition": "inline"}
        with p:
            shipped.copy_s3_object("src", "k", "dst", "k2", extra_args={"ACL": "bucket-owner-full-control"})
        clients["us-west-2"].copy.assert_not_called()
        clients["eu-west-1"].get_object.assert_called_once_with(Bucket="src", Key="k")
        clients["us-west-2"].upload_fileobj.assert_called_once_with(
            body, "dst", "k2",
            ExtraArgs={"ACL": "bucket-owner-full-control", "Metadata": {"assetid": "a1"},
                       "ContentType": "model/gltf-binary", "ContentDisposition": "inline"})

    def test_streamed_copy_with_replace_directive_uses_the_given_metadata_only(self, shipped, monkeypatch):
        monkeypatch.setenv("VAMS_LAMBDAS_IN_VPC", "true")
        clients, p = self._clients(shipped, "eu-west-1", "us-west-2")
        clients["eu-west-1"].get_object.return_value = {
            "Body": MagicMock(), "Metadata": {"stale": "x"}, "ContentType": "model/gltf-binary"}
        with p:
            shipped.copy_s3_object("src", "k", "dst", "k2", extra_args={
                "MetadataDirective": "REPLACE", "Metadata": {"assetid": "a2"},
                "ContentType": "application/octet-stream", "ACL": "bucket-owner-full-control"})
        extra = clients["us-west-2"].upload_fileobj.call_args.kwargs["ExtraArgs"]
        assert extra == {"Metadata": {"assetid": "a2"}, "ContentType": "application/octet-stream",
                         "ACL": "bucket-owner-full-control"}
        assert "MetadataDirective" not in extra

    def test_lambdas_in_vpc_reads_the_env_var(self, shipped, monkeypatch):
        assert shipped.lambdas_in_vpc() is False
        monkeypatch.setenv("VAMS_LAMBDAS_IN_VPC", "TRUE")
        assert shipped.lambdas_in_vpc() is True
        monkeypatch.setenv("VAMS_LAMBDAS_IN_VPC", "false")
        assert shipped.lambdas_in_vpc() is False


@pytest.mark.unit
class TestMockParity:
    """The mock every handler test runs against carries the same surface and the same routing and
    copy decisions as the shipped module."""

    SURFACE = ["deployment_region", "lambdas_in_vpc", "s3_client_for_region", "s3_resource_for_region",
               "bucket_region", "bucket_region_fields", "register_bucket_region", "bucket_region_for_name",
               "s3_client_for_bucket", "s3_resource_for_bucket", "s3_client_for_bucket_name",
               "s3_resource_for_bucket_name", "copy_s3_object", "RegionRoutingS3Client",
               "RegionRoutingS3Resource", "region_routing_s3_client", "region_routing_s3_resource",
               "s3_asset_client", "s3_asset_resource", "S3_ASSET_CLIENT_CONFIG", "MAX_PARALLEL_S3_WORKERS"]

    def test_the_mock_exposes_the_surface(self, mock_module):
        missing = [name for name in self.SURFACE if not hasattr(mock_module, name)]
        assert missing == []

    def test_the_mock_routes_and_falls_back_like_the_shipped_module(self, mock_module):
        mock_module.register_bucket_region("remote-b", "eu-west-1")
        assert mock_module.bucket_region_for_name("remote-b") == "eu-west-1"
        assert mock_module.bucket_region_for_name("unknown") == "us-west-2"
        assert mock_module.bucket_region_fields({"bucketName": "x", "bucketRegion": "ap-south-1",
                                                 "bucketAccountId": "1"}) == \
            {"bucketRegion": "ap-south-1", "bucketAccountId": "1"}
        router = mock_module.region_routing_s3_client()
        url = router.generate_presigned_url("get_object", Params={"Bucket": "remote-b", "Key": "k"}, ExpiresIn=60)
        assert url.startswith("https://s3.eu-west-1.amazonaws.com/remote-b/k")

    def test_the_mock_streams_in_vpc_cross_region_copies(self, mock_module, monkeypatch):
        monkeypatch.setenv("VAMS_LAMBDAS_IN_VPC", "true")
        mock_module.register_bucket_region("src", "eu-west-1")
        mock_module.register_bucket_region("dst", "us-west-2")
        clients = {"eu-west-1": MagicMock(), "us-west-2": MagicMock()}
        clients["eu-west-1"].get_object.return_value = {"Body": MagicMock(), "Metadata": {}}
        with patch.object(mock_module, "s3_client_for_region", side_effect=lambda r=None: clients[r or "us-west-2"]):
            mock_module.copy_s3_object("src", "k", "dst", "k2")
        clients["us-west-2"].upload_fileobj.assert_called_once()
        clients["us-west-2"].copy.assert_not_called()
