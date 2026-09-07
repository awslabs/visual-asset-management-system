# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tag handler module-load contract.

Both tag handlers must abort the cold start when a resource name cannot be
resolved (backend Rule 10 / the Gold Standard module-load contract). When the
name was swallowed into `None` the module imported cleanly, `tag_table` was
`None`, and the first table call raised `AttributeError` inside the
per-operation catch-all -- so a misconfigured stack presented as a working
endpoint that failed per request instead of failing unmistakably at cold start.

Each module is loaded as a FRESH copy by file path rather than reloaded: a
failed `importlib.reload` mutates the live module in place and would leave the
other tests in this directory running against a half-initialized module.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_HANDLER_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "backend", "handlers", "tags",
))

# module file stem -> (expected table attributes, the SSM key the second-name test fails on)
_MODULES = {
    "createTag": (["database_table", "tag_table", "tag_type_table"],
                  "dynamoTables/tagTypeStorage"),
    "tagService": (["tag_table", "tag_type_table"],
                   "dynamoTables/tagTypeStorage"),
}


def _stub_handler_packages():
    """The root conftest replaces `handlers.auth` / `handlers.authz` with
    attribute-less stand-ins before every test, and a fresh module copy imports
    names out of them while it executes. The stand-ins are rebuilt per test, so
    the attributes added here do not outlive it."""
    for module_name, attr in (("handlers.auth", "request_to_claims"),
                              ("handlers.authz", "CasbinEnforcer")):
        module = sys.modules[module_name]
        if not hasattr(module, attr):
            setattr(module, attr, MagicMock())


def _load_fresh(stem, unique_suffix):
    """Load an independent copy of a tags handler by file path."""
    _stub_handler_packages()
    spec = importlib.util.spec_from_file_location(
        f"{stem}_under_test_{unique_suffix}", os.path.join(_HANDLER_DIR, f"{stem}.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
@pytest.mark.parametrize("stem", sorted(_MODULES))
class TestModuleLoadFailsClosed:
    """An unresolvable resource name must fail the cold start, not the request."""

    def test_import_raises_when_resource_name_unresolvable(self, stem):
        boom = RuntimeError("SSM unreachable")
        with patch("common.resourceNames.get_table_name", side_effect=boom):
            with pytest.raises(RuntimeError) as excinfo:
                _load_fresh(stem, "fail")
        assert "SSM unreachable" in str(excinfo.value)

    def test_import_builds_every_table_when_names_resolve(self, stem):
        """Positive control for the test above: with names resolvable the module
        imports and every table is a real object, not None. Without this a
        constructor error unrelated to resolution would make the negative test
        pass for the wrong reason. The call-count assertion proves the patched
        resolver is the object the fresh copy binds, so the negative test's
        `side_effect` really reaches it."""
        expected_tables, _ = _MODULES[stem]
        resolver = MagicMock(side_effect=lambda key: key.param_key)
        with patch("common.resourceNames.get_table_name", resolver):
            module = _load_fresh(stem, "ok")

        assert resolver.call_count == len(expected_tables), \
            f"{stem} did not resolve every name through the patch"
        table_attrs = sorted(name for name in vars(module) if name.endswith("_table"))
        assert table_attrs == expected_tables
        for name in table_attrs:
            assert getattr(module, name) is not None, f"{stem}.{name} resolved to None"

    def test_only_a_later_name_unresolvable_still_aborts_the_cold_start(self, stem):
        """Every name shares one block, so a failure on any one of them aborts.
        Guards the shape as well as the outcome: a per-table fallback would swallow
        this one and leave the earlier tables live with a `None` beside them."""
        _, failing_key = _MODULES[stem]

        def resolve(key):
            if key.param_key == failing_key:
                raise KeyError(failing_key)
            return key.param_key

        with patch("common.resourceNames.get_table_name", side_effect=resolve):
            with pytest.raises(KeyError):
                _load_fresh(stem, "fail_later")
