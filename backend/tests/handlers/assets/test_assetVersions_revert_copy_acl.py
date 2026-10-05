# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset version revert copy grants the asset bucket owner full control of the new version."""

import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder.
from backend.backend.handlers.assets import assetVersions  # noqa: F401,E402


@pytest.mark.unit
class TestRevertCopyAcl:
    def test_revert_copy_sets_bucket_owner_full_control(self, monkeypatch):
        av = assetVersions
        resource = MagicMock()
        client = MagicMock()
        client.head_object.return_value = {"VersionId": "s3v-new"}
        monkeypatch.setattr(av, "s3_resource", resource)
        monkeypatch.setattr(av, "s3_client", client)

        assert av.copy_s3_object_version(
            "asset-bucket", "db1/a1/model.glb", "s3v-old", "asset-bucket", "db1/a1/model.glb"
        ) == "s3v-new"

        extra_args = resource.meta.client.copy.call_args.kwargs.get("ExtraArgs") or {}
        assert extra_args.get("ACL") == "bucket-owner-full-control"
