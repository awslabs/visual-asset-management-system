#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the conversion/mirisUpload pipeline container tests."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from miris_upload_container_harness import make_container  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")


@pytest.fixture
def container(monkeypatch, tmp_path):
    """The container entry point with its working directory under tmp_path and every remote call faked."""
    return make_container(monkeypatch, tmp_path)
