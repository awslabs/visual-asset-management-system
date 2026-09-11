#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the 3dRecon/splatToolbox container tests."""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")


@pytest.fixture(scope="session")
def container_dir():
    """The container directory — the build context, and what this conftest put on ``sys.path``."""
    return _CONTAINER_DIR


@pytest.fixture(scope="module")
def container_main():
    """The container entry module, loaded by file (its name is ``__main__.py``).

    Its ``vams_utils`` / ``boto3`` imports are stubbed, so a test needs none of the container's AWS
    dependencies installed. Only module-level definitions are exercised: importing does not run
    ``main()``.
    """
    stubbed = {}
    for name in ("boto3", "vams_utils", "vams_utils.manifest_io"):
        if name not in sys.modules:
            stubbed[name] = MagicMock()
    sys.modules.update(stubbed)
    try:
        spec = importlib.util.spec_from_file_location(
            "splat_container_main", os.path.join(_CONTAINER_DIR, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return module
