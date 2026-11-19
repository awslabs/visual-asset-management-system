# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Harness for the audit log search handler tests.

The root harness replaces ``sys.modules['common']`` with a mock package and ``handlers.auth`` /
``handlers.authz`` with mock modules, so the real ``common.auditSearch`` library is registered here as
package objects whose ``__path__`` points at the source tree (the approach of
``tests/common/auditSearch/conftest.py``). The audit registries (``common.auditActions``,
``common.auditLogGroups``) are real in the root harness. The log redactor is bound to the real
``mask_sensitive_data`` so redaction is what production does.
"""

import importlib
import importlib.util
import os
import sys

import pytest

_BACKEND_SOURCE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

for _name in ("common.auditSearch", "common.auditSearch.parsers"):
    if _name not in sys.modules:
        _directory = os.path.join(_BACKEND_SOURCE, *_name.split("."))
        _spec = importlib.util.spec_from_file_location(
            _name, os.path.join(_directory, "__init__.py"), submodule_search_locations=[_directory])
        _package = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _package
        _spec.loader.exec_module(_package)
if "common" in sys.modules:
    setattr(sys.modules["common"], "auditSearch", sys.modules["common.auditSearch"])


@pytest.fixture(autouse=True)
def real_log_masking(monkeypatch):
    """The library's redaction uses the real key-name masking, not the harness's pass-through mock."""
    from backend.backend.customLogging.logger import mask_sensitive_data
    redaction = importlib.import_module("common.auditSearch.redaction")
    monkeypatch.setattr(redaction, "mask_sensitive_data", mask_sensitive_data)
