# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A single-file operation changes only the file's own viewer data, not another file's (#1870).

A file's viewer data is in ``{databaseId}/{file key}/preview/`` of the auxiliary bucket. The rest of
``{databaseId}/{file key}/`` holds the data of the files of a folder named like the file: another asset's,
nested in this asset's location, or this asset's own. Under ``preview/`` another file's data appears only for
a file in a folder ``{file key}/preview/``, and its path there has a ``preview`` folder segment
(``common.assetLocations.viewer_data_scope``).

Layout (asset bucket ``vams-scope``, database db1): ``outer`` at ``p/outer/`` holds ``x.txt`` and ``y.txt``;
``kit`` (same database) is stored at ``p/outer/x.txt/``, a folder named like ``x.txt``, and holds ``f.obj`` and
``preview/a.glb``. Deleting, reverting, moving, renaming or copying ``/x.txt``, and the explicit auxiliary
delete of it, act on ``x.txt``'s own viewer data only; archive and unarchive change no viewer data.
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

from backend.backend.handlers.assets import assetFiles, assetVersions  # noqa: E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)

al = sys.modules["common.assetLocations"]
fa = sys.modules["common.fileAttribution"]

BUCKET = "vams-scope"
AUX = "vams-scope-aux"
LOCATION = "p/outer/"
OUTER = asset_row("db1", "outer", "reg-1", LOCATION, BUCKET)
KIT = asset_row("db1", "kit", "reg-1", "p/outer/x.txt/", BUCKET)
ASSET_KEYS = ("p/outer/x.txt", "p/outer/y.txt", "p/outer/x.txt/f.obj", "p/outer/x.txt/preview/a.glb")
OWN = ("db1/p/outer/x.txt/preview/PotreeViewer/metadata.json", "db1/p/outer/x.txt/preview/PotreeViewer/octree.bin")
# The data of kit's files: one beside x.txt's preview folder, one inside it (kit's folder named preview)
NESTED = ("db1/p/outer/x.txt/f.obj/preview/PotreeViewer/metadata.json",
          "db1/p/outer/x.txt/preview/a.glb/preview/PotreeViewer/metadata.json")
OTHER = ("db1/p/outer/y.txt/preview/PotreeViewer/metadata.json",)
CLAIMS = {"tokens": ["tester"], "roles": []}


def _aux_keys(client):
    """Every auxiliary key with a version or delete marker."""
    listed = client.list_object_versions(Bucket=AUX)
    return sorted({entry["Key"] for entry in listed.get("Versions", []) + listed.get("DeleteMarkers", [])})


def _current_aux_keys(client):
    """The auxiliary keys with a current object (a move leaves a delete marker at the source)."""
    return sorted(obj["Key"] for obj in client.list_objects_v2(Bucket=AUX).get("Contents", []))


