#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the preview/3dThumbnail container tests.

The container's rendering dependencies (pyvista, vtk, trimesh, cadquery, ...) are usually not
installed in the test environment, so each one that cannot be imported is stubbed before
``preview_pipeline`` is imported; the orchestration tests in ``core`` run against the stubs. Inside
the pipeline image every dependency is real, and the tests that render or encode images run there
too -- they skip through ``requires_real`` wherever a module they need is a stub."""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)


def _importable(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # a stubbed parent package cannot be searched for submodules
        return False


for _name in (
    "pyvista", "vtk", "trimesh", "imageio", "PIL", "PIL.Image", "laspy", "pye57",
    "cadquery", "DracoPy", "open3d", "pxr", "scipy",
):
    if _name not in sys.modules and not _importable(_name):
        sys.modules[_name] = MagicMock()


def is_stubbed(name):
    """True when ``name`` is a test stub rather than the installed module."""
    return isinstance(sys.modules.get(name), MagicMock)


def requires_real(*names):
    """Skip marker for a test that needs the real modules ``names`` (rendering, encoding, USD)."""
    stubbed = [name for name in names if is_stubbed(name) or not _importable(name)]
    return pytest.mark.skipif(bool(stubbed), reason=f"needs the real {', '.join(stubbed)} "
                                                    f"(run inside the pipeline image)")

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
