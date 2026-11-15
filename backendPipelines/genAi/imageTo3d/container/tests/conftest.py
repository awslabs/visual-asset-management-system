#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the genAi/imageTo3d container tests.

The container's AWS and 3D dependencies (boto3, trimesh, rembg, PIL, torch) and the per-model
``generate`` module are stubbed with MagicMock when they are not installed, so these tests need
none of the container's runtime dependencies. They cover pure path arithmetic, the ``config.json``
allow-list, and the print gate over hand-built meshes.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder

# Container runtime dependencies plus the per-model generate module. Stubbed only when absent, so a
# developer machine that has them installed exercises the real thing.
_OPTIONAL_MODULES = (
    "boto3", "botocore", "trimesh", "rembg", "onnxruntime", "PIL", "torch", "generate",
)


def _stub_missing(name):
    """Install a MagicMock for ``name`` when it cannot be imported."""
    if name in sys.modules:
        return
    try:
        if importlib.util.find_spec(name) is not None:
            return
    except (ImportError, ValueError):
        pass
    module = MagicMock()
    sys.modules[name] = module
    if name == "botocore":
        sys.modules["botocore.exceptions"] = module.exceptions
        module.exceptions.ClientError = type("ClientError", (Exception,), {})
    if name == "boto3":
        sys.modules["boto3.s3"] = module.s3
        sys.modules["boto3.s3.transfer"] = module.s3.transfer
    if name == "trimesh":
        for submodule in ("repair", "boolean"):
            sys.modules[f"trimesh.{submodule}"] = getattr(module, submodule)
    if name == "PIL":
        sys.modules["PIL.Image"] = module.Image


for _name in _OPTIONAL_MODULES:
    _stub_missing(_name)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
    config.addinivalue_line("markers", "integration: drives a locally installed tool (Blender); skipped where absent")
