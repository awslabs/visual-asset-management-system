# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #407: a resolved S3 key over the 1024-byte limit is refused with a 400 before any S3 call.

The caller supplies an ASSET-RELATIVE key; the handler prepends the asset's base key (and, for
auxiliary previews, the database id) before calling S3. The validators bound the relative part, but
the limit S3 enforces is on the RESOLVED key, so a relative path that is inside the limit on its own
can be over it once the prefix is added. Each handler therefore re-checks the resolved key right after
resolving it, and the request ends there: no ``head_object``, no ``get_object``, no presigned URL.

The fixture shape is the one named in the brief -- a 1000-byte relative path against a 60-byte asset
base key, 1060 bytes resolved -- so the relative part passes the validator (``RELATIVE_FILE_PATH`` /
``ASSET_AUXILIARYPREVIEW_PATH`` / the download request model) and only the handler-level guard can
refuse it. Each class carries a paired control at the same base key with a short path, which proves
the guard refuses the length and not the shape, and one multibyte case -- 3-byte characters, inside
the limit in characters and over it in bytes once resolved -- which proves the guard measures what S3
measures; a guard rewritten to ``len(key)`` passes the ASCII cases and fails that one.

Negative control (recorded in the report, not in this file): with the fix stashed, every refusal test
here reaches the S3 mock -- ``get_object`` / ``head_object`` is called with the over-long key -- and
the request is answered from the mock rather than with a 400.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.assets import test_streamAsset_range_header_casing as asset_stream
from backend.tests.handlers.assets import test_streamAuxiliaryPreviewAsset_range_header_casing as aux_stream
from backend.tests.handlers.assets import test_downloadAsset_bulk as download

MESSAGE = "File key exceeds the maximum S3 object key length"

# 60-byte asset base key, slash-terminated like createAsset produces.
ASSET_BASE_KEY = "asset-" + ("k" * 53) + "/"
assert len(ASSET_BASE_KEY.encode("utf-8")) == 60


def _relative_path(prefix, total_bytes, suffix=""):
    """A relative path (no leading slash) of exactly ``total_bytes`` bytes."""
    filler = total_bytes - len(prefix) - len(suffix)
    assert filler > 0
    path = prefix + ("a" * filler) + suffix
    assert len(path.encode("utf-8")) == total_bytes
    return path


# 1000 bytes as the route delivers it (no leading slash); the handler adds one before validating.
LONG_STREAM_PATH = _relative_path("scans/", 1000, ".glb")
SHORT_STREAM_PATH = "scans/pump.glb"

# Same budget for the auxiliary preview route; the validator needs the '/preview/' segment.
LONG_AUX_PATH = _relative_path("scans/pump.e57/preview/", 1000, ".bin")
SHORT_AUX_PATH = "scans/pump.e57/preview/r/octree.bin"

# Download keys carry the leading slash in the request body.
LONG_DOWNLOAD_KEY = "/" + _relative_path("scans/", 999, ".glb")
SHORT_DOWNLOAD_KEY = "/scans/pump.glb"
assert len(LONG_DOWNLOAD_KEY.encode("utf-8")) == 1000

# A 3-byte UTF-8 character (U+4E2D). The multibyte fixtures below are inside the limit in CHARACTERS
# at every layer -- relative part and resolved key alike -- and over it only in BYTES once the asset
# prefix is added. A guard rewritten to count characters lets every one of them through.
THREE_BYTE_CHAR = "\u4e2d"
assert len(THREE_BYTE_CHAR.encode("utf-8")) == 3


def _multibyte_path(prefix, char_count, suffix):
    """A relative path of ``char_count`` 3-byte characters between ``prefix`` and ``suffix``."""
    return prefix + (THREE_BYTE_CHAR * char_count) + suffix


def _assert_over_in_bytes_only(resolved_key):
    assert len(resolved_key) < 1024, "fixture must be inside the limit when counted in characters"
    assert len(resolved_key.encode("utf-8")) > 1024, "fixture must be over the limit in bytes"


# 6 + 330*3 + 4 = 1000 bytes (340 characters): passes the validator; 1060 bytes resolved.
MULTIBYTE_STREAM_PATH = _multibyte_path("scans/", 330, ".glb")
_assert_over_in_bytes_only(ASSET_BASE_KEY + MULTIBYTE_STREAM_PATH)

# 23 + 325*3 + 4 = 1002 bytes (352 characters); 1066 bytes once 'db1/' and the base key are added.
MULTIBYTE_AUX_PATH = _multibyte_path("scans/pump.e57/preview/", 325, ".bin")
_assert_over_in_bytes_only("db1/" + ASSET_BASE_KEY + MULTIBYTE_AUX_PATH)

