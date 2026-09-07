#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Loading for the Cosmos Transfer container tests.

This directory did not exist. The pipeline had `lambda/tests/` only, so both the control-type
resolution and the output-video selection shipped with no regression guard -- the next edit could
restore either fallback with nothing turning red.

Loading is hermetic in both directions, for the reason the Cosmos 3 conftest already documents: the
container is a Batch entrypoint rather than a package, and its sibling module `inference` has the same
name every other NVIDIA container uses for its own copy. `sys.modules` holds one slot per name, so
without save/restore this suite would either inherit a sibling's cached `inference` or leave its own
behind for a sibling that loads later in the same pytest process. Twelve basenames are already
duplicated across the pipeline test trees for exactly this reason.
"""

import importlib.util
import os
import sys

import pytest

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Sibling modules `__main__.py` imports by bare name, which other NVIDIA containers also define.
_SIBLING_MODULES = ("inference", "model_manager", "manifest_io", "manifestHelper")


def _load(file_name, alias):
    saved = {name: sys.modules.get(name) for name in _SIBLING_MODULES}
    sys.path.insert(0, CONTAINER_DIR)
    try:
        for name in _SIBLING_MODULES:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(
            alias, os.path.join(CONTAINER_DIR, file_name))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        if CONTAINER_DIR in sys.path:
            sys.path.remove(CONTAINER_DIR)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


@pytest.fixture(scope="session")
def transfer_inference():
    """`inference.py`, which owns the control-type resolution."""
    return _load("inference.py", "cosmos_transfer_inference_under_test")


@pytest.fixture(scope="session")
def transfer_entrypoint():
    """`__main__.py`, which owns the output-video selection.

    Loaded from its path under an alias: it cannot be imported as `__main__` (that name belongs to the
    running interpreter), and loading it this way also leaves its `if __name__ == "__main__"` guard
    inert, so importing it does not start a pipeline.
    """
    return _load("__main__.py", "cosmos_transfer_entrypoint_under_test")
