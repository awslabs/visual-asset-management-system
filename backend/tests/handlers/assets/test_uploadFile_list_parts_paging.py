# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Upload completion reads every uploaded part, past ListParts' 1,000-part page.

calculate_total_file_size_from_parts compares the parts the client reports with the parts S3
holds and sums their sizes. ListParts returns at most 1,000 parts per call, so a file with more
parts is read over several pages.
"""

import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

PART_SIZE = 5 * 1024 * 1024


def _parts(first, last):
    return [{"PartNumber": n, "ETag": f"e{n}", "Size": PART_SIZE} for n in range(first, last + 1)]


def _expected(count):
    from backend.backend.models.assetsV3 import UploadPartCompletionModel
    return [UploadPartCompletionModel(PartNumber=n, ETag=f"e{n}") for n in range(1, count + 1)]


def _paged_list_parts(total, page=1000):
    """A ListParts double that pages like S3: IsTruncated and NextPartNumberMarker."""
    def list_parts(Bucket, Key, UploadId, PartNumberMarker=0, **kwargs):
        first = PartNumberMarker + 1
        last = min(PartNumberMarker + page, total)
        response = {"Parts": _parts(first, last), "IsTruncated": last < total}
        if last < total:
            response["NextPartNumberMarker"] = last
        return response
    return MagicMock(side_effect=list_parts)


@pytest.mark.unit
class TestListPartsPaging:
    def test_a_file_with_1200_parts_is_read_over_two_pages(self):
        from backend.backend.handlers.assets import uploadFile as uf
        s3 = MagicMock()
        s3.list_parts = _paged_list_parts(1200)
        with patch.object(uf, "s3", s3):
            size, ok, error = uf.calculate_total_file_size_from_parts("b", "k", "u", _expected(1200))

        assert (ok, error) == (True, None)
        assert size == 1200 * PART_SIZE
        assert s3.list_parts.call_count == 2
        assert "PartNumberMarker" not in s3.list_parts.call_args_list[0].kwargs
        assert s3.list_parts.call_args_list[1].kwargs["PartNumberMarker"] == 1000

    def test_the_part_limit_of_10000_is_read_in_full(self):
        from backend.backend.handlers.assets import uploadFile as uf
        s3 = MagicMock()
        s3.list_parts = _paged_list_parts(10000)
        with patch.object(uf, "s3", s3):
            size, ok, _ = uf.calculate_total_file_size_from_parts("b", "k", "u", _expected(10000))

        assert ok is True
        assert size == 10000 * PART_SIZE
        assert s3.list_parts.call_count == 10

    def test_a_part_missing_from_a_later_page_is_still_reported(self):
        """NEGATIVE CONTROL: paging does not hide a real mismatch."""
        from backend.backend.handlers.assets import uploadFile as uf
        s3 = MagicMock()
        s3.list_parts = _paged_list_parts(1199)
        with patch.object(uf, "s3", s3):
            size, ok, error = uf.calculate_total_file_size_from_parts("b", "k", "u", _expected(1200))

        assert ok is False
        assert size == 0
        assert "Missing parts: [1200]" in error

    def test_a_single_page_upload_makes_one_call(self):
        from backend.backend.handlers.assets import uploadFile as uf
        s3 = MagicMock()
        s3.list_parts = _paged_list_parts(3)
        with patch.object(uf, "s3", s3):
            size, ok, _ = uf.calculate_total_file_size_from_parts("b", "k", "u", _expected(3))

        assert ok is True
        assert size == 3 * PART_SIZE
        assert s3.list_parts.call_count == 1
