# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multipart part URLs are signed with the presigned URL timeout as a number of seconds.

`PRESIGNED_URL_TIMEOUT_SECONDS` arrives as a string. The download and stream handlers convert it with
`int()`, which accepts surrounding whitespace and a leading sign. The upload handler signs its part
URLs with the same setting, so it converts it the same way; signing with the raw string would write a
value such as ``" 3600"`` into ``X-Amz-Expires`` unchanged, so the part URLs would break while
downloads signed from the same setting keep working.
"""

import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: E402


def _signed_expiry(expiration):
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "https://signed.example/part"
    with patch.object(uploadFile, "s3", s3):
        uploadFile.generate_presigned_url("a/model.glb", "upload-1", 1, "bucket-1", expiration)
    return s3.generate_presigned_url.call_args.kwargs["ExpiresIn"]


@pytest.mark.unit
class TestPartUrlExpiry:
    @pytest.mark.parametrize("configured", ["3600", " 3600", "+3600", "3600\n", "3600\r\n"])
    def test_the_configured_string_is_signed_as_seconds(self, configured):
        expires_in = _signed_expiry(configured)

        assert expires_in == 3600
        assert type(expires_in) is int

    def test_the_conversion_matches_the_download_handlers(self):
        """Paired control: the value the part URLs use is the one `int()` gives the other handlers."""
        configured = " 86400\n"

        assert _signed_expiry(configured) == int(configured)

    def test_a_value_int_rejects_is_still_rejected(self):
        """Negative control: a value no handler can convert still fails rather than signing."""
        with pytest.raises(ValueError):
            _signed_expiry("1h")
