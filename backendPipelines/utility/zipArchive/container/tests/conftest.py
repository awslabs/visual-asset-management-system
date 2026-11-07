#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the utility/zipArchive pipeline container tests."""

import os
import sys

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

# The job definition's environment (set on the Batch job definition in the deployed pipeline).
os.environ.setdefault("ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE_NAME", "zipDownloadRequestsTable")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "assetFileVersionsTable")
os.environ.setdefault("AWS_REGION", "us-east-1")


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
    config.addinivalue_line("markers", "large: streams several GiB through the engine (minutes)")
