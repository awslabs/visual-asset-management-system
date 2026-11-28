#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the NVIDIA pipeline container tests.

Each NVIDIA GPU pipeline carries its own copy of ``model_manager.py``, and the copies have diverged --
different S3 prefixes, different invalidation handling, different logging. These tests load a copy by
path rather than by import name so several can be loaded in one session without shadowing each other,
and they stub ``boto3`` because nothing here reaches AWS.
"""

import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("boto3", MagicMock())

NVIDIA_DIR = pathlib.Path(__file__).resolve().parent.parent

# Every pipeline that caches HuggingFace weights on the shared filesystem, by the name its S3 prefix
# uses, so a failure names the pipeline rather than a path.
MODEL_MANAGERS = {
    "cosmos3": NVIDIA_DIR / "cosmos/3/container/model_manager.py",
    "cosmos-predict2.5": NVIDIA_DIR / "cosmos/predict/containerv2.5/model_manager.py",
    "cosmos-reason2": NVIDIA_DIR / "cosmos/reason/container/model_manager.py",
    "cosmos-transfer2.5": NVIDIA_DIR / "cosmos/transfer/container/model_manager.py",
    "gr00t": NVIDIA_DIR / "gr00t/container/model_manager.py",
}


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")


def load_model_manager(path: pathlib.Path):
    """One pipeline's model_manager module, loaded under a unique name."""
    unique = f"model_manager_{path.parent.parent.name}_{path.parent.name}".replace(".", "_")
    spec = importlib.util.spec_from_file_location(unique, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=sorted(MODEL_MANAGERS), ids=sorted(MODEL_MANAGERS))
def model_manager(request):
    """Each pipeline's model_manager in turn, so a behaviour is asserted for all of them."""
    return load_model_manager(MODEL_MANAGERS[request.param])


@pytest.fixture
def hf_cache(tmp_path):
    """Builds a HuggingFace-shaped cache: ``hub/models--org--name/snapshots/{revision}/``."""

    def build(repo: str, weight: str = "model.safetensors", revision: str = "abc123"):
        repo_dir = tmp_path / "hub" / ("models--" + repo.replace("/", "--"))
        snapshot = repo_dir / "snapshots" / revision
        snapshot.mkdir(parents=True, exist_ok=True)
        blobs = repo_dir / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        if weight:
            (blobs / "deadbeef").write_bytes(b"weights")
            (snapshot / weight).write_bytes(b"weights")
        return tmp_path

    return build
