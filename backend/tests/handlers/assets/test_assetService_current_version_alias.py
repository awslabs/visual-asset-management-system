# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset's currentVersion reports the version's alias.

Version rows store versionAlias, with '' for a version created without one. The single asset GET
(get_current_version_info) and the listings (enhance_assets_with_version_info) both build
currentVersion from the row, so both report a stored alias, and an unaliased version as null.
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.tests.handlers.assets.test_assetService_history import _load


def _row(alias):
    row = {"databaseId:assetId": "db1:asset-1", "assetVersionId": "2",
           "dateCreated": "2026-01-01T00:00:00Z", "comment": "c", "createdBy": "u1"}
    if alias is not None:
        row["versionAlias"] = alias
    return row


def _asset():
    return {"databaseId": "db1", "assetId": "asset-1", "currentVersionId": "2"}


@pytest.mark.unit
class TestCurrentVersionAlias:
    @pytest.mark.parametrize("alias, expected", [("v2", "v2"), ("", None), (None, None)],
                             ids=["aliased", "empty", "absent"])
    def test_the_single_get_reports_the_alias(self, alias, expected):
        m = _load()
        table = MagicMock()
        table.get_item.return_value = {"Item": _row(alias)}
        with patch.object(m, "versions_table", table):
            version = m.get_current_version_info(_asset())

        assert version.Version == "2"
        assert version.versionAlias == expected

    @pytest.mark.parametrize("alias, expected", [("v2", "v2"), ("", None)], ids=["aliased", "empty"])
    def test_the_listings_report_the_alias(self, alias, expected):
        m = _load()
        resource = MagicMock()
        resource.batch_get_item.return_value = {
            "Responses": {m.asset_versions_table_name: [_row(alias)]}}
        per_item = MagicMock()
        with patch.object(m, "dynamodb", resource), patch.object(m, "versions_table", per_item):
            [enhanced] = m.enhance_assets_with_version_info([_asset()])

        assert resource.batch_get_item.call_args_list  # the row came from the batched read
        per_item.get_item.assert_not_called()
        assert enhanced["currentVersion"].versionAlias == expected
