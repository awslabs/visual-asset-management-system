# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EDH client module-load contract: a missing environment value aborts the cold start.

A handler whose configuration was swallowed into an empty string imports cleanly and answers a 502
on every request instead of surfacing a deployment fault. The module is loaded as a FRESH copy by
file path rather than reloaded: a failed ``importlib.reload`` mutates the live module in place and
would leave every other test in this directory running against a half-initialized module, and
``reload`` re-resolves through ``sys.path`` rather than the file under test.

Each negative case is paired with a positive control that loads the same file with the environment
present, so a constructor error unrelated to the environment cannot make the negative case pass for
the wrong reason.
"""

import importlib.util
import os

import pytest
from unittest.mock import MagicMock, patch

# Imported at module level so a client that fails to import is a collection error for the whole
# file rather than a failure inside each fresh load below.
from backend.backend.handlers.addon.edh.common import edhClient as _ec  # noqa: F401

_EDH_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "edh",
)
_EDH_PACKAGE = "backend.backend.handlers.addon.edh"

# The variables the module reads with os.environ[...]; the CA bundle ARN is the one that may be absent.
REQUIRED_ENV = ("EDH_API_ENDPOINT", "EDH_ADMIN_KEY_SECRET_ARN", "EDH_AUTO_PROVISION_USERS")


def _load_fresh(unique_suffix):
    """Load an independent copy of edhClient.py by file path."""
    path = os.path.abspath(os.path.join(_EDH_DIR, "common", "edhClient.py"))
    spec = importlib.util.spec_from_file_location(
        f"{_EDH_PACKAGE}.common.edhClient_under_test_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestEdhClientModuleLoadFailsClosed:
    @pytest.mark.parametrize("variable", REQUIRED_ENV)
    def test_import_raises_when_a_required_variable_is_missing(self, monkeypatch, variable):
        monkeypatch.delenv(variable)
        with pytest.raises(KeyError) as excinfo:
            _load_fresh(f"missing_{variable}")
        assert variable in str(excinfo.value)

    def test_import_succeeds_with_the_environment_present(self):
        """Positive control: with every value present the module imports and echoes the environment."""
        module = _load_fresh("ok")
        assert module.EDH_API_ENDPOINT == os.environ["EDH_API_ENDPOINT"]
        assert module.EDH_ADMIN_KEY_SECRET_ARN == os.environ["EDH_ADMIN_KEY_SECRET_ARN"]
        assert module.EDH_AUTO_PROVISION_USERS == os.environ["EDH_AUTO_PROVISION_USERS"]

    def test_an_absent_ca_bundle_variable_is_an_empty_string_not_a_failure(self, monkeypatch):
        monkeypatch.delenv("EDH_CA_BUNDLE_SECRET_ARN", raising=False)
        module = _load_fresh("no_ca_bundle")
        assert module.EDH_CA_BUNDLE_SECRET_ARN == ""

    def test_an_absent_tls_server_name_variable_is_an_empty_string_not_a_failure(self, monkeypatch):
        monkeypatch.delenv("EDH_API_TLS_SERVER_NAME", raising=False)
        module = _load_fresh("no_tls_server_name")
        assert module.EDH_API_TLS_SERVER_NAME == ""

    def test_the_client_resolves_no_table_name(self):
        # The client reads no table; a resolved-and-unused name would make an unpublished SSM
        # parameter a cold-start requirement of every EDH Lambda.
        module = _load_fresh("no_tables")
        assert not [name for name in vars(module) if name.endswith("_TABLE_NAME")]
        assert not [name for name in vars(module) if name.endswith("_table")]
        # Positive control for the two absences: the module did load its own names.
        assert module.EDH_REQUEST_BUDGET_SECONDS == 20

    def test_an_endpoint_carrying_a_path_aborts_the_import(self, monkeypatch):
        # EDH routes are absolute /api/... paths; an endpoint that carried one would be joined twice.
        monkeypatch.setenv("EDH_API_ENDPOINT", "https://internal-edh-alb.example.internal/api/v1")
        with pytest.raises(ValueError) as excinfo:
            _load_fresh("endpoint_with_path")
        assert "without a path" in str(excinfo.value)
