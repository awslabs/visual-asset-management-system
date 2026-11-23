# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1846 control: the asset export of a single-file asset is unchanged on 2.6.8.

The export lists the companion folder ``p1/model.glb/`` only, with the paths the file list shows for those files,
whether or not a companion entry named like the object exists; the asset's own file is exported from v2.7.0. Keys
that only share the location's characters are never exported.
"""

from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
)

LOCATION = "p1/model.glb"
COPY = "p1/model.glb/model.glb"


# ---------------------------------------------------------------------------- export

@pytest.mark.unit
class TestExportListing:
    @pytest.mark.parametrize("companion", [False, True], ids=["no-companion", "companion"])
    def test_the_export_lists_the_companion_folder_only(self, companion):
        m = _load_asset_export_service()
        keys = [LOCATION, "p1/model.glb.previewFile.png", "p1/model.glb/sub/x.obj",
                "p1/model.glb.bak", "p1/model.glbx/a.txt"] + ([COPY] if companion else [])
        with mock_aws():
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket="export-bucket")
            client.put_bucket_versioning(Bucket="export-bucket", VersioningConfiguration={"Status": "Enabled"})
            for key in keys:
                client.put_object(Bucket="export-bucket", Key=key, Body=b"x")
            with patch.object(m, "s3_client", client):
                files = m.list_s3_files("export-bucket", LOCATION)
        listed = sorted((f["key"], f["relativePath"]) for f in files)
        expected = [("p1/model.glb/sub/x.obj", "/sub/x.obj")] + ([(COPY, "/model.glb")] if companion else [])
        assert listed == sorted(expected)
