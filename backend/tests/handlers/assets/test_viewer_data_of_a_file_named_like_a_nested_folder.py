# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file's own viewer data stays the file's when an asset is stored in a folder named like it (#1877).

``outer`` (database db1) at ``p/outer/`` holds ``scans/own.e57`` and ``scans/b.txt``. ``kit`` (same database) is
stored at ``p/outer/scans/own.e57/``, a folder named like ``own.e57``, and holds ``k.e57``. In the auxiliary
bucket ``own.e57``'s viewer data is ``db1/p/outer/scans/own.e57/preview/...`` and ``k.e57``'s is
``db1/p/outer/scans/own.e57/k.e57/preview/...``: both under kit's location. The nested-asset rule
(``NestedLocations.keep_aux``) decides by the file a key is the viewer data of (``viewer_data_file_key``, the same
boundary as ``viewer_data_scope``), so ``own.e57``'s data is outer's and ``k.e57``'s is kit's:

-   the auxiliary stream route serves ``own.e57``'s data through outer, and still refuses ``k.e57``'s through
    outer and serves it through kit;
-   the asset permanent delete of outer, a folder delete of ``/scans/`` and the auxiliary preview delete of
    ``/scans/`` remove ``own.e57``'s data and keep ``k.e57``'s.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)
from tests.handlers.assets.test_aux_delete_all_versions import _fresh_asset_service  # noqa: E402
from tests.handlers.assets.test_streamAuxiliaryPreviewAsset_range_header_casing import (  # noqa: E402
    _load as _load_stream, _rest_event, _wire,
)

al = sys.modules["common.assetLocations"]
fa = sys.modules["common.fileAttribution"]

BUCKET = "vams-named"
AUX = "vams-named-aux"
LOCATION = "p/outer/"
OUTER = dict(asset_row("db1", "outer", "reg-1", LOCATION, BUCKET), isDistributable=True)
KIT = dict(asset_row("db1", "kit", "reg-1", "p/outer/scans/own.e57/", BUCKET), isDistributable=True)
OTHER_DB = asset_row("db2", "far", "reg-1", "p/outer/scans/b.txt/", BUCKET)
ASSET_KEYS = ("p/outer/scans/own.e57", "p/outer/scans/b.txt", "p/outer/scans/own.e57/k.e57")
OWN = "db1/p/outer/scans/own.e57/preview/PotreeViewer/metadata.json"
PLAIN = "db1/p/outer/scans/b.txt/preview/PotreeViewer/metadata.json"
KIT_DATA = "db1/p/outer/scans/own.e57/k.e57/preview/PotreeViewer/metadata.json"
CLAIMS = {"tokens": ["tester"], "roles": []}
# A GET is answered with a redirect to the object (this deployment's delivery mode), a HEAD with 200
SERVED = (200, 307)


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    al.clear_nested_locations_cache()
    fa.clear_registration_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield
    al.clear_nested_locations_cache()


def _nested(rows, asset=OUTER, destructive=True):
    return fa.nested_locations_of_asset(AttributionAssetTable(rows), AttributionBucketsTable(
        [registration("reg-1", BUCKET, "/")]), asset, BUCKET, destructive=destructive)


@pytest.mark.unit
class TestTheRule:
    @pytest.mark.parametrize("aux_key,file_key", [
        (OWN, "p/outer/scans/own.e57"),
        (KIT_DATA, "p/outer/scans/own.e57/k.e57"),
        # A file in a folder named preview, and a file named preview, below a file of that name
        ("db1/p/x.txt/preview/a.glb/preview/PotreeViewer/m.json", "p/x.txt/preview/a.glb"),
        ("db1/p/x.txt/preview/preview/m.json", "p/x.txt/preview"),
        ("db1/p/x.txt/notes.json", None),
        ("db2/p/x.txt/preview/m.json", None),
    ])
    def test_the_file_a_key_is_the_viewer_data_of(self, aux_key, file_key):
        assert al.viewer_data_file_key("db1", aux_key) == file_key

    def test_the_files_own_data_is_the_outer_assets_and_the_nested_files_the_nested_assets(self):
        nested = _nested([OUTER, KIT])
        assert nested.keep_aux("db1", OWN) is True
        assert nested.keep_aux("db1", PLAIN) is True
        assert nested.keep_aux("db1", KIT_DATA) is False
        assert nested.keep_aux("db1", "db1/p/outer/scans/own.e57/preview/k2.glb/preview/m.json") is False

    def test_the_rule_is_ownership_whatever_the_nested_assets_database(self):
        nested = _nested([OUTER, OTHER_DB])
        assert nested.keep_aux("db1", PLAIN) is True
        assert nested.keep_aux("db1", "db1/p/outer/scans/b.txt/x.obj/preview/m.json") is False

    def test_a_key_without_a_viewer_data_folder_is_read_as_a_path(self):
        nested = _nested([OUTER, KIT])
        assert nested.keep_aux("db1", "db1/p/outer/scans/own.e57/legacy.bin") is False
        assert nested.keep_aux("db1", "db1/p/outer/scans/legacy.bin") is True


