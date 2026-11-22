# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A single-file asset's file list pages hold at most ``pageSize`` files (D7).

The first page of a single-file location lists its object and its own previews, then its companion folder
``{key}/``. It used to ask the companion folder for at least one key whatever room was left, so with
``pageSize=1`` the first page held the object and a companion file. The object's previews are not files of
the list (the list attaches them to the object), so they take no place. When the object fills the page, the
page lists no companion entry and its token names the start of the companion folder; that token is never
passed to Amazon S3 and lists nothing outside the location.
"""

import os
from collections import Counter

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: E402

BUCKET = "vams-test-pages"
LOCATION = "p1/model.glb"
PREVIEWS = ("p1/model.glb.previewFile.png", "p1/model.glb.previewFile.jpg", "p1/model.glb.previewFile.gif")
COMPANIONS = ("p1/model.glb/a.obj", "p1/model.glb/b.obj", "p1/model.glb/model.glb", "p1/model.glb/sub/c.obj")
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt", "p1/model.glb-old/n.txt")
LAYOUTS = {
    "object only": (LOCATION,),
    "object and previews": (LOCATION,) + PREVIEWS,
    "object and companions": (LOCATION,) + COMPANIONS,
    "object, previews and companions": (LOCATION,) + PREVIEWS + COMPANIONS,
    "companions, object archived": COMPANIONS,
}


@pytest.fixture
def pages(monkeypatch):
    """A versioned moto bucket and a walker that lists one layout page by page."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        monkeypatch.setattr(assetFiles, "s3_client", client)
        monkeypatch.setattr(assetFiles, "MAX_PARALLEL_S3_WORKERS", 1)
        sent = []
        real = client.list_objects_v2

        def spy(**kwargs):
            sent.append(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(client, "list_objects_v2", spy)

        def load(keys):
            for page in client.get_paginator("list_object_versions").paginate(Bucket=BUCKET):
                for entry in page.get("Versions", []) + page.get("DeleteMarkers", []):
                    client.delete_object(Bucket=BUCKET, Key=entry["Key"], VersionId=entry["VersionId"])
            for key in set(keys) | set(NEIGHBOURS) | {LOCATION}:
                client.put_object(Bucket=BUCKET, Key=key, Body=b"x")
            if LOCATION not in keys:
                client.delete_object(Bucket=BUCKET, Key=LOCATION)

        def walk(size, basic_mode=True, location=LOCATION, first_token=None):
            result_pages, token = [], first_token
            while True:
                query = {"pageSize": size}
                if token:
                    query["startingToken"] = token
                result = assetFiles.list_s3_objects_with_archive_status(
                    BUCKET, location, query, include_archived=False, basic_mode=basic_mode)
                result_pages.append((result["items"], result.get("NextToken")))
                token = result.get("NextToken")
                if not token or len(result_pages) > 50:
                    return result_pages

        walk.load = load
        walk.sent = sent
        yield walk


def _files(items):
    """The items the file list shows: previews are attached to their file, not listed."""
    return [item["key"] for item in items if not assetFiles.is_preview_file(item["key"])]


@pytest.mark.unit
class TestSingleFileListingPages:
    def test_every_file_is_listed_once_and_no_page_is_larger_than_its_size(self, pages):
        for layout, keys in LAYOUTS.items():
            pages.load(keys)
            expected = sorted(k for k in keys if not assetFiles.is_preview_file(k))
            for size in (1, 2, 3):
                for basic_mode in (True, False):
                    label = (layout, size, basic_mode)
                    walked = pages(size, basic_mode=basic_mode)
                    files = [_files(items) for items, _ in walked]
                    listed = Counter(key for page_files in files for key in page_files)
                    assert sorted(listed) == expected and set(listed.values()) == {1}, (label, files)
                    assert all(len(page_files) <= size for page_files in files), (label, files)
                    # Every page but the last is full, and none is empty
                    assert all(len(page_files) == size for page_files in files[:-1]), (label, files)
                    assert all(files), (label, files)
                    # The object's previews are read on the page that lists the object
                    previews = [i["key"] for i in walked[0][0] if assetFiles.is_preview_file(i["key"])]
                    assert sorted(previews) == sorted(k for k in keys if k in PREVIEWS), label

    def test_a_full_first_page_hands_over_with_the_companion_folder_token(self, pages):
        pages.load(LAYOUTS["object, previews and companions"])
        walked = pages(1)
        assert walked[0][1] == assetFiles.COMPANION_FOLDER_START_TOKEN
        assert _files(walked[0][0]) == [LOCATION]
        assert _files(walked[1][0]) == ["p1/model.glb/a.obj"]
        # The token never reaches Amazon S3, and every listing names a prefix of the location
        assert all(kw.get("ContinuationToken") != assetFiles.COMPANION_FOLDER_START_TOKEN for kw in pages.sent)
        assert {kw["Prefix"] for kw in pages.sent} <= {LOCATION, LOCATION + ".previewFile.", LOCATION + "/"}
        # With nothing in the companion folder the full first page is the last
        pages.load(LAYOUTS["object and previews"])
        walked = pages(1)
        assert len(walked) == 1 and walked[0][1] is None

    def test_the_token_lists_nothing_outside_the_location(self, pages):
        pages.load(LAYOUTS["object and companions"])
        # A folder location refuses the single-file token before any listing
        pages.sent.clear()
        with pytest.raises(assetFiles.VAMSGeneralErrorResponse) as e:
            pages(1, location="p1/", first_token=assetFiles.COMPANION_FOLDER_START_TOKEN)
        assert "Invalid pagination token" in str(e.value)
        assert pages.sent == []
        # A continuation token from another prefix's listing still lists the companion folder only
        other = assetFiles.s3_client.list_objects_v2(Bucket=BUCKET, Prefix="p1/", MaxKeys=1)
        try:
            walked = pages(2, first_token=other["NextContinuationToken"])
        except assetFiles.VAMSGeneralErrorResponse:
            walked = []
        listed = {key for items, _ in walked for key in _files(items)}
        assert listed <= set(COMPANIONS), listed
