#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Loading for the Cosmos Predict v1 container tests.

The container is a Batch entrypoint, not a package: ``__main__.py`` plus the sibling modules
``inference`` and ``model_manager``, whose names every other NVIDIA container also uses for its own
copies. Loading is hermetic in both directions, as in the other NVIDIA container suites. ``model_manager``
imports ``huggingface_hub``, which the test environment does not install, so a stub stands in for it
while the entrypoint loads; nothing these tests run reaches the model cache.
"""

import importlib.util
import os
import sys
import types

import pytest

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIBLING_MODULES = ("inference", "model_manager")


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")


def _stub_model_manager():
    module = types.ModuleType("model_manager")
    module.ensure_model_available = lambda *args, **kwargs: None
    module.get_required_models = lambda *args, **kwargs: []
    return module


@pytest.fixture(scope="session")
def container():
    """``__main__.py``, loaded from its path under an alias.

    It cannot be imported as ``__main__`` (that name belongs to the running interpreter), and loading it
    this way also leaves its ``if __name__ == "__main__"`` guard inert, so importing it does not start a
    pipeline.
    """
    saved = {name: sys.modules.get(name) for name in _SIBLING_MODULES}
    sys.path.insert(0, CONTAINER_DIR)
    try:
        sys.modules.pop("inference", None)
        sys.modules["model_manager"] = _stub_model_manager()
        spec = importlib.util.spec_from_file_location(
            "cosmos_predict_v1_entrypoint_under_test", os.path.join(CONTAINER_DIR, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(CONTAINER_DIR)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module
