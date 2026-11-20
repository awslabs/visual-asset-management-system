# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset archive, unarchive and permanent delete of a single-file asset touch the keys it holds only.

A single-file asset (location key with no trailing ``/``) holds its object, its companion folder
``{file key}/`` and its own previews. Used as an S3 prefix, its key also matches keys that only share its
characters (``model.glb.bak``, ``model.glbx/...``). Asset archive lists that prefix and archives what it
finds, and permanent delete removes every version of every key it lists, so both reached objects outside
the asset. The archive and unarchive paths run against a versioned moto bucket here; the permanent delete
is checked at the boundary it hands the location to, ``delete_s3_prefix_all_versions``, whose own
behaviour test_assetFiles_single_file_location.py covers.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from tests.handlers.assets.test_assetService_history import _asset
from tests.handlers.assets.test_assetService_permanent_delete_notification import _prepare

os.environ.setdefault("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME", "test-history-table")

_ASSET_SERVICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetService.py"
)

BUCKET = "vams-test-assets"
FILE_LOCATION = "p1/model.glb"
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COMPANION = "p1/model.glb/inner.txt"
OUTSIDE_KEYS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt")
FOLDER_LOCATION = "proj/a/"
SIBLING_FOLDER_KEYS = ("proj/a-other/y.txt", "proj/a.bak")

_cached_module = None


