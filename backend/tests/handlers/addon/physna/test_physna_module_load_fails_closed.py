# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Physna module-load contract for resource-name resolution (S2-BACKEND-067).

A resource-name resolution failure must abort the cold start. When the name was
swallowed into ``None`` the module imported cleanly and the table handle became
``None``; the first table call then raised inside a broad per-record
``except Exception``, the handler still answered a dict, and the SQS event-source
mapping deleted the message. A loud, attributable cold-start failure became an
indefinite silent drop of Physna sync work, and for the viewer a 400 on every
request instead of a visible deployment fault.

The two modules named here are the ones that carried the fail-open shape:
``physnaCommon`` (five names) and ``physnaViewer`` (one). The Garnet indexers are
covered by ``tests/handlers/addon/garnetFramework/test_garnet_module_load_and_sync_action.py``.

Each module is loaded as a FRESH copy by file path rather than reloaded: a failed
``importlib.reload`` mutates the live module in place and would leave every other
test in this directory running against a half-initialized module. ``importlib.reload``
also re-resolves through ``sys.path``, so a by-path load is what keeps the arm
pointed at the file under test.

Every negative case is paired with a positive control that loads the same file with
resolution working, so a constructor error unrelated to resolution cannot make the
negative case pass for the wrong reason.
"""

import importlib.util
import os

import pytest
from unittest.mock import MagicMock, patch

# Module-level import populates the real `backend.backend.handlers` package in
# sys.modules before the root conftest's autouse fixture runs, which otherwise
# stubs it with a non-package MockModule and breaks the relative import below.
from backend.backend.handlers.addon.physna import physnaViewer as _pv  # noqa: F401

_PHYSNA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "physna",
)

# The fresh copy is named INSIDE the real package: physnaViewer resolves
# physnaCommon through a relative import, which needs a parent package to resolve
# against. A bare file-path name leaves __package__ empty and the load fails on the
# import rather than on the resolution the test is about.
_PHYSNA_PACKAGE = "backend.backend.handlers.addon.physna"

# Module file -> a table handle the module must build for its own reads.
_MODULES = {
    "common": ("physnaCommon.py", "asset_storage_table"),
    "viewer": ("physnaViewer.py", "asset_storage_table"),
}


def _load_fresh(file_name, unique_suffix):
    """Load an independent copy of a Physna module by file path."""
    path = os.path.abspath(os.path.join(_PHYSNA_DIR, file_name))
    spec = importlib.util.spec_from_file_location(
        f"{_PHYSNA_PACKAGE}.{file_name[:-3]}_under_test_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestPhysnaModuleLoadFailsClosed:
    """An unresolvable resource name aborts the cold start instead of yielding None."""

    @pytest.mark.parametrize("module_key", sorted(_MODULES))
    def test_import_raises_when_resource_name_unresolvable(self, module_key):
        file_name, _ = _MODULES[module_key]
        boom = RuntimeError("SSM unreachable")
        with patch("common.resourceNames.get_table_name", side_effect=boom):
            with pytest.raises(RuntimeError) as excinfo:
                _load_fresh(file_name, f"fail_{module_key}")
        assert "SSM unreachable" in str(excinfo.value)

    @pytest.mark.parametrize("module_key", sorted(_MODULES))
    def test_import_succeeds_and_tables_are_built_when_names_resolve(self, module_key):
        """Positive control: with names resolvable the module imports and every
        table handle and resolved name is a real value, not None."""
        file_name, table_attr = _MODULES[module_key]
        module = _load_fresh(file_name, f"ok_{module_key}")

        assert getattr(module, table_attr) is not None

        table_attrs = [name for name in vars(module) if name.endswith("_table")]
        assert table_attrs, "expected the module to build at least one table"
        for name in table_attrs:
            assert getattr(module, name) is not None, f"{name} resolved to None"

        name_attrs = [name for name in vars(module) if name.endswith("_TABLE_NAME")]
        assert name_attrs, "expected the module to resolve at least one table name"
        for name in name_attrs:
            assert getattr(module, name) is not None, f"{name} was swallowed to None"


@pytest.mark.unit
class TestPhysnaViewerResolvesOnlyNamesItUses:
    """The viewer reads the asset table only, so it resolves no unused table name."""

    def test_no_database_storage_table_name_is_resolved(self):
        module = _load_fresh("physnaViewer.py", "unused_names")

        assert not hasattr(module, "_DATABASE_STORAGE_TABLE_NAME"), (
            "a name resolved and never read makes an unused parameter a cold-start "
            "requirement")
        assert not hasattr(module, "database_storage_table")
        # Positive control for the two assertions above: the name the viewer DOES
        # read is present, so absence is not simply a failed load.
        assert module._ASSET_STORAGE_TABLE_NAME is not None