@pytest.fixture
def stream(monkeypatch):
    m = _load_stream()
    mock_s3 = _wire(m)
    mock_s3.head_object.return_value = {"ContentType": "application/octet-stream", "ContentLength": 2048}
    monkeypatch.setattr(m, "asset_table", AttributionAssetTable([OUTER, KIT]))
    state = SimpleNamespace(m=m, s3=mock_s3, asset=OUTER)
    monkeypatch.setattr(m, "get_asset_details", MagicMock(side_effect=lambda db, asset: dict(state.asset)))
    return state


def _stream(state, asset_id, path, method="GET"):
    event = _rest_event({"Range": "bytes=0-99"} if method == "GET" else {})
    event["httpMethod"] = method
    event["requestContext"]["httpMethod"] = method
    event["pathParameters"] = {"databaseId": "db1", "assetId": asset_id, "proxy": path}
    return state.m.lambda_handler(event, None)


@pytest.mark.unit
class TestTheViewers:
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_the_outer_files_viewer_reads_its_own_data(self, stream, method):
        assert _stream(stream, "outer", "scans/own.e57/preview/PotreeViewer/metadata.json", method)["statusCode"] \
            in SERVED
        called = stream.s3.get_object if method == "GET" else stream.s3.head_object
        assert called.call_args.kwargs["Key"] == OWN

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_the_nested_assets_data_is_still_refused_through_the_outer_asset(self, stream, method):
        assert _stream(stream, "outer", "scans/own.e57/k.e57/preview/PotreeViewer/metadata.json",
                       method)["statusCode"] == 404
        stream.s3.get_object.assert_not_called()
        stream.s3.head_object.assert_not_called()

    def test_the_nested_assets_viewer_reads_its_data(self, stream):
        """Control: served before #1877 too."""
        stream.asset = KIT
        assert _stream(stream, "kit", "k.e57/preview/PotreeViewer/metadata.json")["statusCode"] in SERVED
        assert stream.s3.get_object.call_args.kwargs["Key"] == KIT_DATA


@pytest.fixture
def aux_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        for bucket in (BUCKET, AUX):
            client.create_bucket(Bucket=bucket)
            client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        for key in ASSET_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
        for key in (OWN, PLAIN, KIT_DATA):
            client.put_object(Bucket=AUX, Key=key, Body=b"viewer data")
        yield client


def _aux_keys(client):
    listed = client.list_object_versions(Bucket=AUX)
    return sorted({entry["Key"] for entry in listed.get("Versions", []) + listed.get("DeleteMarkers", [])})


@pytest.mark.unit
class TestTheAssetPermanentDelete:
    def test_the_outer_assets_delete_removes_the_files_own_data_and_keeps_the_nested_assets(self, aux_bucket):
        m = _fresh_asset_service()
        with patch.object(m, "s3", aux_bucket), patch.object(m, "s3_assetAuxiliary_bucket", AUX):
            m.delete_assetAuxiliary_files("db1", {"Key": LOCATION}, nested=_nested([OUTER, KIT]))

        assert _aux_keys(aux_bucket) == [KIT_DATA]


@pytest.fixture
def files(aux_bucket, monkeypatch):
    m = assetFiles
    monkeypatch.setattr(m, "asset_table", AttributionAssetTable([OUTER, KIT]))
    monkeypatch.setattr(m, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
    monkeypatch.setattr(m, "s3_client", aux_bucket)
    monkeypatch.setattr(m, "asset_aux_bucket_name", AUX)
    monkeypatch.setattr(m, "get_asset_with_permissions", lambda *args, **kwargs: dict(OUTER))
    monkeypatch.setattr(m, "get_default_bucket_details",
                        lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": "/"})
    monkeypatch.setattr(m, "MAX_PARALLEL_S3_WORKERS", 1)
    for name in ("send_subscription_email", "delete_file_metadata", "delete_files_metadata",
                 "delete_file_version_records"):
        monkeypatch.setattr(m, name, MagicMock(name=name))
    return SimpleNamespace(m=m, client=aux_bucket)


@pytest.mark.unit
class TestTheFolderDeletes:
    def test_a_folder_delete_removes_the_files_own_data_and_keeps_the_nested_assets(self, files):
        files.m.delete_file("db1", "outer", "/scans/", True, True, CLAIMS)

        assert _aux_keys(files.client) == [KIT_DATA]
        remaining = files.client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
        assert [obj["Key"] for obj in remaining] == ["p/outer/scans/own.e57/k.e57"]

    def test_the_auxiliary_preview_delete_of_a_folder_removes_the_files_own_data(self, files):
        response = files.m.delete_auxiliary_preview_asset_files("db1", "outer", "/scans/", CLAIMS)

        assert response.deletedCount == 2
        assert _aux_keys(files.client) == [KIT_DATA]

    def test_the_partial_cleanup_removes_the_files_own_data(self, files):
        """Control: the cleanup of a partial permanent delete reads each file's own folder (#1870)."""
        files.m.delete_auxiliary_files_of_keys("db1", ["p/outer/scans/own.e57"])

        assert _aux_keys(files.client) == sorted([KIT_DATA, PLAIN])
