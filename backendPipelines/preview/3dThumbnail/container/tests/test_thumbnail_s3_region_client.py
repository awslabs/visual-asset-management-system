#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail container: S3 requests are signed for each asset bucket's Region (D3).

The pipeline definition carries a bucket name -> Region map (``bucketRegions``); ``core.run``
registers it before any S3 call, and every request for a registered bucket is signed with a client
for that Region (adaptive retries, regional us-east-1 endpoint, no endpoint_url). Buckets the map
leaves out -- the auxiliary bucket, a definition from an older Lambda -- stay on the default client.

The transfer helpers #473 added (the parallel ``download_file``, the resumable streaming GET, the
referenced-file HeadObject) run on the same per-bucket client, so a cross-Region input is
downloaded, streamed and has its referenced files fetched with requests signed for its Region; the
``core.run`` cases follow a definition's map through the existing-preview listing, the size check,
the download and the referenced-file fetch."""

import os
import shutil
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from boto3.exceptions import S3TransferFailedError
from botocore.exceptions import ClientError, ReadTimeoutError

from preview_pipeline import core, dependencies
from preview_pipeline.utils import s3_utils as s3  # noqa: E402
from preview_pipeline.utils.pipeline.objects import PipelineStatus

REMOTE_REGION = "eu-west-1"


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
        regional.download_file.assert_called_once_with("remote-bkt", "x/scan.bin", target, Config=ANY)
        default_client.download_file.assert_not_called()

    def test_download_from_an_unregistered_bucket_stays_on_the_default_client(
            self, s3mod, tmp_path, monkeypatch):
        default_client = MagicMock(name="default")
        monkeypatch.setattr(s3mod, "client", default_client)
        target = str(tmp_path / "scan.bin")

        assert s3mod.download("aux-bkt", "x/scan.bin", target) == target
        default_client.download_file.assert_called_once_with("aux-bkt", "x/scan.bin", target, Config=ANY)

    def test_the_regional_download_keeps_the_parallel_transfer_config(self, s3mod, tmp_path, monkeypatch):
        regional = _route(s3mod, monkeypatch, MagicMock(name=REMOTE_REGION))[1]

        with patch("preview_pipeline.resources.cpu_count", return_value=4):
            s3mod.download("remote-bkt", "x/scan.bin", str(tmp_path / "scan.bin"))
        config = regional.download_file.call_args.kwargs["Config"]
        assert (config.max_concurrency, config.multipart_chunksize) == (16, 64 * 1024 ** 2)

    def test_a_failed_regional_transfer_is_reported_as_no_file(self, s3mod, tmp_path, monkeypatch):
        regional = MagicMock(name=REMOTE_REGION)
        regional.download_file.side_effect = S3TransferFailedError("connection reset")
        _route(s3mod, monkeypatch, regional)

        assert s3mod.download("remote-bkt", "x/scan.bin", str(tmp_path / "scan.bin")) is None
        regional.download_file.assert_called_once()


def _route(s3mod, monkeypatch, regional):
    """Register remote-bkt in REMOTE_REGION with ``regional`` as that Region's client. Returns
    (default client, regional client); the default is a mock that must stay unused for remote-bkt."""
    default_client = MagicMock(name="default")
    monkeypatch.setattr(s3mod, "client", default_client)
    s3mod._clients_by_region[REMOTE_REGION] = regional
    s3mod.register_bucket_regions({"remote-bkt": REMOTE_REGION})
    return default_client, regional


class _Body:
    def __init__(self, chunks, fail_after=None):
        self.chunks, self.fail_after = chunks, fail_after

    def iter_chunks(self, size):
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index == self.fail_after:
                raise ReadTimeoutError(endpoint_url="s3")
            yield chunk

    def close(self):
        pass


@pytest.mark.unit
class TestStreamRoutesByBucketRegion:
    def test_the_stream_and_its_ranged_resume_run_on_the_bucket_region_client(self, s3mod, monkeypatch):
        regional = MagicMock(name=REMOTE_REGION)
        regional.get_object.side_effect = [{"Body": _Body([b"abc", b"def", b"x"], fail_after=2)},
                                           {"Body": _Body([b"ghi"])}]
        default_client, _ = _route(s3mod, monkeypatch, regional)

        assert b"".join(s3mod.iter_object_bytes("remote-bkt", "x/scan.ptx")) == b"abcdefghi"
        first, resumed = regional.get_object.call_args_list
        assert first.kwargs == {"Bucket": "remote-bkt", "Key": "x/scan.ptx"}
        assert resumed.kwargs == {"Bucket": "remote-bkt", "Key": "x/scan.ptx", "Range": "bytes=6-"}
        default_client.get_object.assert_not_called()


@pytest.mark.unit
class TestHeadObjectSizeRoutesByBucketRegion:
    def test_the_size_comes_from_the_bucket_region_client(self, s3mod, monkeypatch):
        regional = MagicMock(name=REMOTE_REGION)
        regional.head_object.return_value = {"ContentLength": 42}
        default_client, _ = _route(s3mod, monkeypatch, regional)

        assert s3mod.head_object_size("remote-bkt", "x/model.mtl") == 42
        regional.head_object.assert_called_once_with(Bucket="remote-bkt", Key="x/model.mtl")
        default_client.head_object.assert_not_called()

    def test_a_missing_object_in_a_remote_bucket_is_none(self, s3mod, monkeypatch):
        regional = MagicMock(name=REMOTE_REGION)
        regional.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        default_client, _ = _route(s3mod, monkeypatch, regional)

        assert s3mod.head_object_size("remote-bkt", "x/missing.png") is None
        regional.head_object.assert_called_once()
        default_client.head_object.assert_not_called()


@pytest.mark.unit
class TestUploadRoutesByBucketRegion:
    def test_the_preview_is_uploaded_with_the_bucket_region_client(self, s3mod, tmp_path, monkeypatch):
        default_client, regional = _route(s3mod, monkeypatch, MagicMock(name=REMOTE_REGION))
        preview = tmp_path / "model.obj.previewFile.gif"
        preview.write_bytes(b"GIF89a")

        assert s3mod.upload("remote-bkt", "xid/model.obj.previewFile.gif", str(preview)) == \
            "xid/model.obj.previewFile.gif"
        regional.upload_file.assert_called_once_with(
            str(preview), "remote-bkt", "xid/model.obj.previewFile.gif",
            ExtraArgs={}, Config=ANY, Callback=ANY)
        default_client.upload_file.assert_not_called()


class RegionalBucketClient:
    """One Region's S3 client: serves ``<root>/<bucket>/<key>`` and records every request with the
    bucket it named."""

    def __init__(self, root, region=REMOTE_REGION):
        self.root = str(root)
        self.meta = SimpleNamespace(region_name=region)
        self.requests = []

    def put(self, bucket, key, data):
        path = os.path.join(self.root, bucket, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if isinstance(data, bytes) else "w") as handle:
            handle.write(data)

    def head_object(self, Bucket, Key):
        self.requests.append(("HeadObject", Bucket, Key))
        path = os.path.join(self.root, Bucket, Key)
        if not os.path.isfile(path):
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": os.path.getsize(path)}

    def download_file(self, Bucket, Key, Filename, Config=None):
        self.requests.append(("GetObject", Bucket, Key))
        shutil.copyfile(os.path.join(self.root, Bucket, Key), Filename)

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        return SimpleNamespace(paginate=self._list)

    def _list(self, Bucket, Prefix):
        self.requests.append(("ListObjectsV2", Bucket, Prefix))
        base = os.path.join(self.root, Bucket)
        keys = sorted(os.path.relpath(os.path.join(directory, name), base).replace(os.sep, "/")
                      for directory, _, names in os.walk(base) for name in names)
        return [{"Contents": [{"Key": key} for key in keys if key.startswith(Prefix)]}]


OBJ = "mtllib ../materials/model.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl wood\nf 1 2 3\n"
MTL = "newmtl wood\nKd 1 1 1\nmap_Kd tex/wood.png\n"


def _remote_asset(tmp_path):
    regional = RegionalBucketClient(tmp_path / "eu-west-1")
    regional.put("remote-bkt", "xid/models/model.obj", OBJ)
    regional.put("remote-bkt", "xid/materials/model.mtl", MTL)
    regional.put("remote-bkt", "xid/materials/tex/wood.png", b"png")
    return regional


@pytest.mark.unit
class TestReferencedFilesRouteByBucketRegion:
    def test_the_references_of_a_remote_input_are_sized_and_fetched_in_its_region(
            self, s3mod, tmp_path, monkeypatch):
        default_client, regional = _route(s3mod, monkeypatch, _remote_asset(tmp_path))
        local_root = str(tmp_path / "local")
        local_input = os.path.join(local_root, "models", "model.obj")
        os.makedirs(os.path.dirname(local_input))
        shutil.copyfile(os.path.join(regional.root, "remote-bkt", "xid/models/model.obj"), local_input)

        report = dependencies.fetch_referenced_files("remote-bkt", "xid/models/model.obj", local_input,
                                                     local_root, "xid/", ".obj")

        assert sorted(report.fetched) == ["materials/model.mtl", "materials/tex/wood.png"]
        assert regional.requests == [
            ("HeadObject", "remote-bkt", "xid/materials/model.mtl"),
            ("GetObject", "remote-bkt", "xid/materials/model.mtl"),
            ("HeadObject", "remote-bkt", "xid/materials/tex/wood.png"),
            ("GetObject", "remote-bkt", "xid/materials/tex/wood.png"),
        ]
        assert default_client.method_calls == []


def _definition(bucket_regions):
    output = {"bucketName": "remote-bkt", "objectDir": "pipelines/preview3dThumbnail/JOB/output/E1/files/"}
    definition = {
        "jobName": "PipelineJob_x",
        "stages": [{
            "type": "PREVIEW_3D_THUMBNAIL",
            "inputFile": {"bucketName": "remote-bkt", "objectKey": "xid/models/model.obj",
                          "fileExtension": ".obj"},
            "outputFiles": output, "outputMetadata": output, "temporaryFiles": output,
        }],
        "externalSfnTaskToken": "tok",
        "localTest": "False",
        "assetId": "xid",
    }
    if bucket_regions is not None:
        definition["bucketRegions"] = bucket_regions
    return definition


def _run_until_load(tmp_path, definition):
    """core.run on ``definition``, stopped at the load: everything before it -- the existing-preview
    listing, the size check, the download, the referenced-file fetch -- runs. Returns the run's
    output and, per load, the files present under the local asset root."""
    def _create_dir(parts):
        path = os.path.join(str(tmp_path / "work"), *parts)
        os.makedirs(path, exist_ok=True)
        return path

    loaded = []

    def _load_file(file_path, ext, asset_root=None, budgets=None):
        loaded.append(sorted(os.path.relpath(os.path.join(directory, name), asset_root).replace(os.sep, "/")
                             for directory, _, names in os.walk(asset_root) for name in names))
        raise RuntimeError("stopped after the fetch")

    with patch.object(core, "_create_dir", _create_dir), \
            patch.object(core, "_load_file", _load_file), \
            patch.object(core, "sfn", MagicMock()):
        output = core.run(definition)
    return output, loaded


@pytest.mark.unit
class TestRunRoutesTheDefinitionMap:
    def test_every_request_for_a_remote_input_runs_in_its_region(self, s3mod, tmp_path, monkeypatch):
        default_client, regional = _route(s3mod, monkeypatch, _remote_asset(tmp_path))
        s3mod._bucket_regions.clear()  # core.run registers the definition's map itself

        output, loaded = _run_until_load(tmp_path, _definition({"remote-bkt": REMOTE_REGION}))

        assert output.status == PipelineStatus.FAILED  # the stopped load, not a failed download
        assert loaded == [["materials/model.mtl", "materials/tex/wood.png", "models/model.obj"]]
        assert {(operation, key) for operation, _, key in regional.requests} == {
            ("ListObjectsV2", "xid/models/model.obj.previewFile."),
            ("HeadObject", "xid/models/model.obj"),
            ("GetObject", "xid/models/model.obj"),
            ("HeadObject", "xid/materials/model.mtl"),
            ("GetObject", "xid/materials/model.mtl"),
            ("HeadObject", "xid/materials/tex/wood.png"),
            ("GetObject", "xid/materials/tex/wood.png"),
        }
        assert {bucket for _, bucket, _ in regional.requests} == {"remote-bkt"}
        assert default_client.method_calls == []

    def test_a_definition_without_the_map_stays_on_the_default_client(self, s3mod, tmp_path, monkeypatch):
        default_client = _remote_asset(tmp_path)
        unused_regional = MagicMock(name=REMOTE_REGION)
        monkeypatch.setattr(s3mod, "client", default_client)
        s3mod._clients_by_region[REMOTE_REGION] = unused_regional

        output, loaded = _run_until_load(tmp_path, _definition(None))

        assert loaded == [["materials/model.mtl", "materials/tex/wood.png", "models/model.obj"]]
        assert ("GetObject", "remote-bkt", "xid/materials/tex/wood.png") in default_client.requests
        assert unused_regional.method_calls == []