# 7 + 330*3 + 4 = 1001 bytes (341 characters); 1060 bytes resolved.
MULTIBYTE_DOWNLOAD_KEY = "/" + _multibyte_path("scans/", 330, ".glb")
_assert_over_in_bytes_only(ASSET_BASE_KEY + MULTIBYTE_DOWNLOAD_KEY.lstrip("/"))


def _assert_no_s3_call(mock_s3):
    assert not mock_s3.head_object.called, "head_object was called with an over-long key"
    assert not mock_s3.get_object.called, "get_object was called with an over-long key"
    assert not mock_s3.generate_presigned_url.called, "a presigned URL was minted for an over-long key"


# --------------------------------------------------------------------------- streamAsset

def _stream_event(proxy, method):
    event = asset_stream._rest_event({"Accept": "*/*"}, method=method)
    event["pathParameters"]["proxy"] = proxy
    event["path"] = f"/database/db1/assets/asset1/download/stream/{proxy}"
    event["requestContext"]["path"] = event["path"]
    return event


def _stream_wire(m):
    mock_s3 = asset_stream._wire(m)
    m.get_asset_details = MagicMock(return_value={
        "databaseId": "db1", "assetId": "asset1", "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": ASSET_BASE_KEY},
    })
    mock_s3.head_object.return_value = {"ContentType": "model/gltf-binary", "ContentLength": 1024}
    return mock_s3


@pytest.mark.unit
class TestStreamAssetResolvedKeyLength:

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_resolved_key_over_the_limit_is_a_400_and_never_reaches_s3(self, method):
        m = asset_stream._load()
        mock_s3 = _stream_wire(m)

        response = m.lambda_handler(_stream_event(LONG_STREAM_PATH, method), MagicMock())

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["message"] == MESSAGE
        assert LONG_STREAM_PATH[:40] not in response["body"], "the response echoed the key"
        _assert_no_s3_call(mock_s3)

    def test_the_get_refusal_carries_the_streaming_cors_headers(self):
        """A browser viewer must be able to read the 400, like every other stream error."""
        m = asset_stream._load()
        _stream_wire(m)

        response = m.lambda_handler(_stream_event(LONG_STREAM_PATH, "GET"), MagicMock())

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"
        assert response["headers"]["Access-Control-Allow-Headers"] == "Range"

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_the_guard_measures_bytes_not_characters(self, method):
        """A resolved key under 1024 characters but over 1024 bytes is still refused."""
        m = asset_stream._load()
        mock_s3 = _stream_wire(m)

        response = m.lambda_handler(_stream_event(MULTIBYTE_STREAM_PATH, method), MagicMock())

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == MESSAGE
        _assert_no_s3_call(mock_s3)

    @pytest.mark.parametrize("method,s3_call,status", [("GET", "get_object", 307), ("HEAD", "head_object", 200)])
    def test_a_short_key_at_the_same_base_still_reaches_s3(self, method, s3_call, status):
        """Paired control: the guard refuses the length, not the base key or the shape."""
        m = asset_stream._load()
        mock_s3 = _stream_wire(m)

        response = m.lambda_handler(_stream_event(SHORT_STREAM_PATH, method), MagicMock())

        assert response["statusCode"] == status
        assert getattr(mock_s3, s3_call).call_args.kwargs["Key"] == ASSET_BASE_KEY + SHORT_STREAM_PATH


# ------------------------------------------------------- streamAuxiliaryPreviewAsset

def _aux_event(proxy, method):
    event = aux_stream._rest_event({"Accept": "*/*"})
    event["httpMethod"] = method
    event["requestContext"]["httpMethod"] = method
    event["pathParameters"]["proxy"] = proxy
    event["path"] = f"/database/db1/assets/asset1/auxiliaryPreviewAssets/stream/{proxy}"
    event["requestContext"]["path"] = event["path"]
    return event


def _aux_wire(m):
    mock_s3 = aux_stream._wire(m)
    m.get_asset_details = MagicMock(return_value={
        "databaseId": "db1", "assetId": "asset1", "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": ASSET_BASE_KEY},
    })
    mock_s3.head_object.return_value = {"ContentType": "application/octet-stream", "ContentLength": 2048}
    return mock_s3


