# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Set up environment variables for all tests"""
    os.environ["ASSET_LINKS_STORAGE_TABLE_NAME"] = "test-asset-links-table"
    os.environ["ASSET_STORAGE_TABLE_NAME"] = "test-asset-table"
    os.environ["COGNITO_AUTH_ENABLED"] = "true"
    # Add any other required environment variables here


@pytest.fixture(autouse=True)
def stamp_asset_change():
    """The link create and delete stamp both linked assets through `stamp_asset_change`, an
    `update_item` on the module's real asset-table handle. Replaced for every test here so none
    reaches AWS; yields `{module name: mock}` for the tests that assert on the stamps."""
    from backend.backend.handlers.assetLinks import assetLinksService, createAssetLink
    stamps = {"createAssetLink": MagicMock(name="createAssetLink.stamp_asset_change"),
              "assetLinksService": MagicMock(name="assetLinksService.stamp_asset_change")}
    with patch.object(createAssetLink, "stamp_asset_change", stamps["createAssetLink"]), \
            patch.object(assetLinksService, "stamp_asset_change", stamps["assetLinksService"]):
        yield stamps
