# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest

@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Set up environment variables for all tests"""
    os.environ["TAGS_STORAGE_TABLE_NAME"] = "test-tags-table"
    os.environ["TAG_TYPES_STORAGE_TABLE_NAME"] = "test-tag-types-table"
    os.environ["COGNITO_AUTH_ENABLED"] = "true"
    # Add any other required environment variables here
