# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Harness for the compliance handler suite.

The root harness replaces `common` and `handlers` with mock packages, so the two REAL compliance
packages (`common.compliance`, `handlers.compliance`) are registered here as package objects whose
`__path__` points at the source tree -- the same way `tests/conftest.py` registers
`common.workflows`. Every test module imports the handlers under the `handlers.compliance.*` /
`common.compliance.*` names the production code itself uses, so a patch on
`handlers.compliance.complianceEvaluationStore` reaches the one module instance every handler holds
(importing the same file under `backend.backend.handlers.compliance.*` would create a second module
object that no handler reads).

Env vars the evaluation store and the workflow callback resolve at import and that the root harness
leaves unset are seeded here with `setdefault`, using the SAME values the workflow suites setdefault
and assert on: this directory is collected before `tests/handlers/workflows/`, so a different value
here would pre-empt theirs.

Shared event builders and stand-ins live in `_harness.py` next to this file.
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_BACKEND_SOURCE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

for _package_name, _source_parts in (
    ("common.compliance", ("common", "compliance")),
    ("handlers.compliance", ("handlers", "compliance")),
):
    if _package_name not in sys.modules:
        _package = types.ModuleType(_package_name)
        _package.__path__ = [os.path.join(_BACKEND_SOURCE, *_source_parts)]
        _package.__package__ = _package_name
        sys.modules[_package_name] = _package
    _parent_name, _child_name = _package_name.split(".")
    setattr(sys.modules[_parent_name], _child_name, sys.modules[_package_name])

for _key, _value in {
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
}.items():
    os.environ.setdefault(_key, _value)

# The evaluation store, the trigger and the cascade executor import the notifications module lazily
# (`from handlers.compliance.complianceNotifications import ...` inside the function that fires the
# notification). Load it once under that name so the autouse fixture below can stub its clients
# before the lazy import resolves it, rather than letting the first quarantine verdict build real
# boto3 clients mid-test.
import handlers.compliance.complianceNotifications as _notifications  # noqa: E402


@pytest.fixture(autouse=True)
def notifications_aws():
    """Stub the notifications module's DynamoDB and SNS clients for every test in this directory, so a
    quarantine verdict or a cascade completion publishes to a mock rather than reaching AWS. Yields
    the two mocks so a test can script the asset-topic lookup and assert on the publish."""
    dynamodb_client = MagicMock(name="notifications.dynamodb_client")
    dynamodb_client.query.return_value = {"Items": []}
    sns_client = MagicMock(name="notifications.sns_client")
    with patch.object(_notifications, "dynamodb_client", dynamodb_client), \
            patch.object(_notifications, "sns_client", sns_client):
        yield types.SimpleNamespace(dynamodb_client=dynamodb_client, sns_client=sns_client)
