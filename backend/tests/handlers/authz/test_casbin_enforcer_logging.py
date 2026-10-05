# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Casbin's own request and role logging stays off in the VAMS enforcer.

pycasbin's ``enable_log=True`` runs ``logging.config.dictConfig`` with a stderr
``StreamHandler`` on the ``casbin`` loggers, after which every ``enforce()`` writes
``Request: <sub>, <obj>, <act> ---> <result>`` (INFO when allowed, WARNING when denied)
and every later enforcer build writes a ``user::<id> < 'role::<name>'`` line listing the
caller's role links. In Lambda, stderr is the function's CloudWatch log group, so each
authorization check would put the caller id and the full checked object (asset names,
tags, database ids) in the handler log. VAMS records API-level decisions and data-level
denials through ``log_authorization_api`` / ``log_authorization`` instead.

These tests build the enforcer through the REAL ``_create_casbin_enforcer`` path and run
the REAL ``CasbinEnforcerService.enforce()`` wrapper, with only the DynamoDB reads stubbed.
"""

import logging
from datetime import datetime

import pytest
from unittest.mock import patch

from backend.backend.handlers.authz import CasbinEnforcerService
from backend.backend.common.constants import PERMISSION_CONSTRAINT_POLICY

USER_ID = "tester"
PROBE_ASSET_NAME = "casbin-log-probe-asset"
CASBIN_LOGGER_NAMES = ("casbin", "casbin.policy", "casbin.enforcer", "casbin.role")


@pytest.fixture
def casbin_loggers_enabled():
    """Start each test with the Casbin loggers enabled, so the assertion does not rely on
    an earlier test in the session having disabled them, and restore their state after."""
    saved = []
    for name in CASBIN_LOGGER_NAMES:
        casbin_logger = logging.getLogger(name)
        saved.append(
            (casbin_logger, casbin_logger.disabled, casbin_logger.level,
             casbin_logger.propagate, list(casbin_logger.handlers))
        )
        casbin_logger.disabled = False
    yield
    for casbin_logger, disabled, level, propagate, handlers in saved:
        for handler in list(casbin_logger.handlers):
            if handler not in handlers:
                casbin_logger.removeHandler(handler)
        for handler in handlers:
            if handler not in casbin_logger.handlers:
                casbin_logger.addHandler(handler)
        casbin_logger.disabled = disabled
        casbin_logger.setLevel(level)
        casbin_logger.propagate = propagate


def _policy_text():
    """Policy text from the REAL generator: roleA may GET assets in database db1."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    policies = [{
        "constraintId": "C1",
        "objectType": "asset",
        "criteriaAnd": [{"field": "databaseId", "operator": "equals", "value": "db1"}],
        "criteriaOr": [],
        "groupPermissions": [{"groupId": "roleA", "permission": "GET", "permissionType": "allow"}],
        "userPermissions": [],
    }]
    user_roles = [{"userId": USER_ID, "roleName": "roleA"}]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies):
        return svc._create_policy_text_helper()


def _production_service(policy_text):
    """A CasbinEnforcerService whose Casbin enforcer is built by the PRODUCTION
    ``_create_casbin_enforcer`` path (the one ``__init__`` calls)."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    svc._model_text = PERMISSION_CONSTRAINT_POLICY
    svc._dateTime_Cached = datetime.now()
    svc._enforcer = None
    svc._create_casbin_enforcer(policy_text)
    return svc


def _casbin_warning_records(caplog):
    """WARNING-or-higher records from the Casbin loggers that reached the root logger (in
    Lambda the root handler also writes to the function log group)."""
    return [
        r for r in caplog.records
        if (r.name == "casbin" or r.name.startswith("casbin.")) and r.levelno >= logging.WARNING
    ]


@pytest.mark.unit
class TestCasbinLibraryLoggingDisabled:
    def test_enforce_writes_no_casbin_request_line(self, casbin_loggers_enabled, capsys, caplog):
        svc = _production_service(_policy_text())

        allowed = svc.enforce(
            {"object__type": "asset", "databaseId": "db1", "assetName": PROBE_ASSET_NAME, "tags": ["t1"]},
            "GET",
        )
        denied = svc.enforce(
            {"object__type": "asset", "databaseId": "db2", "assetName": PROBE_ASSET_NAME, "tags": ["t1"]},
            "GET",
        )

        # The allow proves the real enforcer was built (the deny-all fallback would also be
        # silent), and both decisions are unchanged by the logging setting
        assert allowed is True
        assert denied is False

        stderr = capsys.readouterr().err
        assert "--->" not in stderr, f"Casbin wrote per-request lines to stderr:\n{stderr}"
        assert PROBE_ASSET_NAME not in stderr, f"Constraint object reached stderr:\n{stderr}"
        assert _casbin_warning_records(caplog) == [], (
            f"Casbin emitted log records: {[r.getMessage() for r in _casbin_warning_records(caplog)]}"
        )

    def test_rebuilt_enforcer_writes_no_role_link_line(self, casbin_loggers_enabled, capsys, caplog):
        policy_text = _policy_text()

        # A cache refresh builds a second enforcer in the same process
        _production_service(policy_text)
        svc = _production_service(policy_text)
        assert svc.enforce({"object__type": "asset", "databaseId": "db1"}, "GET") is True

        stderr = capsys.readouterr().err
        assert "role::roleA" not in stderr, f"Casbin wrote role links to stderr:\n{stderr}"
        assert f"user::{USER_ID}" not in stderr, f"Casbin wrote the caller id to stderr:\n{stderr}"
        assert _casbin_warning_records(caplog) == [], (
            f"Casbin emitted log records: {[r.getMessage() for r in _casbin_warning_records(caplog)]}"
        )
