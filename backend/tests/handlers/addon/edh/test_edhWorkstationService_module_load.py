# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Module-load contract of edhWorkstationService: configuration failures abort the cold start.

A table name that cannot be resolved, a missing ``EDH_*`` variable or an allow-list that is not a
JSON array of strings must raise at import. Swallowed into a default, the handler would import
cleanly and every request would then fail inside a per-request ``except`` -- a 500 per call instead
of a visible deployment fault (the shape ``test_physna_module_load_fails_closed.py`` guards).

Each case loads a FRESH copy by file path, named inside the real package so the relative sibling
imports resolve. The root conftest's autouse fixture (``setup_mock_imports`` in ``backend/conftest.py``)
re-registers ``handlers.auth`` / ``handlers.authz`` in ``sys.modules`` as bare ``MockModule`` objects
before every test, so a load at test time would find no ``request_to_claims`` / ``CasbinEnforcer`` to
import; the load installs its own functional stand-ins for both under ``patch.dict``, so what the copy
binds depends on neither that fixture nor on an earlier test. Both boto3 seams the module block uses
(the DynamoDB resource and the Lambda client) are patched. Every negative case is paired with the
positive control at the end.
"""

import importlib.util
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# Module-level import populates the real package in sys.modules so the by-path copies can resolve
# their relative sibling imports.
from backend.backend.handlers.addon.edh import edhWorkstationService as _svc  # noqa: F401

_EDH_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "edh",
)
_EDH_PACKAGE = "backend.backend.handlers.addon.edh"

REQUIRED_ENV = (
    "EDH_ALLOWED_INSTANCE_TYPES",
    "EDH_VDI_PROFILE_ID",
    "EDH_LAUNCH_WORKER_FUNCTION_NAME",
    "EDH_AMI_BUILDS_ENABLED",
)


def _auth_stubs():
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = lambda event: {"tokens": ["stub"], "roles": [], "mfaEnabled": False, "authMethod": "jwt"}
    authz = types.ModuleType("handlers.authz")
    authz.CasbinEnforcer = MagicMock()
    return {"handlers.auth": auth, "handlers.authz": authz}


def _load_fresh(unique_suffix):
    """Load an independent copy of edhWorkstationService by file path."""
    path = os.path.abspath(os.path.join(_EDH_DIR, "edhWorkstationService.py"))
    spec = importlib.util.spec_from_file_location(
        f"{_EDH_PACKAGE}.edhWorkstationService_under_test_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, _auth_stubs()), \
            patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
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
        monkeypatch.delenv(name, raising=False)
        with pytest.raises(KeyError) as excinfo:
            _load_fresh(f"missing_{name}")
        assert name in str(excinfo.value)

    @pytest.mark.parametrize("raw", ["not json", '{"a": 1}', "[1, 2]", '["g5.xlarge", ""]'])
    def test_import_raises_when_the_allow_list_is_not_a_json_array_of_strings(self, monkeypatch, raw):
        monkeypatch.setenv("EDH_ALLOWED_INSTANCE_TYPES", raw)
        with pytest.raises(ValueError):
            _load_fresh("bad_allow_list")

    def test_import_raises_when_the_profile_id_is_not_an_integer(self, monkeypatch):
        monkeypatch.setenv("EDH_VDI_PROFILE_ID", "one")
        with pytest.raises(ValueError):
            _load_fresh("bad_profile_id")

    def test_import_succeeds_and_configuration_is_bound_when_names_resolve(self, monkeypatch):
        """Positive control for every case above: the same load with a good environment."""
        monkeypatch.setenv("EDH_ALLOWED_INSTANCE_TYPES", '["g5.xlarge", "g5.2xlarge"]')
        monkeypatch.setenv("EDH_VDI_PROFILE_ID", "1")
        monkeypatch.setenv("EDH_LAUNCH_WORKER_FUNCTION_NAME", "vams-edh-launch-worker")
        monkeypatch.setenv("EDH_AMI_BUILDS_ENABLED", "true")
        module = _load_fresh("ok")

        assert module.edh_session_table is not None
        assert module.edh_session_table_name is not None
        # The break-glass override the test conftests set is what the name resolves to here.
        assert module.edh_session_table_name == os.environ["EDH_SESSION_STORAGE_TABLE_NAME"]
        assert module.lambda_client is not None
        assert module.allowed_instance_types == ("g5.xlarge", "g5.2xlarge")
        assert module.vdi_profile_id == 1
        assert module.launch_worker_function_name == "vams-edh-launch-worker"
        assert module.ami_builds_enabled is True
        # The empty array is the documented "unfiltered" value and must parse to an empty tuple.
        monkeypatch.setenv("EDH_ALLOWED_INSTANCE_TYPES", "[]")
        assert _load_fresh("ok_empty").allowed_instance_types == ()
        # The CDK builder writes "true" or "false"; anything but "true" leaves AMI builds off.
        for index, raw in enumerate(("false", "", "TRUE", "True", " true")):
            monkeypatch.setenv("EDH_AMI_BUILDS_ENABLED", raw)
            assert _load_fresh(f"ok_builds_off_{index}").ami_builds_enabled is False, raw
