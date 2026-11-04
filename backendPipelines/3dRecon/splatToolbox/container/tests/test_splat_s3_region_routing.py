#  SPDX-License-Identifier: Apache-2.0

"""3dRecon/splatToolbox container: S3 requests are signed for each asset bucket's Region (D3).

The pipeline definition carries a bucket name -> Region map (``bucketRegions``). The entry point
registers it in ``vams_utils.aws.s3`` (its own manifest reads and the output check) and hands it to
``main.py``'s process, where the launcher replaces a bare ``boto3.client('s3')`` -- the client the
upstream ``main.py`` downloads S3_INPUT and uploads S3_OUTPUT with -- by a client whose every call
runs on the client for the Region of the bucket it names. ``main.py`` is upstream-synced, so this
hook is the only durable place for it."""

import importlib.util
import json
import os
import sys
from unittest.mock import ANY, MagicMock

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder

import boto3  # noqa: E402

from vams_utils.aws import s3  # noqa: E402


@pytest.fixture
def s3mod():
    saved_regions, saved_clients = dict(s3._bucket_regions), dict(s3._clients_by_region)
    s3._bucket_regions.clear()
    s3._clients_by_region.clear()
    yield s3
    s3._bucket_regions.clear()
    s3._bucket_regions.update(saved_regions)
    s3._clients_by_region.clear()
    s3._clients_by_region.update(saved_clients)


