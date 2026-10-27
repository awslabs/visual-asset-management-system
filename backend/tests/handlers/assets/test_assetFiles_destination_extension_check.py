# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Copy and move check the destination file name against the blocked file extensions.

The blocked-extension helper is replaced by an always-True stand-in in tests/mocks/common/s3.py,
so these tests load the real ``validateUnallowedFileExtensionAndContentType`` from common/s3.py,
with the real lists from common/constants.py, and patch it into assetFiles.
"""

import os
import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "asset-bucket"
BASE_KEY = "db1/a1/"
CLAIMS = {"tokens": ["alice"]}
REFUSED = "File type not allowed for the destination file name."

_S3_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "s3.py")


def _load_real_blocklist_helper():
    """Read the real validateUnallowedFileExtensionAndContentType from source with the real lists."""
    from backend.backend.common.constants import (
        UNALLOWED_FILE_EXTENSION_LIST,
        UNALLOWED_MIME_LIST,
    )

    with open(_S3_MODULE_PATH, encoding="utf-8") as f:
        source = f.read()
    start = source.index("def validateUnallowedFileExtensionAndContentType(")
    end = source.index("\ndef validateS3AssetExtensionsAndContentType(", start)
    namespace = {
        "os": os,
        "logger": MagicMock(),
        "UNALLOWED_MIME_LIST": UNALLOWED_MIME_LIST,
        "UNALLOWED_FILE_EXTENSION_LIST": UNALLOWED_FILE_EXTENSION_LIST,
    }
    exec(compile(source[start:end], _S3_MODULE_PATH, "exec"), namespace)
    return namespace["validateUnallowedFileExtensionAndContentType"]


@pytest.fixture
def af(monkeypatch):
    """assetFiles with the real blocklist helper and stubbed asset, bucket and S3 access.

    head_object reports the source as missing, so a destination that passes the name check
    stops at "Source file not found." without any write.
    """
    from backend.backend.handlers.assets import assetFiles as module

    real_helper = MagicMock(side_effect=_load_real_blocklist_helper())
    monkeypatch.setattr(module, "validateUnallowedFileExtensionAndContentType", real_helper)
    monkeypatch.setattr(module, "get_asset_with_permissions",
                        lambda databaseId, assetId, op, claims: {"assetId": assetId})
    monkeypatch.setattr(module, "get_asset_s3_location", lambda asset: (BUCKET, BASE_KEY))
    monkeypatch.setattr(module, "is_file_archived", lambda bucket, key, version_id=None: False)
    s3_client = MagicMock()
    s3_client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
    monkeypatch.setattr(module, "s3_client", s3_client)
    monkeypatch.setattr(module, "copy_s3_object", MagicMock(return_value=True))
    monkeypatch.setattr(module, "move_s3_object", MagicMock(return_value=True))
    return module


BLOCKED_DESTINATIONS = ["/payload.exe", "/b.EXE", "/dir/run.Bat", "/notes.txt.exe", "/payload.exe."]


@pytest.mark.unit
class TestMoveChecksTheDestinationName:
    @pytest.mark.parametrize("dest", BLOCKED_DESTINATIONS)
    def test_a_destination_with_a_blocked_extension_is_refused_before_s3_is_touched(self, af, dest):
        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.move_file("db1", "a1", "/a.txt", dest, CLAIMS)

        assert REFUSED in str(err.value)
        af.validateUnallowedFileExtensionAndContentType.assert_called_once_with(
            BASE_KEY + dest.lstrip("/"), "")
        af.s3_client.head_object.assert_not_called()
        af.move_s3_object.assert_not_called()

    def test_a_rename_in_place_to_a_blocked_extension_is_refused(self, af):
        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.move_file("db1", "a1", "/models/part.txt", "/models/part.exe", CLAIMS)

        assert REFUSED in str(err.value)
        af.move_s3_object.assert_not_called()

    def test_an_allowed_destination_passes_the_check(self, af):
        """POSITIVE CONTROL: the name check passes and the move goes on to look up the source."""
        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.move_file("db1", "a1", "/a.txt", "/b.glb", CLAIMS)

        assert "Source file not found." in str(err.value)
        af.s3_client.head_object.assert_called_once()


@pytest.mark.unit
class TestCopyChecksTheDestinationName:
    @pytest.mark.parametrize("dest", BLOCKED_DESTINATIONS)
    def test_a_destination_with_a_blocked_extension_is_refused_before_s3_is_touched(self, af, dest):
        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.copy_file("db1", "a1", "/a.txt", dest, None, None, CLAIMS)

        assert REFUSED in str(err.value)
        af.s3_client.head_object.assert_not_called()
        af.copy_s3_object.assert_not_called()

    def test_a_cross_asset_destination_with_a_blocked_extension_is_refused(self, af, monkeypatch):
        monkeypatch.setattr(af, "validate_cross_asset_permissions", lambda *args: True)

        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.copy_file("db1", "a1", "/a.txt", "/a.exe", "a2", None, CLAIMS)

        assert REFUSED in str(err.value)
        af.copy_s3_object.assert_not_called()

    def test_an_allowed_destination_passes_the_check(self, af):
        """POSITIVE CONTROL: the name check passes and the copy goes on to look up the source."""
        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.copy_file("db1", "a1", "/a.txt", "/b.glb", None, None, CLAIMS)

        assert "Source file not found." in str(err.value)
        af.s3_client.head_object.assert_called_once()

    def test_the_refusal_echoes_no_file_name(self, af):
        with pytest.raises(af.VAMSGeneralErrorResponse) as err:
            af.copy_file("db1", "a1", "/a.txt", "/secret-name.exe", None, None, CLAIMS)

        assert "secret-name" not in str(err.value)
