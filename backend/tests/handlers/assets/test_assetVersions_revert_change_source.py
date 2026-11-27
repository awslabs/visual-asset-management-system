# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset version revert records each file it copies forward as a file revert (#2006).

The revert copies each file's recorded S3 version forward. The copy kept the source version's metadata
as it was, so the new current version carried that version's change source (for example `upload`) and
the file's history labelled the revert as that action. The copy now keeps the source version's metadata
and headers, and stamps `fileRevert`, the acting user and the source version, the way a single-file
revert does. The bucket is a versioned moto bucket.
"""

import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder.
from backend.backend.handlers.assets import assetVersions as av  # noqa: E402

BUCKET = "asset-bucket"
DB = "db1"
ASSET = "a1"
PREFIX = f"{ASSET}/"
KEY = f"{PREFIX}model.glb"


@pytest.fixture
def s3(monkeypatch):
    """A versioned moto bucket holding two versions of one file; returns (client, first version id)."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        first = client.put_object(
            Bucket=BUCKET, Key=KEY, Body=b"first", ContentType="model/gltf-binary",
            Metadata={"assetid": ASSET, "databaseid": DB, "vams-changesource": "workflowExecution",
                      "vams-changeuserid": "bob", "vams-changeworkflowid": "wf-1",
                      "vams-changeworkflowexecutionid": "exec-1"})["VersionId"]
        client.put_object(Bucket=BUCKET, Key=KEY, Body=b"second",
                          Metadata={"assetid": ASSET, "databaseid": DB, "vams-changesource": "upload"})
        monkeypatch.setattr(av, "s3_client", client)
        monkeypatch.setattr(av, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        yield client, first


def _current(client):
    head = client.head_object(Bucket=BUCKET, Key=KEY)
    return head, client.get_object(Bucket=BUCKET, Key=KEY)["Body"].read()


@pytest.mark.unit
class TestTheRevertCopy:
    def test_the_new_version_is_recorded_as_a_file_revert(self, s3):
        client, first = s3
        new_version = av.copy_s3_object_version(BUCKET, KEY, first, BUCKET, KEY, change_user_id="alice")

        head, body = _current(client)
        assert head["VersionId"] == new_version
        assert body == b"first"
        assert head["Metadata"]["vams-changesource"] == "fileRevert"
        assert head["Metadata"]["vams-changeuserid"] == "alice"
        assert head["Metadata"]["vams-changeassetfileversionfrom"] == first

    def test_the_source_versions_other_metadata_and_headers_are_kept(self, s3):
        client, first = s3
        av.copy_s3_object_version(BUCKET, KEY, first, BUCKET, KEY, change_user_id="alice")

        head, _ = _current(client)
        assert head["Metadata"]["assetid"] == ASSET
        assert head["Metadata"]["databaseid"] == DB
        assert head["ContentType"] == "model/gltf-binary"

    def test_the_source_versions_workflow_provenance_is_not_carried_forward(self, s3):
        client, first = s3
        av.copy_s3_object_version(BUCKET, KEY, first, BUCKET, KEY, change_user_id="alice")

        head, _ = _current(client)
        assert "vams-changeworkflowid" not in head["Metadata"]
        assert "vams-changeworkflowexecutionid" not in head["Metadata"]

    def test_no_user_records_the_system_user(self, s3):
        client, first = s3
        av.copy_s3_object_version(BUCKET, KEY, first, BUCKET, KEY)

        head, _ = _current(client)
        assert head["Metadata"]["vams-changeuserid"] == "SYSTEM_USER"


@pytest.mark.unit
def test_an_asset_version_revert_records_the_acting_user_on_each_copied_file(s3):
    client, first = s3
    request = av.RevertAssetVersionRequestModel(assetVersionId="1", comment="back")
    target = {"files": [{"relativeKey": "model.glb", "versionId": first, "size": 5, "etag": "e"}]}
    with patch.object(av, "get_asset_with_permissions", return_value={"assetId": ASSET}), \
            patch.object(av, "get_asset_s3_location", return_value=(BUCKET, PREFIX)), \
            patch.object(av, "nested_locations_for", return_value=MagicMock(keep=lambda key: True)), \
            patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1"}), \
            patch.object(av, "get_asset_file_versions", return_value=target), \
            patch.object(av, "reserve_next_asset_version", return_value="3"), \
            patch.object(av, "delete_assetAuxiliary_files"), \
            patch.object(av, "save_asset_file_versions", return_value=True), \
            patch.object(av, "save_asset_metadata_version", return_value=True), \
            patch.object(av, "stamp_asset_version_provenance"), \
            patch.object(av, "finalize_asset_version"), \
            patch.object(av, "send_subscription_email"):
        response = av.revert_asset_version(DB, ASSET, request, {"tokens": ["alice"]})

    assert response.skippedFiles is None
    head, body = _current(client)
    assert body == b"first"
    assert head["Metadata"]["vams-changesource"] == "fileRevert"
    assert head["Metadata"]["vams-changeuserid"] == "alice"
    assert head["Metadata"]["vams-changeassetfileversionfrom"] == first
