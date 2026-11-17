# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import types

import pytest

# The root harness replaces `common` with a mock package, so the REAL `common.compliance` package
# is registered here as a package object whose `__path__` points at the source tree (the same way
# `tests/handlers/compliance/conftest.py` and `tests/conftest.py` register `common.compliance` and
# `common.workflows`). The four asset download handlers import
# `common.compliance.quarantineGuard` at module load, and the guard tests patch that one module
# instance, so the handlers and the tests must resolve the same object.
_BACKEND_SOURCE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

if "common.compliance" not in sys.modules:
    _compliance_pkg = types.ModuleType("common.compliance")
    _compliance_pkg.__path__ = [os.path.join(_BACKEND_SOURCE, "common", "compliance")]
    _compliance_pkg.__package__ = "common.compliance"
    sys.modules["common.compliance"] = _compliance_pkg
if "common" in sys.modules:
    setattr(sys.modules["common"], "compliance", sys.modules["common.compliance"])


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
