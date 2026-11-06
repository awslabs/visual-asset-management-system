# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-version resolvers never echo the requested version id, alias or file key (#1134).

Their VAMSGeneralErrorResponse text reaches API callers verbatim through ``str(e)`` in the download,
stream and zip download handlers, so a caller-supplied value (which can be any string up to the
model's bound) was reflected into the response body. Each refusal must now carry a fixed message,
and the specifics go to the log only. Every test sends a distinctive marker and asserts it is absent
from the error text; on the old code each of them fails because the marker was interpolated.
"""

import pytest
from unittest.mock import MagicMock, patch

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder (see S27-TEST-001).
from backend.backend.handlers.assets import assetVersions as _assetVersions

# The class the module raises (imported there as models.common), not a second import of it.
VAMSGeneralErrorResponse = _assetVersions.VAMSGeneralErrorResponse

DB = "db1"
ASSET = "asset-1"
MARKER = "<script>zz-1134-marker</script>"


@pytest.fixture
def av():
    return _assetVersions


def _assert_no_echo(exc_info, expected):
    text = str(exc_info.value)
    assert MARKER not in text
    assert "zz-1134-marker" not in text
    # str() of the exception carries its "VAMS General Error: " prefix; the message is the suffix.
    assert text.endswith(expected)


@pytest.mark.unit
class TestResolverErrorsDoNotEchoInput:
    def test_missing_asset_version(self, av):
        table = MagicMock()
        table.get_item.return_value = {}
        with patch.object(av, "asset_versions_table", table):
            with pytest.raises(VAMSGeneralErrorResponse) as e:
                av.validate_asset_version_exists(DB, ASSET, MARKER)
        _assert_no_echo(e, getattr(av, "MSG_ASSET_VERSION_NOT_FOUND", "<constant missing>"))

    def test_version_without_files(self, av):
        with patch.object(av, "validate_asset_version_exists", return_value=True), \
                patch.object(av, "get_asset_file_versions", return_value=None):
            with pytest.raises(VAMSGeneralErrorResponse) as e:
                av.resolve_file_version_from_asset_version(DB, ASSET, MARKER, "/a.glb")
        _assert_no_echo(e, getattr(av, "MSG_ASSET_VERSION_NO_FILES", "<constant missing>"))

    def test_file_not_in_version(self, av):
        files = {"files": [{"relativeKey": "/other.glb", "versionId": "v1"}]}
        with patch.object(av, "validate_asset_version_exists", return_value=True), \
                patch.object(av, "get_asset_file_versions", return_value=files):
            with pytest.raises(VAMSGeneralErrorResponse) as e:
                av.resolve_file_version_from_asset_version(DB, ASSET, MARKER, f"/{MARKER}.glb")
        _assert_no_echo(e, getattr(av, "MSG_ASSET_VERSION_FILE_NOT_FOUND", "<constant missing>"))

    def test_file_without_recorded_version(self, av):
        files = {"files": [{"relativeKey": "/a.glb", "versionId": ""}]}
        with patch.object(av, "validate_asset_version_exists", return_value=True), \
                patch.object(av, "get_asset_file_versions", return_value=files):
            with pytest.raises(VAMSGeneralErrorResponse) as e:
                av.resolve_file_version_from_asset_version(DB, ASSET, MARKER, "/a.glb")
        _assert_no_echo(e, getattr(av, "MSG_ASSET_VERSION_FILE_NO_VERSION", "<constant missing>"))

    def test_alias_not_found(self, av):
        with patch.object(av, "get_all_asset_versions",
                          return_value=[{"assetVersionId": "1", "versionAlias": "RC1"}]):
            with pytest.raises(VAMSGeneralErrorResponse) as e:
                av.resolve_asset_version_id_from_alias(DB, ASSET, MARKER)
        _assert_no_echo(e, getattr(av, "MSG_ASSET_VERSION_ALIAS_NOT_FOUND", "<constant missing>"))

    def test_alias_ambiguous(self, av):
        versions = [{"assetVersionId": "1", "versionAlias": MARKER},
                    {"assetVersionId": "2", "versionAlias": MARKER}]
        with patch.object(av, "get_all_asset_versions", return_value=versions):
            with pytest.raises(VAMSGeneralErrorResponse) as e:
                av.resolve_asset_version_id_from_alias(DB, ASSET, MARKER)
        _assert_no_echo(e, getattr(av, "MSG_ASSET_VERSION_ALIAS_AMBIGUOUS", "<constant missing>"))

    def test_resolvers_still_resolve(self, av):
        """The fix changes only the refusal text: a valid pin still resolves."""
        files = {"files": [{"relativeKey": "/a.glb", "versionId": "s3v"}]}
        with patch.object(av, "validate_asset_version_exists", return_value=True), \
                patch.object(av, "get_asset_file_versions", return_value=files):
            assert av.resolve_file_version_from_asset_version(DB, ASSET, "1", "a.glb") == "s3v"
        with patch.object(av, "get_all_asset_versions",
                          return_value=[{"assetVersionId": "3", "versionAlias": "RC1"}]):
            assert av.resolve_asset_version_id_from_alias(DB, ASSET, "RC1") == "3"
