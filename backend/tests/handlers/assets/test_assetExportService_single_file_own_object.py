# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1846: the asset export lists a single-file asset's own object at ``/{file name}``, as the file list does.

Without a companion entry ``p1/model.glb/model.glb`` the object and its preview are exported with the companion
folder; with one, the export lists the companion folder only, as before. Keys that only share the location's
characters are never exported.
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
    def test_the_export_lists_what_the_file_list_shows(self, companion):
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
        if companion:
            assert listed == sorted([(COPY, "/model.glb"), ("p1/model.glb/sub/x.obj", "/sub/x.obj")])
        else:
            assert listed == sorted([(LOCATION, "/model.glb"),
                                     ("p1/model.glb.previewFile.png", "/model.glb.previewFile.png"),
                                     ("p1/model.glb/sub/x.obj", "/sub/x.obj")])