@pytest.mark.unit
class TestStreamAuxiliaryPreviewAssetResolvedKeyLength:

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_resolved_key_over_the_limit_is_a_400_and_never_reaches_s3(self, method):
        m = aux_stream._load()
        mock_s3 = _aux_wire(m)

        response = m.lambda_handler(_aux_event(LONG_AUX_PATH, method), MagicMock())

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == MESSAGE
        _assert_no_s3_call(mock_s3)

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_the_guard_measures_bytes_not_characters(self, method):
        """A resolved key under 1024 characters but over 1024 bytes is still refused."""
        m = aux_stream._load()
        mock_s3 = _aux_wire(m)

        response = m.lambda_handler(_aux_event(MULTIBYTE_AUX_PATH, method), MagicMock())

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == MESSAGE
        _assert_no_s3_call(mock_s3)

    @pytest.mark.parametrize("method,s3_call,status", [("GET", "get_object", 307), ("HEAD", "head_object", 200)])
    def test_a_short_key_at_the_same_base_still_reaches_s3(self, method, s3_call, status):
        """Paired control: the database-scoped prefix is still applied and the request proceeds."""
        m = aux_stream._load()
        mock_s3 = _aux_wire(m)

        response = m.lambda_handler(_aux_event(SHORT_AUX_PATH, method), MagicMock())

        assert response["statusCode"] == status
        assert getattr(mock_s3, s3_call).call_args.kwargs["Key"] == f"db1/{ASSET_BASE_KEY}{SHORT_AUX_PATH}"


# ------------------------------------------------------------------------ downloadAsset

def _download_event(body):
    return {
        "resource": "/database/{databaseId}/assets/{assetId}/download",
        "path": "/database/db1/assets/asset1/download",
        "httpMethod": "POST",
        "headers": {"Content-Type": "application/json"},
        "pathParameters": {"databaseId": "db1", "assetId": "asset1"},
        "queryStringParameters": None,
        "requestContext": {
            "identity": {"sourceIp": "10.0.0.7"},
            "path": "/database/db1/assets/asset1/download",
            "httpMethod": "POST",
        },
        "body": json.dumps(body),
    }


def _download_wire(m):
    download._wire_asset_context(m)
    m.get_asset_details = MagicMock(return_value={
        "databaseId": "db1", "assetId": "asset1", "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": ASSET_BASE_KEY},
    })
    m.request_to_claims = MagicMock(return_value={"tokens": ["test-user"], "roles": [], "mfaEnabled": False})
    m.CasbinEnforcer = MagicMock()
    return m.s3


@pytest.mark.unit
class TestDownloadAssetResolvedKeyLength:

    def test_a_resolved_key_over_the_limit_is_a_400_and_never_reaches_s3(self):
        m = download._load()
        mock_s3 = _download_wire(m)

        response = m.lambda_handler(
            _download_event({"downloadType": "assetFile", "key": LONG_DOWNLOAD_KEY}), MagicMock())

        assert response["statusCode"] == 400
        assert MESSAGE in json.loads(response["body"])["message"]
        assert LONG_DOWNLOAD_KEY[:40] not in response["body"], "the response echoed the key"
        _assert_no_s3_call(mock_s3)

    def test_the_guard_measures_bytes_not_characters(self):
        """A resolved key under 1024 characters but over 1024 bytes is still refused."""
        m = download._load()
        mock_s3 = _download_wire(m)

        response = m.lambda_handler(
            _download_event({"downloadType": "assetFile", "key": MULTIBYTE_DOWNLOAD_KEY}), MagicMock())

        assert response["statusCode"] == 400
        assert MESSAGE in json.loads(response["body"])["message"]
        _assert_no_s3_call(mock_s3)

    def test_a_short_key_at_the_same_base_is_still_signed(self):
        """Paired control: the guard refuses the length, not the base key."""
        m = download._load()
        mock_s3 = _download_wire(m)

        response = m.lambda_handler(
            _download_event({"downloadType": "assetFile", "key": SHORT_DOWNLOAD_KEY}), MagicMock())

        assert response["statusCode"] == 200
        signed = mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"]
        assert signed == ASSET_BASE_KEY + SHORT_DOWNLOAD_KEY.lstrip("/")

    def test_bulk_skips_only_the_over_long_key(self):
        """The bulk path reports the refusal per entry and still signs the other keys."""
        m = download._load()
        mock_s3 = _download_wire(m)

        response = m.lambda_handler(
            _download_event({"downloadType": "assetFile", "keys": [SHORT_DOWNLOAD_KEY, LONG_DOWNLOAD_KEY]}),
            MagicMock())

        assert response["statusCode"] == 200
        by_key = {f["key"]: f for f in json.loads(response["body"])["files"]}
        assert by_key[SHORT_DOWNLOAD_KEY]["success"] is True
        assert by_key[LONG_DOWNLOAD_KEY]["success"] is False
        assert MESSAGE in by_key[LONG_DOWNLOAD_KEY]["error"]
        headed = [c.kwargs["Key"] for c in mock_s3.head_object.call_args_list]
        signed = [c.kwargs["Params"]["Key"] for c in mock_s3.generate_presigned_url.call_args_list]
        assert headed == signed == [ASSET_BASE_KEY + SHORT_DOWNLOAD_KEY.lstrip("/")]