def _load():
    """A private copy of the real assetService module, loaded by path with its handler imports stubbed.

    Other assetService suites replace functions on their own cached copy, so this file keeps its own.
    """
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = ("handlers.assets.assetCount", "handlers.assets.assetFiles", "handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}

    count_stub = types.ModuleType("handlers.assets.assetCount")
    count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = count_stub
    files_stub = types.ModuleType("handlers.assets.assetFiles")
    files_stub.delete_s3_prefix_all_versions = MagicMock()
    files_stub.S3PrefixDeleteIncomplete = type("S3PrefixDeleteIncomplete", (Exception,), {})
    files_stub.delete_files_metadata = MagicMock()
    files_stub.delete_auxiliary_files_of_keys = MagicMock()
    files_stub.delete_file_version_records = MagicMock()
    files_stub.aux_bucket_asset_file_base = lambda db, key: f"{(db or '').strip('/')}/{(key or '').strip('/')}/"
    sys.modules["handlers.assets.assetFiles"] = files_stub
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"]})
    sys.modules["handlers.auth"] = auth_stub

    dynamodb_mod = sys.modules.get("common.dynamodb")
    added = dynamodb_mod is not None and not hasattr(dynamodb_mod, "validate_pagination_info")
    if added:
        dynamodb_mod.validate_pagination_info = MagicMock()
    try:
        with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(
                "assetService_single_file_location_under_test", os.path.abspath(_ASSET_SERVICE_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
        if added:
            delattr(dynamodb_mod, "validate_pagination_info")
    _cached_module = module
    return module


def _live(client, key):
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


@pytest.fixture
def svc(monkeypatch):
    """assetService against a versioned moto bucket holding a single-file asset beside outside keys."""
    m = _load()
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in (FILE_LOCATION, OWN_PREVIEW, COMPANION) + OUTSIDE_KEYS + ("proj/a/", "proj/a/x.txt") + SIBLING_FOLDER_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")
        history = MagicMock()
        monkeypatch.setattr(m, "s3", client)
        monkeypatch.setattr(m, "asset_file_version_history_table", history)
        # moto serves one request at a time reliably; the handler's worker pool is not under test
        monkeypatch.setattr(m, "MAX_PARALLEL_S3_WORKERS", 1)
        m.test_client = client
        yield m


def _history_items(history):
    return [c.kwargs["Item"] for c in history.put_item.call_args_list]


@pytest.mark.unit
class TestAssetArchive:
    def test_archive_of_a_single_file_asset_archives_the_keys_it_holds_only(self, svc):
        svc.archive_multi_assetFiles({"Key": FILE_LOCATION}, BUCKET, "db1", "a1", "tester")

        for key in (FILE_LOCATION, OWN_PREVIEW, COMPANION):
            assert not _live(svc.test_client, key), key
        for key in OUTSIDE_KEYS:
            assert _live(svc.test_client, key), key
        items = _history_items(svc.asset_file_version_history_table)
        assert sorted(i["filePath"] for i in items) == ["/inner.txt", "/model.glb", "/model.glb.previewFile.png"]
        assert {i["changeSource"] for i in items} == {"assetArchive"}

    def test_archive_of_a_single_file_asset_reads_no_key_that_only_shares_its_characters(self, svc, monkeypatch):
        listed = []
        real = svc.test_client.get_paginator

        class _Spy:
            def __init__(self, name):
                self.inner = real(name)

            def paginate(self, **kwargs):
                listed.append((kwargs["Prefix"], (kwargs.get("PaginationConfig") or {}).get("MaxItems")))
                return self.inner.paginate(**kwargs)

        monkeypatch.setattr(svc.test_client, "get_paginator", _Spy)
        svc.archive_multi_assetFiles({"Key": FILE_LOCATION}, BUCKET, "db1", "a1", "tester")
        assert listed == [(FILE_LOCATION, 1), (FILE_LOCATION + ".previewFile.", None), (FILE_LOCATION + "/", None)]
        assert not _live(svc.test_client, FILE_LOCATION) and not _live(svc.test_client, COMPANION)

    def test_control_a_folder_asset_archives_its_folder_and_no_sibling_prefix(self, svc):
        svc.archive_multi_assetFiles({"Key": FOLDER_LOCATION}, BUCKET, "db1", "a2", "tester")

        assert not _live(svc.test_client, "proj/a/x.txt")
        for key in SIBLING_FOLDER_KEYS:
            assert _live(svc.test_client, key), key
        assert [i["filePath"] for i in _history_items(svc.asset_file_version_history_table)] == ["/x.txt"]


@pytest.mark.unit
class TestAssetUnarchive:
    def _archive(self, svc, key):
        return svc.test_client.delete_object(Bucket=BUCKET, Key=key)["VersionId"]

    def _recorded(self, svc, records):
        """The asset's assetArchive history: (filePath, delete-marker VersionId) pairs."""
        svc.asset_file_version_history_table.query.return_value = {"Items": [
            {"changeSource": "assetArchive", "filePath": path, "versionId": version} for path, version in records]}

    @pytest.mark.parametrize("own_path,preview_path", [
        ("/model.glb", "/model.glb.previewFile.png"),
        ("/", "/.previewFile.png"),  # recorded relative to the bare file key
    ])
    def test_unarchive_restores_the_object_and_preview_however_recorded(self, svc, own_path, preview_path):
        own = self._archive(svc, FILE_LOCATION)
        preview = self._archive(svc, OWN_PREVIEW)
        companion = self._archive(svc, COMPANION)
        self._recorded(svc, [(own_path, own), (preview_path, preview), ("/inner.txt", companion)])

        restored = svc.unarchive_multi_assetFiles({"Key": FILE_LOCATION}, BUCKET, "db1", "a1", "tester")

        assert restored == 3
        for key in (FILE_LOCATION, OWN_PREVIEW, COMPANION):
            assert _live(svc.test_client, key), key
        restored_items = [i for i in _history_items(svc.asset_file_version_history_table)
                          if i["changeSource"] == "assetUnarchive"]
        assert sorted(i["filePath"] for i in restored_items) == [
            "/inner.txt", "/model.glb", "/model.glb.previewFile.png"]

    def test_unarchive_restores_no_key_outside_the_asset(self, svc):
        own = self._archive(svc, FILE_LOCATION)
        outside = self._archive(svc, "p1/model.glb.bak")
        # A record that names the outside key under this asset (relative to the bare location key).
        self._recorded(svc, [("/", own), ("/.bak", outside)])

        restored = svc.unarchive_multi_assetFiles({"Key": FILE_LOCATION}, BUCKET, "db1", "a1", "tester")

        assert restored == 1
        assert _live(svc.test_client, FILE_LOCATION)
        assert not _live(svc.test_client, "p1/model.glb.bak")

    def test_control_a_folder_asset_restores_its_recorded_files(self, svc):
        marker = self._archive(svc, "proj/a/x.txt")
        self._recorded(svc, [("/x.txt", marker)])

        assert svc.unarchive_multi_assetFiles({"Key": FOLDER_LOCATION}, BUCKET, "db1", "a2", "tester") == 1
        assert _live(svc.test_client, "proj/a/x.txt")


@pytest.mark.unit
class TestPermanentDelete:
    def test_permanent_delete_bounds_each_listing_to_its_location(self, monkeypatch):
        m = _load()
        preview_key = "previews/a1/thumb.png"
        _prepare(m, monkeypatch, _asset(db="db1#deleted", status="archived",
                                        assetLocation={"Key": FILE_LOCATION},
                                        previewLocation={"Key": preview_key}))
        request = MagicMock()
        request.confirmPermanentDelete = True

        m.delete_asset_permanent("db1#deleted", "a1", request, {"tokens": ["u1"]})

        calls = [(c.args, c.kwargs) for c in m.delete_s3_prefix_all_versions.call_args_list]
        assert calls == [
            (("bucket", FILE_LOCATION), {"location": FILE_LOCATION}),
            (("bucket", preview_key), {"location": preview_key}),
        ]
