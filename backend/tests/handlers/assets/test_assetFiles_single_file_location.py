# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file list and the permanent prefix delete of a single-file asset stay inside the asset.

An asset created with a ``bucketExistingKey`` that names an object has a location key with no trailing
``/``. The asset holds that object, its companion folder ``model.glb/`` and its own previews
(``model.glb.previewFile.png``). Used as an S3 prefix, the key also matches keys that only share its
characters -- here ``model.glb.bak`` and ``model.glbx/inner.txt`` -- which belong to something else. These
tests run the listing and ``delete_s3_prefix_all_versions`` against a versioned moto bucket and check that
only the asset's keys are listed and deleted. Relative paths are the key after the location key, as
before: ``/`` for the object, ``/.previewFile.png`` for its preview, ``/sub/x.obj`` for a companion entry.
"""

import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "vams-test-assets"
FILE_LOCATION = "p1/model.glb"
OWN_KEY = FILE_LOCATION
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COMPANION = "p1/model.glb/sub/x.obj"
SAME_NAME_COMPANION = "p1/model.glb/model.glb"
HELD = (OWN_KEY, OWN_PREVIEW, COMPANION, SAME_NAME_COMPANION)
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt")
FOLDER_LOCATION = "proj/a/"
FOLDER_KEYS = ("proj/a/", "proj/a/x.txt", "proj/a/x.txt.previewFile.png", "proj/a/sub/y.txt")
SIBLING_FOLDER_KEYS = ("proj/a-other/y.txt", "proj/a.bak")
CLAIMS = {"tokens": ["tester"], "roles": []}


def _live(client, key):
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def _versions(client, key):
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    return ([v for v in listing.get("Versions", []) if v["Key"] == key]
            + [m for m in listing.get("DeleteMarkers", []) if m["Key"] == key])


@pytest.fixture
def af(monkeypatch):
    """assetFiles against a versioned moto bucket; the asset record names the single-file location."""
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in HELD + NEIGHBOURS + FOLDER_KEYS + SIBLING_FOLDER_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")

        asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1",
                 "assetLocation": {"Key": FILE_LOCATION}}
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET,
                                               "baseAssetsPrefix": ""})
        # moto serves one request at a time reliably; the handler's worker pool is not under test
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        module.test_client = client
        yield module


def _walk(af, params, basic_mode, include_archived=True, location=FILE_LOCATION):
    items, token = [], None
    while True:
        query = dict(params)
        if token:
            query["startingToken"] = token
        result = af.list_s3_objects_with_archive_status(
            BUCKET, location, query, include_archived=include_archived, basic_mode=basic_mode)
        items.extend(result["items"])
        token = result.get("NextToken")
        if not token:
            return items


@pytest.mark.unit
@pytest.mark.parametrize("basic_mode", [False, True], ids=["full", "basic"])
class TestListFiles:
    def test_the_object_its_preview_and_companion_folder_are_listed_and_neighbours_are_not(self, af, basic_mode):
        items = _walk(af, {}, basic_mode)
        assert sorted((i["key"], i["relativePath"]) for i in items) == sorted([
            (OWN_KEY, "/"), (OWN_PREVIEW, "/.previewFile.png"), (COMPANION, "/sub/x.obj"),
            (SAME_NAME_COMPANION, "/model.glb")])

    def test_every_page_of_a_paged_listing_stays_inside_the_asset(self, af, basic_mode):
        items = _walk(af, {"pageSize": 1}, basic_mode, include_archived=False)
        assert sorted(i["key"] for i in items) == sorted(HELD)

    def test_an_archived_neighbour_is_not_listed_and_an_archived_own_file_is(self, af, basic_mode):
        af.test_client.delete_object(Bucket=BUCKET, Key="p1/model.glb.bak")
        af.test_client.delete_object(Bucket=BUCKET, Key=OWN_KEY)
        items = _walk(af, {}, basic_mode)
        archived = [(i["key"], i["relativePath"]) for i in items if i["isArchived"]]
        assert archived == [(OWN_KEY, "/")]
        assert "p1/model.glb.bak" not in {i["key"] for i in items}

    def test_a_folder_filter_lists_that_companion_folder(self, af, basic_mode):
        items = _walk(af, {"prefix": "sub/"}, basic_mode)
        assert [i["key"] for i in items] == [COMPANION]

    def test_control_a_folder_lists_its_keys_and_no_sibling_prefix(self, af, basic_mode):
        items = _walk(af, {}, basic_mode, location=FOLDER_LOCATION)
        assert sorted(i["relativePath"] for i in items) == [
            "/", "/sub/y.txt", "/x.txt", "/x.txt.previewFile.png"]


@pytest.mark.unit
class TestListAssetFiles:
    def test_the_own_file_carries_its_own_preview_and_no_neighbour_is_listed(self, af):
        response = af.list_asset_files("db1", "a1", {"pageSize": "100", "maxItems": "1000"}, CLAIMS)
        by_key = {item.key: item for item in response.items}
        assert sorted(by_key) == sorted([OWN_KEY, COMPANION, SAME_NAME_COMPANION])
        assert by_key[OWN_KEY].previewFile == "/.previewFile.png"


@pytest.mark.unit
class TestPermanentPrefixDelete:
    def test_a_single_file_location_deletes_the_asset_keys_only(self, af):
        deleted = af.delete_s3_prefix_all_versions(BUCKET, FILE_LOCATION, location=FILE_LOCATION)
        assert sorted(deleted) == sorted(HELD)
        for key in HELD:
            assert _versions(af.test_client, key) == [], key
        for key in NEIGHBOURS:
            assert _live(af.test_client, key), key
            assert len(_versions(af.test_client, key)) == 1, key

    def test_a_folder_location_keeps_sibling_prefixes(self, af):
        deleted = af.delete_s3_prefix_all_versions(BUCKET, "proj/a", location=FOLDER_LOCATION)
        assert sorted(deleted) == sorted(FOLDER_KEYS)
        for key in SIBLING_FOLDER_KEYS:
            assert _live(af.test_client, key), key
