# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Module-load contract of edhContainerService: configuration failures abort the cold start.

A missing ``EDH_CONTAINERS_EKS_ENABLED`` must raise at import. Swallowed into a default, the handler
would import cleanly and every request would then fail inside a per-request ``except`` -- a 500 per
call instead of a visible deployment fault.

Each case loads a FRESH copy by file path, named inside the real package so the relative sibling
imports resolve. The root conftest registers ``handlers.auth`` and ``handlers.authz`` in
``sys.modules`` as MagicMocks once at import, shared by every test in the process; the load installs
its own functional stand-ins for both. Every negative case is paired with the positive control.
"""

import importlib.util
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# Module-level import populates the real package in sys.modules so the by-path copies can resolve
# their relative sibling imports.
from backend.backend.handlers.addon.edh import edhContainerService as _svc  # noqa: F401

_EDH_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "edh",
)
_EDH_PACKAGE = "backend.backend.handlers.addon.edh"

REQUIRED_ENV = ("EDH_CONTAINERS_EKS_ENABLED",)


def _auth_stubs():
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = lambda event: {"tokens": ["stub"], "roles": [], "mfaEnabled": False, "authMethod": "jwt"}
    authz = types.ModuleType("handlers.authz")
    authz.CasbinEnforcer = MagicMock()
    return {"handlers.auth": auth, "handlers.authz": authz}


def _load_fresh(unique_suffix):
    """Load an independent copy of edhContainerService by file path."""
    path = os.path.abspath(os.path.join(_EDH_DIR, "edhContainerService.py"))
    spec = importlib.util.spec_from_file_location(
        f"{_EDH_PACKAGE}.edhContainerService_under_test_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, _auth_stubs()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestModuleLoadFailsClosed:
    @pytest.mark.parametrize("name", REQUIRED_ENV)
    def test_import_raises_when_an_environment_variable_is_missing(self, monkeypatch, name):
        monkeypatch.delenv(name)
        with pytest.raises(KeyError) as excinfo:
            _load_fresh(f"missing_{name}")
        assert name in str(excinfo.value)

    @pytest.mark.parametrize("value,expected", [("false", False), ("true", True)])
    def test_import_succeeds_and_binds_the_switch_when_the_environment_is_complete(
            self, monkeypatch, value, expected):
        """Positive control for the case above: the same load with a good environment."""
        monkeypatch.setenv("EDH_CONTAINERS_EKS_ENABLED", value)
        module = _load_fresh(f"ok_{value}")
        assert module.eks_enabled is expected