@pytest.fixture
def af(monkeypatch):
    """assetFiles on a versioned asset bucket and auxiliary bucket in moto, with the location index ready."""
    al.clear_nested_locations_cache()
    fa.clear_registration_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        for bucket in (BUCKET, AUX):
            client.create_bucket(Bucket=bucket)
            client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        for key in ASSET_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
        client.put_object(Bucket=BUCKET, Key="p/outer/x.txt", Body=b"v2")
        for key in OWN + NESTED + OTHER:
            client.put_object(Bucket=AUX, Key=key, Body=b"viewer data")
        m = assetFiles
        table = AttributionAssetTable([OUTER, KIT])
        monkeypatch.setattr(m, "asset_table", table)
        monkeypatch.setattr(m, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        monkeypatch.setattr(m, "s3_client", client)
        monkeypatch.setattr(m, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(m, "asset_aux_bucket_name", AUX)
        monkeypatch.setattr(m, "get_asset_with_permissions", lambda *args, **kwargs: dict(OUTER))
        monkeypatch.setattr(m, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": "/"})
        monkeypatch.setattr(m, "asset_file_version_history_table", None)
        monkeypatch.setattr(m, "MAX_PARALLEL_S3_WORKERS", 1)
        for name in ("send_subscription_email", "delete_file_metadata", "delete_files_metadata",
                     "delete_file_version_records"):
            monkeypatch.setattr(m, name, MagicMock(name=name))
        yield SimpleNamespace(m=m, client=client, table=table)
    al.clear_nested_locations_cache()


@pytest.mark.unit
class TestTheScope:
    def test_the_files_own_folder_and_nothing_of_another_file(self):
        prefix, keep = al.viewer_data_scope("db1", "p/outer/x.txt")
        assert prefix == "db1/p/outer/x.txt/preview/"
        assert all(keep(key) for key in OWN)
        assert not any(keep(key) for key in NESTED + OTHER)
        # A file named preview in a folder named like the file has its data at .../preview/preview/
        assert not keep("db1/p/outer/x.txt/preview/preview/PotreeViewer/metadata.json")


@pytest.mark.unit
class TestTheSingleFileOperations:
    def test_a_file_delete_removes_its_own_viewer_data_only(self, af):
        af.m.delete_file("db1", "outer", "/x.txt", False, True, CLAIMS)

        assert _aux_keys(af.client) == sorted(NESTED + OTHER)

    def test_a_file_of_a_folder_named_like_it_in_the_same_asset_keeps_its_data(self, af):
        af.table.rows[:] = [OUTER]
        af.m.delete_file("db1", "outer", "/x.txt", False, True, CLAIMS)

        assert _aux_keys(af.client) == sorted(NESTED + OTHER)

    def test_a_revert_deletes_its_own_viewer_data_only(self, af):
        versions = af.client.list_object_versions(Bucket=BUCKET, Prefix="p/outer/x.txt")["Versions"]
        oldest = [v["VersionId"] for v in versions if v["Key"] == "p/outer/x.txt" and not v["IsLatest"]][0]

        assert af.m.revert_file_version("db1", "outer", "/x.txt", oldest, CLAIMS).success

        assert _aux_keys(af.client) == sorted(NESTED + OTHER)

    @pytest.mark.parametrize("dest", ["/z.txt", "/moved/x.txt"], ids=["rename", "move"])
    def test_a_move_or_rename_carries_its_own_viewer_data_only(self, af, monkeypatch, dest):
        monkeypatch.setattr(af.m, "move_s3_object", lambda *args, **kwargs: True)
        monkeypatch.setattr(af.m, "process_preview_files", lambda **kwargs: [])
        monkeypatch.setattr(af.m, "_copy_file_metadata_to_destination", lambda *args, **kwargs: 0)

        af.m.move_file("db1", "outer", "/x.txt", dest, CLAIMS)

        moved = [key.replace("p/outer/x.txt/", "p/outer/" + dest.lstrip("/") + "/", 1) for key in OWN]
        assert _current_aux_keys(af.client) == sorted(NESTED + OTHER + tuple(moved))

    def test_a_copy_carries_its_own_viewer_data_only(self, af, monkeypatch):
        monkeypatch.setattr(af.m, "copy_s3_object", lambda *args, **kwargs: True)
        monkeypatch.setattr(af.m, "process_preview_files", lambda **kwargs: [])
        monkeypatch.setattr(af.m, "_copy_file_metadata_to_destination", lambda *args, **kwargs: 0)

        af.m.copy_file("db1", "outer", "/x.txt", "/c.txt", None, None, CLAIMS)

        copied = [key.replace("p/outer/x.txt/", "p/outer/c.txt/", 1) for key in OWN]
        assert _aux_keys(af.client) == sorted(OWN + NESTED + OTHER + tuple(copied))

    def test_the_auxiliary_delete_of_a_file_removes_its_own_viewer_data_only(self, af):
        response = af.m.delete_auxiliary_preview_asset_files("db1", "outer", "/x.txt", CLAIMS)

        assert response.deletedCount == len(OWN)
        assert _aux_keys(af.client) == sorted(NESTED + OTHER)

    def test_archive_and_unarchive_change_no_viewer_data(self, af):
        """Control: neither touches the auxiliary bucket, before or after #1870."""
        af.m.archive_file("db1", "outer", "/x.txt", False, CLAIMS)
        af.m.unarchive_file("db1", "outer", "/x.txt", CLAIMS)

        assert _aux_keys(af.client) == sorted(OWN + NESTED + OTHER)


@pytest.mark.unit
class TestTheAssetVersionRevert:
    def test_a_reverted_file_loses_its_own_viewer_data_only(self, af, monkeypatch):
        av = assetVersions
        monkeypatch.setattr(av, "s3_client", af.client)
        monkeypatch.setattr(av, "asset_aux_bucket_name", AUX)
        monkeypatch.setattr(av, "asset_table", af.table)
        monkeypatch.setattr(av, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        request = MagicMock(assetVersionId="1", revertMetadata=False, comment="")
        files = [{"relativeKey": "/x.txt", "versionId": "s3v1", "size": 1, "etag": "e"}]
        with patch.object(av, "get_asset_with_permissions", return_value=dict(OUTER)), \
                patch.object(av, "get_asset_s3_location", return_value=(BUCKET, LOCATION)), \
                patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1"}), \
                patch.object(av, "get_asset_file_versions", return_value={"files": files}), \
                patch.object(av, "reserve_next_asset_version", return_value="3"), \
                patch.object(av, "does_file_version_exist", return_value=True), \
                patch.object(av, "copy_s3_object_version", return_value="s3v-new"), \
                patch.object(av, "save_asset_file_versions", return_value=True), \
                patch.object(av, "save_asset_metadata_version", return_value=True), \
                patch.object(av, "stamp_asset_version_provenance", MagicMock()), \
                patch.object(av, "finalize_asset_version", MagicMock()), \
                patch.object(av, "send_subscription_email", MagicMock()), \
                patch.object(av, "MAX_PARALLEL_S3_WORKERS", 1):
            assert av.revert_asset_version("db1", "outer", request, CLAIMS).success

        assert _aux_keys(af.client) == sorted(NESTED + OTHER)
