# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Physna conftest's ``handlers.auth`` stub carries every name the backend imports from it (#1451).

The stub is installed into ``sys.modules`` at collection time and reinstalled before every test in this
directory, so for the rest of the session any module that imports ``handlers.auth`` gets it. It held only
``request_to_claims``; ``customLogging.auditLogging`` also imports ``AUTH_METHOD_API_KEY``, so a run that
collected the Physna tests before the first real ``auditLogging`` import (``tests/handlers`` alone, or the
Physna tests together with a handler test) failed at collection with ``ImportError``.

The names are read from the backend source rather than listed here, so a new import cannot leave the stub
behind again.
"""

import ast
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[4] / "backend"
_AUTH_PACKAGE = _BACKEND / "handlers" / "auth"


def _names_imported_from_handlers_auth():
    names = set()
    for path in _BACKEND.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "handlers.auth":
                names.update(alias.name for alias in node.names)
    # A submodule of the package (`from handlers.auth import apiKeyService`) is not a stubbed attribute.
    return sorted(name for name in names if not (_AUTH_PACKAGE / f"{name}.py").exists())


NAMES = _names_imported_from_handlers_auth()


@pytest.mark.unit
def test_the_backend_imports_names_from_handlers_auth():
    assert "request_to_claims" in NAMES
    assert "AUTH_METHOD_API_KEY" in NAMES


@pytest.mark.unit
@pytest.mark.parametrize("name", NAMES)
def test_the_stub_carries_every_imported_name(name):
    stub = sys.modules["handlers.auth"]
    assert hasattr(stub, name), f"the Physna handlers.auth stub lacks {name}"


@pytest.mark.unit
def test_the_auth_method_values_match_the_real_module():
    real = (_AUTH_PACKAGE / "__init__.py").read_text(encoding="utf-8")
    stub = sys.modules["handlers.auth"]
    assert f'AUTH_METHOD_JWT = "{stub.AUTH_METHOD_JWT}"' in real
    assert f'AUTH_METHOD_API_KEY = "{stub.AUTH_METHOD_API_KEY}"' in real
