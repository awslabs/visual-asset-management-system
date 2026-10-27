# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""listFiles with includeArchived lists each archived file once and never a restored file.

Archived files are the keys whose current version is a delete marker. They are listed from
list_object_versions beside the paged list_objects_v2 listing, on the first page only, and an
older delete marker of a file restored since does not mark that file archived.
"""

import os
from datetime import datetime

import boto3
import pytest
from moto import mock_aws

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "vams-test-assets"
PREFIX = "db1/a1/"


@pytest.fixture
def af(monkeypatch):
    """assetFiles pointed at a versioned moto bucket with three live files and two deleted ones."""
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for name in ("a.txt", "b.txt", "c.txt", "x-archived.txt", "y-archived.txt"):
            client.put_object(Bucket=BUCKET, Key=PREFIX + name, Body=b"content")
        for name in ("x-archived.txt", "y-archived.txt"):
            client.delete_object(Bucket=BUCKET, Key=PREFIX + name)

        monkeypatch.setattr(module, "s3_client", client)
        yield module


def _walk(af, basic_mode):
    """Every item of every page, following NextToken with includeArchived on."""
    items, token, pages = [], None, 0
    while True:
        params = {"pageSize": 2}
        if token:
            params["startingToken"] = token
        result = af.list_s3_objects_with_archive_status(
            BUCKET, PREFIX, params, include_archived=True, basic_mode=basic_mode)
        items.extend(result["items"])
        pages += 1
        token = result.get("NextToken")
        if not token:
            return items, pages


@pytest.mark.unit
@pytest.mark.parametrize("basic_mode", [False, True], ids=["full", "basic"])
class TestIncludeArchivedAcrossPages:
    def test_each_key_appears_once_across_all_pages(self, af, basic_mode):
        items, pages = _walk(af, basic_mode)

        assert pages == 2
        assert sorted(item["relativePath"] for item in items) == [
            "/a.txt", "/b.txt", "/c.txt", "/x-archived.txt", "/y-archived.txt"]

    def test_the_archived_files_are_listed_on_the_first_page(self, af, basic_mode):
        first = af.list_s3_objects_with_archive_status(
            BUCKET, PREFIX, {"pageSize": 2}, include_archived=True, basic_mode=basic_mode)

        archived = sorted(item["relativePath"] for item in first["items"] if item["isArchived"])
        assert archived == ["/x-archived.txt", "/y-archived.txt"]

    def test_a_later_page_lists_no_archived_entries(self, af, basic_mode):
        first = af.list_s3_objects_with_archive_status(
            BUCKET, PREFIX, {"pageSize": 2}, include_archived=True, basic_mode=basic_mode)
        second = af.list_s3_objects_with_archive_status(
            BUCKET, PREFIX, {"pageSize": 2, "startingToken": first["NextToken"]},
            include_archived=True, basic_mode=basic_mode)

        assert [item["relativePath"] for item in second["items"]] == ["/c.txt"]
        assert [item["isArchived"] for item in second["items"]] == [False]


class _RestoredFileS3:
    """S3 double for one listing page of a.txt and b.txt, where z-restored.txt, live on a later
    page, was deleted and then restored by copy.

    Its old delete marker is reported with IsLatest false and its restored copy with IsLatest
    true, as S3 reports them. (moto computes IsLatest per page of a paged version listing, so
    it cannot model this case.)
    """

    WHEN = datetime(2026, 9, 1, 0, 0, 0)

    def list_objects_v2(self, **kwargs):
        return {
            "Contents": [
                {"Key": PREFIX + name, "Size": 7, "ETag": '"e"', "LastModified": self.WHEN}
                for name in ("a.txt", "b.txt")
            ],
            "NextContinuationToken": "page-2",
        }

    def head_object(self, Bucket, Key, **kwargs):
        return {"VersionId": "v-live", "Metadata": {}}

    def get_paginator(self, name):
        assert name == "list_object_versions"
        page = {
            "Versions": [
                {"Key": PREFIX + "z-restored.txt", "VersionId": "v-copy", "IsLatest": True,
                 "Size": 7, "ETag": '"e"', "LastModified": self.WHEN},
                {"Key": PREFIX + "z-restored.txt", "VersionId": "v-orig", "IsLatest": False,
                 "Size": 7, "ETag": '"e"', "LastModified": self.WHEN},
            ],
            "DeleteMarkers": [
                {"Key": PREFIX + "z-restored.txt", "VersionId": "dm-old", "IsLatest": False,
                 "LastModified": self.WHEN},
            ],
        }

        class _Paginator:
            def paginate(self, **kwargs):
                return [page]

        return _Paginator()


@pytest.mark.unit
@pytest.mark.parametrize("basic_mode", [False, True], ids=["full", "basic"])
def test_a_restored_file_is_not_reported_archived(monkeypatch, basic_mode):
    from backend.backend.handlers.assets import assetFiles as af
    monkeypatch.setattr(af, "s3_client", _RestoredFileS3())

    result = af.list_s3_objects_with_archive_status(
        BUCKET, PREFIX, {"pageSize": 2}, include_archived=True, basic_mode=basic_mode)

    assert [item["relativePath"] for item in result["items"]] == ["/a.txt", "/b.txt"]
    assert not any(item["isArchived"] for item in result["items"])
