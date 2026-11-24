# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset type detection after an upload counts only the keys a single-file asset holds.

A single-file asset (location key with no trailing ``/``) holds its object, its companion folder
``{file key}/`` and its own previews. The asset-type sample lists the location key as an S3 prefix, which
also returns keys that only share its characters (``model.glb.bak``, ``model.glbx/...``). Counted as the
asset's files, those turned a single file into a folder. Both upload paths (the API upload and the large
file consumer) are checked.
"""

import importlib
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402

FILE_LOCATION = "p1/model.glb"
MODULES = ["uploadFile", "sqsUploadFileLarge"]


def _listing(*keys):
    """A list_all_objects stand-in: every key under the prefix, in key order, up to the sample cap."""
    def _list_all_objects(bucket, prefix, client=None, max_objects=None):
        listed = [{"Key": key} for key in sorted(k for k in keys if k.startswith(prefix))]
        return listed[:max_objects] if max_objects is not None else listed
    return _list_all_objects


def _asset_type(module_name, *keys, location=FILE_LOCATION):
    module = importlib.import_module(f"backend.backend.handlers.assets.{module_name}")
    with patch.object(module, "list_all_objects", side_effect=_listing(*keys)), \
            patch.object(module, "is_file_archived", return_value=False):
        return module.determine_asset_type("asset-1", "bucket", location)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", MODULES)
class TestAssetTypeAfterUpload:
    def test_keys_that_only_share_the_characters_are_not_counted(self, module_name):
        assert _asset_type(module_name, "p1/model.glb", "p1/model.glb.bak", "p1/model.glbx/inner.txt") == ".glb"

    def test_a_neighbour_alone_leaves_the_asset_without_files(self, module_name):
        assert _asset_type(module_name, "p1/model.glb.bak") is None

    def test_a_companion_file_makes_a_folder(self, module_name):
        assert _asset_type(module_name, "p1/model.glb", "p1/model.glb-old/n.txt", "p1/model.glb/sub/x.obj") == "folder"

    def test_control_a_folder_location_counts_its_files_only(self, module_name):
        assert _asset_type(module_name, "proj/a/x.obj", "proj/a-other/y.glb", location="proj/a/") == ".obj"
