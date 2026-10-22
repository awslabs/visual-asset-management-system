# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""listFiles attaches a file's preview when the preview falls on the next page.

'<file>.previewFile.<ext>' sorts directly after '<file>', so with a small page size a file can end
one page while its preview opens the next. The file still carries its previewFile on its own page,
and the next page does not report that preview as orphaned.
"""

import os

import boto3
import pytest
from moto import mock_aws
from unittest.mock import patch

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "vams-test-assets"
BASE_KEY = "db1/a1/"


@pytest.fixture
def af(monkeypatch):
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        for name in ("a.glb", "a.glb.previewFile.png", "b.glb", "b.glb.previewFile.jpg", "c.glb"):
            client.put_object(Bucket=BUCKET, Key=BASE_KEY + name, Body=b"content")
        monkeypatch.setattr(module, "s3_client", client)
        yield module


def _page(af, page_size, token=None):
    from backend.backend.models.assetsV3 import ListAssetFilesRequestModel

    request = ListAssetFilesRequestModel(pageSize=page_size, startingToken=token)
    return af.list_asset_files_current("db1", "a1", {"assetId": "a1"}, BUCKET, BASE_KEY, request)


def _walk(af, page_size):
    pages, token = [], None
    while True:
        response = _page(af, page_size, token)
        pages.append(response)
        token = response.NextToken
        if not token:
            return pages


@pytest.mark.unit
class TestPreviewOnTheNextPage:
    def test_a_file_ending_a_page_carries_its_preview_from_the_next_page(self, af):
        first = _page(af, 1)

        assert [(i.relativePath, i.previewFile) for i in first.items] == [
            ("/a.glb", "/a.glb.previewFile.png")]
        assert first.NextToken

    @pytest.mark.parametrize("page_size", [1, 2, 3, 10])
    def test_every_file_carries_its_preview_for_any_page_size(self, af, page_size):
        items = [item for page in _walk(af, page_size) for item in page.items]

        assert {i.relativePath: i.previewFile for i in items} == {
            "/a.glb": "/a.glb.previewFile.png",
            "/b.glb": "/b.glb.previewFile.jpg",
            "/c.glb": "",
        }

    def test_the_next_page_does_not_report_the_preview_as_orphaned(self, af):
        first = _page(af, 1)
        with patch.object(af, "logger") as mock_logger:
            second = _page(af, 1, first.NextToken)

        assert second.items == []  # the page held only a.glb's preview
        # The listing logs on every page, so a patch that missed the handler cannot pass silently.
        assert mock_logger.mock_calls
        assert not any("orphaned" in str(call) for call in mock_logger.mock_calls)

    def test_a_preview_without_its_base_file_is_still_reported_orphaned(self, af):
        """NEGATIVE CONTROL: a preview whose base file does not exist is still logged."""
        af.s3_client.put_object(Bucket=BUCKET, Key=BASE_KEY + "z.glb.previewFile.png", Body=b"x")
        with patch.object(af, "logger") as mock_logger:
            _page(af, 10)

        assert any("orphaned" in str(call) for call in mock_logger.warning.call_args_list)
