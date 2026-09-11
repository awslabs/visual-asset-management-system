#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the preview/pcPotreeViewer container tests.

The container directory itself is the package (``python3 -m pc_pipeline`` in both images), and its
stage modules reach the shared helpers with three-dot relative imports
(``from ...utils.aws import s3``). Putting the container directory on ``sys.path`` and importing
``pipelines.potree.pipeline`` therefore fails with "attempted relative import beyond top-level
package" -- the stage modules must be imported as submodules of a package rooted AT the container
directory.

``load_stage_module`` registers that package under the name the image uses (``pc_pipeline``) with
its search location set to the container directory, so the relative imports resolve to the real
``utils`` package. The name is unique in this repository, which keeps the entry from resolving to
another pipeline's container package if both test suites run in one interpreter.

No dependency stubbing is needed: the only third-party import the stage modules reach is boto3,
which the repository already provides. Both converters are exercised through a fake
``subprocess.Popen``, so neither PDAL nor PotreeConverter has to be present.
"""

import importlib
import importlib.machinery
import importlib.util
import os
import sys

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_NAME = "pc_pipeline"

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder


def _ensure_package():
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    spec = importlib.machinery.ModuleSpec(_PKG_NAME, None, is_package=True)
    spec.submodule_search_locations = [_CONTAINER_DIR]
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PKG_NAME] = module
    return module


def load_stage_module(dotted_name):
    """Import a container module (e.g. ``pipelines.potree.pipeline``) with its relative imports
    intact."""
    _ensure_package()
    return importlib.import_module(f"{_PKG_NAME}.{dotted_name}")


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
