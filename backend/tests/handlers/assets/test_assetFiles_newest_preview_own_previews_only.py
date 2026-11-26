# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The newest-preview lookup of file info and listings names a file's own preview only.

A file's preview is ``{file key}.previewFile.{ext}`` with no ``/`` in ``ext`` (``common.assetLocations``).
``find_newest_preview_file_for_base`` lists the prefix ``{file key}.previewFile.``, which also matches keys
inside a folder whose name starts with it. Here the single-file asset is ``p/model.glb``, and a folder asset
next to it is ``p/model.glb.previewFile.png.previewFile.x/``, holding ``model.glb.previewFile.jpg``. That key
is the folder asset's file, not a preview of ``p/model.glb``, so it is never named as one. The text after its
first ``.previewFile.`` is ``png``, an allowed preview extension, so the extension check alone does not drop it
(a folder ``p/model.glb.previewFile.png/`` is dropped by that check already). This is the fourth preview
lookup; #1920 fixed the other three the same way.
"""

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BASE = "p/model.glb"
OWN = "p/model.glb.previewFile.png"
IN_FOLDER = "p/model.glb.previewFile.png.previewFile.x/model.glb.previewFile.jpg"
IN_PREVIEW_NAMED_FOLDER = "p/model.glb.previewFile.png/model.glb.previewFile.jpg"
OLD = "2026-10-01T10:00:00+00:00"
NEW = "2026-10-16T10:00:00+00:00"


def _lookup(monkeypatch, objects, base=BASE):
    """find_newest_preview_file_for_base over a listing of (key, LastModified); returns (result, prefixes)."""
    from backend.backend.handlers.assets import assetFiles as af

    prefixes = []

    def paginate(Bucket, Prefix):  # noqa: N803 - boto3 kwarg names
        prefixes.append(Prefix)
        return [{"Contents": [{"Key": key, "LastModified": modified} for key, modified in objects
                              if key.startswith(Prefix)]}]

    client = MagicMock()
    client.get_paginator.return_value.paginate.side_effect = paginate
    monkeypatch.setattr(af, "s3_client", client)
    return af.find_newest_preview_file_for_base("asset-bucket", base), prefixes


@pytest.mark.unit
class TestTheNewestPreviewIsTheFilesOwn:
    def test_a_key_in_a_folder_named_like_the_preview_is_not_a_preview(self, monkeypatch):
        result, prefixes = _lookup(monkeypatch, [(IN_FOLDER, NEW)])
        assert prefixes == ["p/model.glb.previewFile."]  # the listing does match the folder's key
        assert result is None

    def test_the_own_preview_wins_over_a_newer_key_in_such_a_folder(self, monkeypatch):
        result, _ = _lookup(monkeypatch, [(OWN, OLD), (IN_FOLDER, NEW)])
        assert result == OWN

    def test_the_newest_own_preview_still_wins(self, monkeypatch):
        # Control (#466): several own previews, the newest is named.
        result, _ = _lookup(monkeypatch, [("p/model.glb.previewFile.gif", OLD), (OWN, NEW), (IN_FOLDER, NEW)])
        assert result == OWN

    def test_a_folder_named_like_one_preview_is_not_a_preview_either(self, monkeypatch):
        # Control: the extension check already drops this shape ('png/model.glb' is no extension).
        result, _ = _lookup(monkeypatch, [(IN_PREVIEW_NAMED_FOLDER, NEW)])
        assert result is None

    def test_a_file_at_the_bucket_root(self, monkeypatch):
        in_folder = "model.glb.previewFile.png.previewFile.x/model.glb.previewFile.jpg"
        result, prefixes = _lookup(monkeypatch, [("model.glb.previewFile.png", OLD), (in_folder, NEW)],
                                   base="model.glb")
        assert prefixes == ["model.glb.previewFile."]
        assert result == "model.glb.previewFile.png"
