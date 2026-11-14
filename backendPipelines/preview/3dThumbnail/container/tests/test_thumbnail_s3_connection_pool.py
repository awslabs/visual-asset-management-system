#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail container: every S3 client pools as many connections as one transfer runs
requests at once.

A download runs up to four ranged GETs per CPU, at most 32 (``download_transfer_config``). On botocore's
default pool of 10 connections, urllib3 discards the connection of each concurrent request beyond the pool
("Connection pool is full") and the next request reconnects: a 16 vCPU render job logged one warning per
discarded connection on every large file it fetched. The input download, the analysis branch's
referenced-file walk (``ClientStore``) and the preview upload each run on a client whose pool covers the
TransferConfig they pass."""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

from preview_pipeline import dependencies
from preview_pipeline.utils import s3_utils as s3

HANDLER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lambda_handler.py")
REMOTE_REGION = "eu-west-1"
# Task sizes from one CPU to well past the Fargate maximum, so the widest download is the helper's ceiling.
CPU_COUNTS = (1, 2, 3, 4, 8, 16, 64, 256)


def _pool(client):
    return client.meta.config.max_pool_connections


def _widest_download():
    return max(s3.download_transfer_config(cpus=cpus).max_concurrency for cpus in CPU_COUNTS)


@pytest.fixture
def regional_client():
    saved_regions, saved_clients = dict(s3._bucket_regions), dict(s3._clients_by_region)
    s3._bucket_regions.clear()
    s3._clients_by_region.clear()
    s3.register_bucket_regions({"remote-bkt": REMOTE_REGION})
    yield s3.client_for_bucket("remote-bkt")
    s3._bucket_regions.clear()
    s3._bucket_regions.update(saved_regions)
    s3._clients_by_region.clear()
    s3._clients_by_region.update(saved_clients)


@pytest.fixture(scope="module")
def handler():
    spec = importlib.util.spec_from_file_location("render3d_lambda_handler_pool_under_test", HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestClientsPoolTheTransferConcurrency:
    def test_the_widest_download_is_the_module_ceiling(self):
        assert _widest_download() == s3.MAX_TRANSFER_CONCURRENCY

    def test_the_default_client_pools_the_widest_download(self):
        assert _pool(s3.client) >= _widest_download()

    def test_a_regional_client_pools_the_widest_download(self, regional_client):
        assert regional_client is not s3.client
        assert regional_client.meta.region_name == REMOTE_REGION
        assert _pool(regional_client) >= _widest_download()

    def test_the_resource_client_pools_the_widest_download(self):
        assert _pool(s3.s3.meta.client) >= _widest_download()


@pytest.mark.unit
class TestTransfersRunWithinThePool:
    def test_the_input_download_passes_the_parallel_config(self, tmp_path, monkeypatch):
        real_client, recorder = s3.client, MagicMock(name="client")
        monkeypatch.setattr(s3, "client", recorder)

        with patch("preview_pipeline.resources.cpu_count", return_value=64):
            s3.download("aux-bkt", "xid/scan.e57", str(tmp_path / "scan.e57"))
        config = recorder.download_file.call_args.kwargs["Config"]
        assert config.max_concurrency == _widest_download()
        assert config.max_concurrency <= _pool(real_client)

    def test_the_analysis_walk_downloads_on_a_client_whose_pool_covers_its_config(self, handler, tmp_path):
        stores = []
        options, _ = handler.load_render_options("")
        request = handler.Render3dRequest("s3://aux-bkt/E1/analysis.json", "s3://asset-bkt/xid/model.obj", "",
                                          "mesh", ".obj")

        def _resolve(store, *args, **kwargs):
            stores.append(store)

        with patch("preview_pipeline.resources.cpu_count", return_value=64), \
                patch.object(dependencies, "resolve", side_effect=_resolve):
            handler.resolve_dependencies(request, options, "asset-bkt", "xid/model.obj",
                                         str(tmp_path / "model.obj"), str(tmp_path), "xid/", None)
        (store,) = stores
        assert store.client is s3.client
        assert store.transfer_config.max_concurrency == _widest_download()
        assert store.transfer_config.max_concurrency <= _pool(store.client)

    def test_the_store_passes_its_config_to_each_download(self, tmp_path):
        client, config = MagicMock(name="client"), s3.download_transfer_config(cpus=64)
        target = str(tmp_path / "wood.png")

        store = dependencies.ClientStore(client, "asset-bkt", config)
        assert store.download("xid/tex/wood.png", "v1", target)
        client.download_file.assert_called_once_with("asset-bkt", "xid/tex/wood.png", target,
                                                     ExtraArgs={"VersionId": "v1"}, Config=config)

    def test_the_preview_upload_runs_within_the_pool(self, tmp_path, monkeypatch):
        real_client, recorder = s3.client, MagicMock(name="client")
        monkeypatch.setattr(s3, "client", recorder)
        preview = tmp_path / "model.obj.previewFile.gif"
        preview.write_bytes(b"GIF89a")

        s3.upload("aux-bkt", "xid/model.obj.previewFile.gif", str(preview))
        assert recorder.upload_file.call_args.kwargs["Config"].max_concurrency <= _pool(real_client)
