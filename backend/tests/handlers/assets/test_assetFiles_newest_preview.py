# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file with several ``.previewFile.<ext>`` objects shows its newest one (#466).

A 3D Preview Thumbnail run with another output format writes ``<file>.previewFile.png`` beside an
existing ``<file>.previewFile.gif``; neither the run nor process-output removes the other format. The
listing used to attach the first preview in key order (``.gif`` before ``.jpg`` before ``.png``), so
the asset kept showing the old preview. Every listing path -- the current listing, a version
snapshot in basic and detailed mode, and the single-file info -- now attaches the newest one.
"""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-level import ensures the real backend.backend.handlers.assets package is populated in
# sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

_DB = "db1"
_ASSET = "asset1"
_PREFIX = f"{_ASSET}/"
_OLD = "2026-10-01T10:00:00+00:00"
_NEW = "2026-10-16T10:00:00+00:00"


def _af():
    from backend.backend.handlers.assets import assetFiles as af
    return af


def _item(relative, modified, archived=False):
    return {
        "fileName": relative.rsplit("/", 1)[-1],
        "key": _PREFIX + relative,
        "relativePath": "/" + relative,
        "isFolder": False,
        "dateCreatedCurrentVersion": modified,
        "storageClass": "STANDARD",
        "isArchived": archived,
    }


@pytest.mark.unit
class TestSelectNewestPreviewFile:
    def test_the_newest_preview_wins_over_key_order(self):
        af = _af()
        candidates = [("a/m.glb.previewFile.gif", _OLD, False), ("a/m.glb.previewFile.png", _NEW, False)]
        assert af.select_newest_preview_file(candidates) == "a/m.glb.previewFile.png"
        assert af.select_newest_preview_file(list(reversed(candidates))) == "a/m.glb.previewFile.png"

    def test_equal_times_keep_the_first_in_key_order(self):
        af = _af()
        candidates = [("m.glb.previewFile.png", _NEW, False), ("m.glb.previewFile.gif", _NEW, False)]
        assert af.select_newest_preview_file(candidates) == "m.glb.previewFile.gif"

    def test_a_live_preview_beats_a_newer_archived_one(self):
        af = _af()
        candidates = [("m.glb.previewFile.gif", _OLD, False), ("m.glb.previewFile.png", _NEW, True)]
        assert af.select_newest_preview_file(candidates) == "m.glb.previewFile.gif"

    def test_datetimes_and_unreadable_values_are_ordered(self):
        af = _af()
        newer = datetime(2026, 10, 16, tzinfo=timezone.utc)
        assert af.select_newest_preview_file([
            ("m.glb.previewFile.gif", "not a date", False),
            ("m.glb.previewFile.jpg", newer, False),
            ("m.glb.previewFile.png", None, False),
        ]) == "m.glb.previewFile.jpg"
        # A naive timestamp is read as UTC, so it compares with an offset-aware one.
        assert af.select_newest_preview_file([
            ("m.glb.previewFile.gif", "2026-10-16T11:00:00", False),
            ("m.glb.previewFile.png", "2026-10-16T10:00:00Z", False),
        ]) == "m.glb.previewFile.gif"

    def test_a_disallowed_extension_is_never_attached(self):
        af = _af()
        assert af.select_newest_preview_file([("m.glb.previewFile.exe", _NEW, False)]) is None
        assert af.select_newest_preview_file([]) is None


@pytest.mark.unit
class TestListingAttachesTheNewestPreview:
    def test_current_listing(self):
        af = _af()
        items = [
            _item("a/m.glb", _OLD),
            _item("a/m.glb.previewFile.gif", _OLD),
            _item("a/m.glb.previewFile.png", _NEW),
        ]
        request = MagicMock(maxItems=100, pageSize=100, startingToken=None, prefix=None,
                            includeArchived=False, basic=False)
        with patch.object(af, "list_s3_objects_with_archive_status",
                          return_value={"items": items, "NextToken": None}):
            response = af.list_asset_files_current(_DB, _ASSET, {}, "bucket", _PREFIX, request)
        [model] = [item for item in response.items if item.relativePath == "/a/m.glb"]
        assert model.previewFile == "/a/m.glb.previewFile.png"

    def _snapshot(self):
        return {"files": [
            {"relativeKey": "a/m.glb", "lastModified": _OLD, "versionId": "v1", "size": 1},
            {"relativeKey": "a/m.glb.previewFile.png", "lastModified": _NEW, "versionId": "v3"},
            {"relativeKey": "a/m.glb.previewFile.gif", "lastModified": _OLD, "versionId": "v2"},
        ]}

    def test_version_snapshot_basic_mode(self):
        af = _af()
        request = MagicMock(assetVersionId="1", basic=True)
        with patch.object(af, "validate_asset_version_exists"), \
                patch.object(af, "get_asset_file_versions", return_value=self._snapshot()):
            response = af.list_asset_files_from_version(_DB, _ASSET, {}, "bucket", _PREFIX, request)
        [model] = response.items
        assert model.previewFile == "/a/m.glb.previewFile.png"

    def test_version_snapshot_detailed_mode(self):
        af = _af()
        request = MagicMock(assetVersionId="1", basic=False, maxItems=100, pageSize=100,
                            startingToken=None, prefix=None, includeArchived=False)
        items = [
            _item("a/m.glb", _OLD),
            _item("a/m.glb.previewFile.gif", _OLD),
            _item("a/m.glb.previewFile.png", _NEW),
        ]
        with patch.object(af, "validate_asset_version_exists"), \
                patch.object(af, "get_asset_file_versions", return_value=self._snapshot()), \
                patch.object(af, "list_s3_objects_with_archive_status",
                             return_value={"items": items, "NextToken": None}):
            response = af.list_asset_files_from_version(_DB, _ASSET, {}, "bucket", _PREFIX, request)
        [model] = [item for item in response.items if item.relativePath == "/a/m.glb"]
        assert model.previewFile == "/a/m.glb.previewFile.png"

    def test_single_file_lookup(self):
        af = _af()
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": [
            {"Key": "asset1/a/m.glb.previewFile.gif",
             "LastModified": datetime(2026, 10, 1, tzinfo=timezone.utc)},
            {"Key": "asset1/a/m.glb.previewFile.png",
             "LastModified": datetime(2026, 10, 16, tzinfo=timezone.utc)},
            {"Key": "asset1/a/m.glb.previewFile.jpg.bak",
             "LastModified": datetime(2026, 10, 17, tzinfo=timezone.utc)},
        ]}]
        client = MagicMock()
        client.get_paginator.return_value = paginator
        with patch.object(af, "s3_client", client):
            newest = af.find_newest_preview_file_for_base("bucket", "asset1/a/m.glb")
        assert newest == "asset1/a/m.glb.previewFile.png"
        paginator.paginate.assert_called_once_with(Bucket="bucket", Prefix="asset1/a/m.glb.previewFile.")

    def test_single_file_lookup_lists_a_remote_bucket_in_its_region(self, monkeypatch):
        """The lookup lists through the module's Region-routing client, so an asset bucket in
        another Region is listed with that Region's client (#410)."""
        af = _af()
        # The module the routing client's methods resolve names in (the conftest's common.s3 mock;
        # sys.modules may hold a later copy of it).
        routing = type(af.s3_client)._client_for.__globals__
        regional, default = MagicMock(name="eu-west-1"), MagicMock(name="default")
        regional.get_paginator.return_value.paginate.return_value = [{"Contents": [
            {"Key": "asset1/a/m.glb.previewFile.png",
             "LastModified": datetime(2026, 10, 16, tzinfo=timezone.utc)},
        ]}]
        monkeypatch.setitem(routing["_bucket_regions_by_name"], "remote-bucket", "eu-west-1")
        monkeypatch.setitem(routing, "s3_client_for_region",
                            lambda region=None: {"eu-west-1": regional}[region])
        monkeypatch.setattr(af.s3_client, "_default_client", default)

        newest = af.find_newest_preview_file_for_base("remote-bucket", "asset1/a/m.glb")

        assert newest == "asset1/a/m.glb.previewFile.png"
        regional.get_paginator.assert_called_once_with("list_objects_v2")
        regional.get_paginator.return_value.paginate.assert_called_once_with(
            Bucket="remote-bucket", Prefix="asset1/a/m.glb.previewFile.")
        default.get_paginator.assert_not_called()
