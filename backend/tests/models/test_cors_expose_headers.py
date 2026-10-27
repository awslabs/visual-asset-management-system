# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cross-origin callers need Access-Control-Expose-Headers for Location/ETag (#847)."""

from models.common import CORS_EXPOSE_HEADERS, commonHeaders, streamCorsHeaders


def test_common_headers_exposes_location_and_etag():
    headers = commonHeaders()
    exposed = {h.strip() for h in headers["Access-Control-Expose-Headers"].split(",")}
    assert "Retry-After" in exposed
    assert "Location" in exposed
    assert "ETag" in exposed


def test_stream_cors_headers_exposes_stream_metadata():
    headers = streamCorsHeaders()
    exposed = {h.strip() for h in headers["Access-Control-Expose-Headers"].split(",")}
    for name in (
        "Location",
        "ETag",
        "Last-Modified",
        "Accept-Ranges",
        "Content-Range",
        "Content-Encoding",
        "x-amz-version-id",
        "x-amz-storage-class",
    ):
        assert name in exposed
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Access-Control-Allow-Headers"] == "Range"
    assert CORS_EXPOSE_HEADERS == headers["Access-Control-Expose-Headers"]
