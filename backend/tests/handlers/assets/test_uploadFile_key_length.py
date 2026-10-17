# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #407, write paths: a resolved S3 key over the 1024-byte limit is refused before the first S3 call.

The request models bound the asset-relative key a caller sends, but S3 measures the RESOLVED key: the
asset's base key plus the relative part and, for an upload in flight, the ``temp-uploads/`` prefix on
top. A relative key inside the limit on its own can therefore resolve to a key S3 refuses. Before this
guard, upload initialization let that key reach ``create_multipart_upload``; S3 answered
``KeyTooLongError``, nothing caught it, and the request was a 500 -- with the batch's earlier files left
as orphaned multipart uploads. ``create_folder``, ``move_file`` and ``copy_file`` answered a misleading
400 ("Error creating folder." / "Error checking destination file." / "Error accessing destination
path...") after one wasted S3 round trip.

Each guard is exercised the same way: the refusal is the generic message the stream and download
guards already use, the key is not echoed, and the S3 client mock records no call. A paired short-key
control at the same base key proves the guard refuses the length and not the shape; a multibyte case --
3-byte characters, inside the limit in characters and over it in bytes -- proves the guard measures what
S3 measures. The fixtures import nothing that the fix introduced, so on the base tree this file collects
and the refusal tests fail on their assertions (negative control recorded in the report).
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.common.s3PathPatterns import TEMPORARY_UPLOAD_PREFIX
from backend.tests.handlers.assets import test_uploadFile_preview_extension_init as upload_init
from backend.tests.handlers.assets import test_assetFiles_head_miss_archived_check as asset_files

MESSAGE = "File key exceeds the maximum S3 object key length"
S3_KEY_LIMIT_BYTES = 1024

DATABASE_ID = "db-001"
ASSET_ID = "asset1"
CLAIMS = {"tokens": ["alice@corp"], "roles": []}

# 60-byte asset base key, slash-terminated like createAsset produces.
ASSET_BASE_KEY = "asset-" + ("k" * 53) + "/"
assert len(ASSET_BASE_KEY.encode("utf-8")) == 60

# A 3-byte UTF-8 character (U+4E2D).
THREE_BYTE_CHAR = "\u4e2d"
assert len(THREE_BYTE_CHAR.encode("utf-8")) == 3


def _ascii_key(prefix, total_bytes, suffix):
    """A key of exactly ``total_bytes`` bytes shaped ``<prefix><filler><suffix>``."""
    filler = total_bytes - len(prefix) - len(suffix)
    assert filler > 0
    key = prefix + ("a" * filler) + suffix
    assert len(key.encode("utf-8")) == total_bytes
    return key


def _multibyte_key(prefix, char_count, suffix):
    key = prefix + (THREE_BYTE_CHAR * char_count) + suffix
    assert len(key.encode("utf-8")) <= S3_KEY_LIMIT_BYTES, "the relative part must pass the validator"
    return key


def _over_in_bytes_only(resolved_key):
    return len(resolved_key) < S3_KEY_LIMIT_BYTES < len(resolved_key.encode("utf-8"))


# Relative keys as the request body carries them (leading slash). Each passes the request model's
# own bound on the relative part; only a guard on the RESOLVED key can refuse it.
SHORT_FILE_KEY = "/scans/pump.glb"
LONG_FILE_KEY = _ascii_key("/scans/", 1000, ".glb")                  # 1059 bytes resolved
MULTIBYTE_FILE_KEY = _multibyte_key("/scans/", 330, ".glb")          # 1001 bytes, 341 chars
assert _over_in_bytes_only(ASSET_BASE_KEY + MULTIBYTE_FILE_KEY[1:])

SHORT_FOLDER_KEY = "/scans/raw/"
LONG_FOLDER_KEY = _ascii_key("/scans/", 1000, "/")                   # 1059 bytes resolved
MULTIBYTE_FOLDER_KEY = _multibyte_key("/scans/", 330, "/")           # 998 bytes, 338 chars
assert _over_in_bytes_only(ASSET_BASE_KEY + MULTIBYTE_FOLDER_KEY[1:])

# Resolved FINAL key inside the limit (60 + 960 = 1020 bytes) while the TEMPORARY key -- the one
# create_multipart_upload is actually issued for -- is over it (13 + 1020 = 1033 bytes).
TEMP_ONLY_FILE_KEY = _ascii_key("/scans/", 961, ".glb")
assert len((ASSET_BASE_KEY + TEMP_ONLY_FILE_KEY[1:]).encode("utf-8")) == 1020
assert len((TEMPORARY_UPLOAD_PREFIX + ASSET_BASE_KEY + TEMP_ONLY_FILE_KEY[1:]).encode("utf-8")) == 1033


def _assert_not_echoed(text, key):
    assert key[8:48] not in text, "the response echoed the key"


# ------------------------------------------------------------------ uploadFile: initialize

def _upload_asset():
    return {
        "databaseId": DATABASE_ID,
        "assetId": ASSET_ID,
        "bucketId": "bucket-1",
        "assetLocation": {"Key": ASSET_BASE_KEY},
    }


def _upload_request(*relative_keys):
    from backend.backend.models.assetsV3 import InitializeUploadRequestModel
    return InitializeUploadRequestModel(
        assetId=ASSET_ID,
        databaseId=DATABASE_ID,
        uploadType="assetFile",
        files=[{"relativeKey": k, "file_size": 2048} for k in relative_keys],
    )


def _upload_event(*relative_keys):
    return {
        "body": json.dumps({
            "assetId": ASSET_ID,
            "databaseId": DATABASE_ID,
            "uploadType": "assetFile",
            "files": [{"relativeKey": k, "file_size": 2048} for k in relative_keys],
        }),
        "requestContext": {"http": {"path": "/uploads", "method": "POST"}},
        "headers": {},
    }


@pytest.mark.unit
class TestInitializeUploadResolvedKeyLength:
    """The guard sits in the pre-loop validation block: a refusal creates NO multipart upload for any
    file in the batch, so the over-long file is placed LAST -- the only position that discriminates
    between "refused before its own upload" and "refused before any upload"."""

    def test_an_over_long_temp_key_in_last_position_creates_no_upload(self):
        with upload_init._initialize_upload_env(_upload_asset()) as (uf, mock_s3):
            with pytest.raises(uf.VAMSGeneralErrorResponse) as err:
                uf.initialize_upload(_upload_request(SHORT_FILE_KEY, LONG_FILE_KEY), CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.create_multipart_upload.assert_not_called()
        mock_s3.abort_multipart_upload.assert_not_called()

    def test_the_refusal_is_a_generic_400_through_the_handler(self):
        with upload_init._initialize_upload_env(_upload_asset()) as (uf, mock_s3), \
                patch.object(uf, "request_to_claims", return_value=CLAIMS), \
                patch.object(uf, "CasbinEnforcer", MagicMock()):
            response = uf.lambda_handler(_upload_event(SHORT_FILE_KEY, LONG_FILE_KEY), MagicMock())

        assert response["statusCode"] == 400
        assert MESSAGE in json.loads(response["body"])["message"]
        _assert_not_echoed(response["body"], LONG_FILE_KEY)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_a_final_key_inside_the_limit_whose_temp_key_is_over_it_is_refused(self):
        """The temporary object is the one S3 is asked to create, so its key is the one that matters."""
        with upload_init._initialize_upload_env(_upload_asset()) as (uf, mock_s3):
            with pytest.raises(uf.VAMSGeneralErrorResponse) as err:
                uf.initialize_upload(_upload_request(TEMP_ONLY_FILE_KEY), CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_the_guard_measures_bytes_not_characters(self):
        with upload_init._initialize_upload_env(_upload_asset()) as (uf, mock_s3):
            with pytest.raises(uf.VAMSGeneralErrorResponse) as err:
                uf.initialize_upload(_upload_request(SHORT_FILE_KEY, MULTIBYTE_FILE_KEY), CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_a_short_key_at_the_same_base_still_initializes(self):
        """Paired control: the guard refuses the length, not the base key or the batch shape."""
        with upload_init._initialize_upload_env(_upload_asset()) as (uf, mock_s3):
            response = uf.initialize_upload(_upload_request(SHORT_FILE_KEY, "/scans/pump.e57"), CLAIMS)

        assert response.uploadId
        assert len(response.files) == 2
        created = [c.kwargs["Key"] for c in mock_s3.create_multipart_upload.call_args_list]
        assert created == [
            TEMPORARY_UPLOAD_PREFIX + ASSET_BASE_KEY + "scans/pump.glb",
            TEMPORARY_UPLOAD_PREFIX + ASSET_BASE_KEY + "scans/pump.e57",
        ]


# ------------------------------------------------------- assetFiles: createFolder / move / copy

BUCKET = "asset-bucket"


def _file_ops_asset():
    return {
        "databaseId": DATABASE_ID,
        "assetId": ASSET_ID,
        "bucketId": "bucket-1",
        "assetLocation": {"Key": ASSET_BASE_KEY},
    }


def _missing(operation="HeadObject"):
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, operation)


def _wire_file_ops(monkeypatch):
    """Patch everything short of the S3 client so only the resolved-key guard and the first S3 call
    are in play. Returns (module, s3 client mock)."""
    af = asset_files._load_asset_files()
    mock_s3 = MagicMock()
    monkeypatch.setattr(af, "get_asset_with_permissions",
                        lambda databaseId, assetId, op, claims: _file_ops_asset())
    monkeypatch.setattr(af, "get_asset_s3_location", lambda asset: (BUCKET, ASSET_BASE_KEY))
    monkeypatch.setattr(af, "get_default_bucket_details",
                        lambda bucketId: {"bucketId": bucketId, "bucketName": BUCKET, "baseAssetsPrefix": ""})
    monkeypatch.setattr(af, "is_file_archived", lambda bucket, key, version_id=None: False)
    monkeypatch.setattr(af, "validate_cross_asset_permissions", lambda source, dest, claims: True)
    monkeypatch.setattr(af, "s3_client", mock_s3)
    return af, mock_s3


def _folder_request(relative_key):
    from backend.backend.models.assetsV3 import CreateFolderRequestModel
    return CreateFolderRequestModel(relativeKey=relative_key)


@pytest.mark.unit
class TestCreateFolderResolvedKeyLength:

    def test_an_over_long_resolved_folder_key_is_refused_before_put_object(self, monkeypatch):
        af, mock_s3 = _wire_file_ops(monkeypatch)

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.create_folder(DATABASE_ID, ASSET_ID, _folder_request(LONG_FOLDER_KEY), CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.put_object.assert_not_called()

    def test_the_guard_measures_bytes_not_characters(self, monkeypatch):
        af, mock_s3 = _wire_file_ops(monkeypatch)

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.create_folder(DATABASE_ID, ASSET_ID, _folder_request(MULTIBYTE_FOLDER_KEY), CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.put_object.assert_not_called()

    def test_a_short_folder_at_the_same_base_is_still_created(self, monkeypatch):
        """Paired control."""
        af, mock_s3 = _wire_file_ops(monkeypatch)

        response = af.create_folder(DATABASE_ID, ASSET_ID, _folder_request(SHORT_FOLDER_KEY), CLAIMS)

        assert response.relativeKey == SHORT_FOLDER_KEY
        assert mock_s3.put_object.call_args.kwargs["Key"] == ASSET_BASE_KEY + SHORT_FOLDER_KEY[1:]


@pytest.mark.unit
class TestMoveFileResolvedKeyLength:

    def test_an_over_long_resolved_destination_is_refused_before_any_s3_call(self, monkeypatch):
        af, mock_s3 = _wire_file_ops(monkeypatch)

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.move_file(DATABASE_ID, ASSET_ID, SHORT_FILE_KEY, LONG_FILE_KEY, CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.head_object.assert_not_called()
        mock_s3.list_object_versions.assert_not_called()

    def test_the_guard_measures_bytes_not_characters(self, monkeypatch):
        af, mock_s3 = _wire_file_ops(monkeypatch)

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.move_file(DATABASE_ID, ASSET_ID, SHORT_FILE_KEY, MULTIBYTE_FILE_KEY, CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.head_object.assert_not_called()

    def test_a_short_destination_at_the_same_base_still_reaches_s3(self, monkeypatch):
        """Paired control: the request proceeds to its first S3 call, the source lookup."""
        af, mock_s3 = _wire_file_ops(monkeypatch)
        mock_s3.head_object.side_effect = _missing()

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.move_file(DATABASE_ID, ASSET_ID, SHORT_FILE_KEY, "/scans/pump-v2.glb", CLAIMS)

        assert "Source file not found." in str(err.value)
        assert mock_s3.head_object.call_args.kwargs["Key"] == ASSET_BASE_KEY + SHORT_FILE_KEY[1:]


@pytest.mark.unit
class TestCopyFileResolvedKeyLength:

    def test_an_over_long_resolved_destination_is_refused_before_any_s3_call(self, monkeypatch):
        af, mock_s3 = _wire_file_ops(monkeypatch)

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.copy_file(DATABASE_ID, ASSET_ID, SHORT_FILE_KEY, LONG_FILE_KEY, None, None, CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.head_object.assert_not_called()
        mock_s3.copy_object.assert_not_called()

    def test_the_guard_measures_bytes_not_characters(self, monkeypatch):
        af, mock_s3 = _wire_file_ops(monkeypatch)

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.copy_file(DATABASE_ID, ASSET_ID, SHORT_FILE_KEY, MULTIBYTE_FILE_KEY, None, None, CLAIMS)

        assert MESSAGE in str(err.value)
        mock_s3.head_object.assert_not_called()

    def test_a_short_destination_at_the_same_base_still_reaches_s3(self, monkeypatch):
        """Paired control: the request proceeds to its first S3 call, the source lookup."""
        af, mock_s3 = _wire_file_ops(monkeypatch)
        mock_s3.head_object.side_effect = _missing()

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.copy_file(DATABASE_ID, ASSET_ID, SHORT_FILE_KEY, "/scans/pump-copy.glb", None, None, CLAIMS)

        assert "Source file not found." in str(err.value)
        assert mock_s3.head_object.call_args.kwargs["Key"] == ASSET_BASE_KEY + SHORT_FILE_KEY[1:]


# ------------------------------------------------------------ assetFiles: through the handlers

def _rest_event(route, body):
    path = f"/database/{DATABASE_ID}/assets/{ASSET_ID}/{route}"
    return {
        "path": path,
        "httpMethod": "POST",
        "requestContext": {"identity": {"sourceIp": "10.0.0.7"}, "path": path, "httpMethod": "POST"},
        "pathParameters": {"databaseId": DATABASE_ID, "assetId": ASSET_ID},
        "queryStringParameters": None,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


FILE_OP_ROUTES = [
    ("createFolder", "handle_create_folder", {"relativeKey": LONG_FOLDER_KEY}, LONG_FOLDER_KEY, "put_object"),
    ("moveFile", "handle_move_file",
     {"sourcePath": SHORT_FILE_KEY, "destinationPath": LONG_FILE_KEY}, LONG_FILE_KEY, "head_object"),
    ("copyFile", "handle_copy_file",
     {"sourcePath": SHORT_FILE_KEY, "destinationPath": LONG_FILE_KEY}, LONG_FILE_KEY, "head_object"),
]


@pytest.mark.unit
class TestFileOperationHandlersAnswerAGeneric400:

    @pytest.mark.parametrize("route,handler,body,long_key,first_s3_call", FILE_OP_ROUTES,
                             ids=[r[0] for r in FILE_OP_ROUTES])
    def test_the_refusal_is_a_generic_400_that_does_not_echo_the_key(
            self, monkeypatch, route, handler, body, long_key, first_s3_call):
        af, mock_s3 = _wire_file_ops(monkeypatch)
        monkeypatch.setattr(af, "request_to_claims", lambda event: CLAIMS)
        monkeypatch.setattr(af, "CasbinEnforcer", MagicMock())

        response = getattr(af, handler)(_rest_event(route, body), MagicMock())

        assert response["statusCode"] == 400
        assert MESSAGE in json.loads(response["body"])["message"]
        _assert_not_echoed(response["body"], long_key)
        getattr(mock_s3, first_s3_call).assert_not_called()
