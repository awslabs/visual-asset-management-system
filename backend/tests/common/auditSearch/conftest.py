# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Directory-scoped setup for the audit search library tests.

The root harness replaces ``sys.modules['common']`` with a mock package, so the real
``common.auditSearch`` package is registered here as a package object whose ``__path__`` points at the
source tree (the same approach as ``tests/common/compliance/conftest.py``). The log redactor the
library imports is the mock ``customLogging.logger`` in this harness, so every test runs with the real
``mask_sensitive_data`` bound into ``common.auditSearch.redaction``. The shared record helpers of
``tests/common/test_audit_action_records.py`` are importable from here.
"""

import importlib
import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest

_TESTS_COMMON = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND_SOURCE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
if _TESTS_COMMON not in sys.path:
    sys.path.append(_TESTS_COMMON)

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


@pytest.fixture
def writer(monkeypatch):
    """The writer with recording clients: the module client (outside a request context) and the flush
    client (inside one)."""
    from test_audit_action_records import RecordingClient, _Claims, auditLogging, mask_sensitive_data
    monkeypatch.setattr(auditLogging, "request_to_claims", _Claims())
    monkeypatch.setattr(auditLogging, "mask_sensitive_data", mask_sensitive_data)
    monkeypatch.setattr(auditLogging, "_created_log_streams", {})
    module_client, flush_client = RecordingClient(), RecordingClient()
    monkeypatch.setattr(auditLogging, "cloudwatch_logs", module_client)
    monkeypatch.setattr(auditLogging, "audit_flush_client", flush_client)
    return SimpleNamespace(module=module_client, flush=flush_client)
