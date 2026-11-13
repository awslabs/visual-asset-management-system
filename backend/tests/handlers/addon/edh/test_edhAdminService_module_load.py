# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Module-load contract of edhAdminService: configuration failures abort the cold start.

A table name that cannot be resolved or a missing ``EDH_CLUSTER_ID`` or ``EDH_AMI_BUILDS_ENABLED`` must raise at
import. Swallowed
into a default, the handler would import cleanly and every request would then fail inside a
per-request ``except`` -- a 500 per call instead of a visible deployment fault.

Each case loads a FRESH copy by file path, named inside the real package so the relative sibling
imports resolve. The root conftest registers ``handlers.auth`` and ``handlers.authz`` in
``sys.modules`` as MagicMocks once at import, shared by every test in the process; the load installs
its own functional stand-ins for both, so what the copy binds does not depend on whatever an earlier
test left on those shared mocks. Every negative case is paired with the positive control at the end.
"""

import importlib.util
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# Module-level import populates the real package in sys.modules so the by-path copies can resolve
# their relative sibling imports.
from backend.backend.handlers.addon.edh import edhAdminService as _svc  # noqa: F401

_EDH_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "edh",
)
_EDH_PACKAGE = "backend.backend.handlers.addon.edh"

REQUIRED_ENV = ("EDH_CLUSTER_ID", "EDH_AMI_BUILDS_ENABLED")


def _auth_stubs():
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = lambda event: {"tokens": ["stub"], "roles": [], "mfaEnabled": False, "authMethod": "jwt"}
    authz = types.ModuleType("handlers.authz")
    authz.CasbinEnforcer = MagicMock()
    return {"handlers.auth": auth, "handlers.authz": authz}


def _load_fresh(unique_suffix):
    """Load an independent copy of edhAdminService by file path."""
    path = os.path.abspath(os.path.join(_EDH_DIR, "edhAdminService.py"))
    spec = importlib.util.spec_from_file_location(
        f"{_EDH_PACKAGE}.edhAdminService_under_test_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, _auth_stubs()), \
            patch("boto3.resource", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestModuleLoadFailsClosed:
    def test_import_raises_when_the_table_name_is_unresolvable(self):
        boom = RuntimeError("SSM unreachable")
        with patch("common.resourceNames.get_table_name", side_effect=boom):
            with pytest.raises(RuntimeError) as excinfo:
                _load_fresh("no_table")
        assert "SSM unreachable" in str(excinfo.value)

    @pytest.mark.parametrize("name", REQUIRED_ENV)
    def test_import_raises_when_an_environment_variable_is_missing(self, monkeypatch, name):
        monkeypatch.delenv(name)
        with pytest.raises(KeyError) as excinfo:
            _load_fresh(f"missing_{name}")
        assert name in str(excinfo.value)

    def test_import_succeeds_and_configuration_is_bound_when_names_resolve(self, monkeypatch):
        """Positive control for every case above: the same load with a good environment."""
        monkeypatch.setenv("EDH_CLUSTER_ID", "edh-test")
        monkeypatch.setenv("EDH_AMI_BUILDS_ENABLED", "true")
        module = _load_fresh("ok")
        assert module.edh_session_table is not None
        assert module.edh_session_table_name is not None
        assert module.edh_cluster_id == "edh-test"
        assert module.ami_builds_enabled is True
