# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest

@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Set up environment variables for all tests"""
    os.environ["ASSET_LINKS_STORAGE_TABLE_NAME"] = "test-asset-links-table"
    os.environ["ASSET_STORAGE_TABLE_NAME"] = "test-asset-table"
    os.environ["COGNITO_AUTH_ENABLED"] = "true"
    # Add any other required environment variables here
