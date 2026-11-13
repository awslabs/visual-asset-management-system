# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import pytest

_DYNAMODB_COMMON = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "dynamodb.py")

@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Set up environment variables for all tests"""
    os.environ["DATABASE_STORAGE_TABLE_NAME"] = "test-database-table"
    os.environ["WORKFLOW_STORAGE_TABLE_NAME"] = "test-workflow-table"
    os.environ["PIPELINE_STORAGE_TABLE_NAME"] = "test-pipeline-table"
    os.environ["ASSET_STORAGE_TABLE_NAME"] = "test-asset-table"
    os.environ["COGNITO_AUTH_ENABLED"] = "true"
    # Add any other required environment variables here


@pytest.fixture(scope="session")
def real_to_update_expr():
    """The shipped `common.dynamodb.to_update_expr`, loaded by path.

    The root conftest registers `common.dynamodb` as a MagicMock, so a handler that imports
    `to_update_expr` at module load holds a stand-in whose result unpacks to nothing. Binding the
    shipped helper onto the handler is what makes the update expression it builds observable.
    """
    spec = importlib.util.spec_from_file_location(
        "_real_common_dynamodb_for_database_tests", _DYNAMODB_COMMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.to_update_expr


@pytest.fixture(autouse=True)
def no_compliance_retirement(request, monkeypatch):
    """A database delete starts the move of its compliance data (`databaseRetirement.begin`) and
    queues the rest (`hand_off_compliance_move`). These tests model no compliance tables, so both
    are stubbed; a module that drives the move against its own tables sets
    `USES_RETIREMENT_RECORD = True`."""
    if getattr(request.module, "USES_RETIREMENT_RECORD", False):
        yield
        return
    import sys
    from common.compliance import databaseRetirement
    monkeypatch.setattr(databaseRetirement, "begin", lambda *args, **kwargs: {})
    module = sys.modules.get("backend.backend.handlers.databases.databaseService")
    if module is not None:
        monkeypatch.setattr(module, "hand_off_compliance_move", lambda *args, **kwargs: True)
    yield