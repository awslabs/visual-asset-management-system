#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the preview/3dThumbnail container tests.

The container's rendering dependencies (pyvista, vtk, trimesh, cadquery, ...) are not installed in
the test environment, so they are stubbed before ``preview_pipeline`` is imported. The tests here
cover the pure orchestration logic in ``core``."""

import os
import sys
from unittest.mock import MagicMock

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

for _name in (
    "pyvista", "vtk", "trimesh", "imageio", "PIL", "PIL.Image", "laspy", "pye57",
    "cadquery", "DracoPy", "open3d", "pxr", "scipy",
):
    sys.modules.setdefault(_name, MagicMock())

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