@pytest.fixture
def entry():
    """The real entry module (vams_utils.aws.s3 is the real helper; manifest_io is stubbed)."""
    stubbed = {}
    for name in ("vams_utils.manifest_io",):
        if name not in sys.modules:
            stubbed[name] = MagicMock()
    sys.modules.update(stubbed)
    try:
        spec = importlib.util.spec_from_file_location(
            "splat_container_main_regions", os.path.join(_CONTAINER_DIR, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return module


@pytest.fixture
def restore_boto3_client():
    original = boto3.client
    yield
    boto3.client = original


@pytest.mark.unit
class TestClientForBucket:
    def test_an_unregistered_bucket_uses_the_default_client(self, s3mod):
        assert s3mod.client_for_bucket("aux-bucket") is s3mod.client
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
        assert s3mod.client_for_bucket("remote-bkt") is regional

    def test_a_bucket_in_the_default_region_keeps_the_default_client(self, s3mod):
        s3mod.register_bucket_regions({"local-bkt": s3mod.client.meta.region_name})
        assert s3mod.client_for_bucket("local-bkt") is s3mod.client

    def test_download_runs_on_the_bucket_region_client(self, s3mod, tmp_path, monkeypatch):
        default_client, regional = MagicMock(name="default"), MagicMock(name="eu-west-1")
        monkeypatch.setattr(s3mod, "client", default_client)
        s3mod._clients_by_region["eu-west-1"] = regional
        s3mod.register_bucket_regions({"remote-bkt": "eu-west-1"})
        target = str(tmp_path / "scan.zip")

        assert s3mod.download("remote-bkt", "x/scan.zip", target) == target
        regional.download_fileobj.assert_called_once_with("remote-bkt", "x/scan.zip", ANY)
        default_client.download_fileobj.assert_not_called()


@pytest.mark.unit
class TestRegionRoutingClient:
    """The stand-in for upstream main.py's one `boto3.client('s3')`."""

    @pytest.fixture
    def routed(self, s3mod, monkeypatch):
        default_client, regional = MagicMock(name="default"), MagicMock(name="eu-west-1")
        monkeypatch.setattr(s3mod, "client", default_client)
        s3mod._clients_by_region["eu-west-1"] = regional
        s3mod.register_bucket_regions({"remote-bkt": "eu-west-1"})
        return s3mod.region_routing_client(), default_client, regional

    def test_transfer_methods_route_on_their_positional_bucket(self, routed):
        router, default_client, regional = routed
        router.download_file("remote-bkt", "k", "/tmp/x")
        router.upload_file("/tmp/x", "remote-bkt", "k")
        router.download_file("aux-bkt", "k", "/tmp/y")
        regional.download_file.assert_called_once_with("remote-bkt", "k", "/tmp/x")
        regional.upload_file.assert_called_once_with("/tmp/x", "remote-bkt", "k")
        default_client.download_file.assert_called_once_with("aux-bkt", "k", "/tmp/y")
        default_client.upload_file.assert_not_called()

    def test_api_operations_route_on_their_bucket_keyword(self, routed):
        router, default_client, regional = routed
        router.put_object(Bucket="remote-bkt", Key="k", Body=b"x")
        router.head_object(Bucket="aux-bkt", Key="k")
        regional.put_object.assert_called_once_with(Bucket="remote-bkt", Key="k", Body=b"x")
        default_client.head_object.assert_called_once_with(Bucket="aux-bkt", Key="k")

    def test_a_presigned_url_routes_on_the_params_bucket(self, routed):
        router, default_client, regional = routed
        router.generate_presigned_url("get_object", Params={"Bucket": "remote-bkt", "Key": "k"})
        regional.generate_presigned_url.assert_called_once()
        default_client.generate_presigned_url.assert_not_called()

    def test_paginators_route_on_the_paginate_bucket(self, routed):
        router, default_client, regional = routed
        router.get_paginator("list_objects_v2").paginate(Bucket="remote-bkt", Prefix="p/")
        regional.get_paginator.assert_called_once_with("list_objects_v2")
        regional.get_paginator.return_value.paginate.assert_called_once_with(Bucket="remote-bkt", Prefix="p/")
        default_client.get_paginator.assert_not_called()

    def test_bucketless_calls_and_attributes_go_to_the_default_client(self, routed):
        router, default_client, regional = routed
        router.list_buckets()
        default_client.list_buckets.assert_called_once_with()
        assert router.meta is default_client.meta
        assert router.exceptions is default_client.exceptions
        regional.list_buckets.assert_not_called()


@pytest.mark.unit
class TestLauncherHook:
    def test_the_launcher_installs_the_hook_before_running_main(self, entry):
        launcher = entry._GUARDED_MAIN_LAUNCHER
        hook = launcher.index("install_region_routing_s3_clients")
        assert hook < launcher.index("runpy.run_path(")
        assert "_entry.BUCKET_REGIONS_ENV" in launcher

    def test_a_bare_s3_client_becomes_the_routing_client(self, entry, s3mod, restore_boto3_client):
        original = boto3.client
        registered = entry.install_region_routing_s3_clients(json.dumps({"remote-bkt": "eu-west-1"}))
        assert registered == {"remote-bkt": "eu-west-1"}
        assert s3mod._bucket_regions == {"remote-bkt": "eu-west-1"}
        assert boto3.client is not original
        assert isinstance(boto3.client("s3"), s3mod.RegionRoutingClient)
        # An explicit Region or endpoint, or another service, is built exactly as asked
        pinned = boto3.client("s3", region_name="us-west-2")
        assert not isinstance(pinned, s3mod.RegionRoutingClient)
        assert pinned.meta.region_name == "us-west-2"
        sfn = boto3.client("stepfunctions", region_name="us-west-2")
        assert sfn.meta.service_model.service_name == "stepfunctions"

    def test_no_map_leaves_boto3_untouched(self, entry, s3mod, restore_boto3_client):
        original = boto3.client
        assert entry.install_region_routing_s3_clients("") == {}
        assert entry.install_region_routing_s3_clients(json.dumps({})) == {}
        assert boto3.client is original
        assert s3mod._bucket_regions == {}


@pytest.mark.unit
class TestEntryHandsTheMapToMainPy:
    def test_the_entry_module_registers_and_exports_the_map(self, entry):
        source_path = os.path.join(_CONTAINER_DIR, "__main__.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        main_body = source[source.index("def main():"):]
        assert "bucket_regions = pipeline_def.get('bucketRegions') or {}" in main_body
        assert "vams_s3.register_bucket_regions(bucket_regions)" in main_body
        assert "os.environ[BUCKET_REGIONS_ENV] = json.dumps(bucket_regions)" in main_body
        # The output check runs on the output bucket's own Region client
        assert "vams_s3.client_for_bucket(output_bucket)" in main_body
        assert "boto3.client('s3', region_name=region" not in main_body
