# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import pytest

@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Set up environment variables for all tests"""
    os.environ["ASSET_STORAGE_TABLE_NAME"] = "test-asset-table"
    os.environ["DATABASE_STORAGE_TABLE_NAME"] = "test-database-table"
    os.environ["S3_ASSET_STORAGE_BUCKET"] = "test-asset-bucket"
    os.environ["S3_ASSET_AUXILIARY_BUCKET"] = "test-asset-auxiliary-bucket"
    os.environ["COGNITO_AUTH_ENABLED"] = "true"
    os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"] = "test-s3-buckets-table"
    os.environ["ASSET_UPLOAD_TABLE_NAME"] = "test-asset-upload-table"
    os.environ["SEND_EMAIL_FUNCTION_NAME"] = "test-send-email-function"
    os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"] = "3600"
