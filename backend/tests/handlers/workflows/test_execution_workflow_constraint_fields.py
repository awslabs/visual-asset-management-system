# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow Casbin object the execution routes authorize against.

`category` and `name` are ABAC constraint fields (common/constants.PERMISSION_CONSTRAINT_FIELDS), so a
workflow rule scoped on either must decide an execution read exactly as it decides the workflow itself.
The object the execution paths build therefore carries the workflow definition's attributes, not just
its ids — otherwise a DENY scoped on category or name is silently inert on details/logs/abort/rerun
while an ALLOW scoped on them never grants.

Each check below is driven by an enforcer that reads only the field under test, so a missing field shows
up as the wrong decision rather than as an error.

executionService resolves its table names at import (mirrors test_executions_authz_bound.py)."""

import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.common.constants import PERMISSION_CONSTRAINT_FIELDS  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402
from backend.backend.handlers.workflows import workflowService as ws  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

# A run with no inputs and no asset output: workflow GET is its only control, so the decision under test
# is the only one the check makes.
MAIN_ROW = {"workflowExecutionId": "E1", "workflowId": "wf1", "workflowDatabaseId": "db1"}
WORKFLOW_DEFINITION = {"databaseId": "db1", "workflowId": "wf1", "workflowName": "Nightly Recon",
                       "category": "restricted", "description": "d"}


@pytest.fixture(autouse=True)
def _clear_caches():
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._workflow_definition_cache.clear()
    le._disarm_authz_entity_budget()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._workflow_definition_cache.clear()
    le._disarm_authz_entity_budget()


def _field_rule_enforcer(rule):
    """An enforcer answering `rule(obj)` for a workflow object and allowing everything else."""
    e = MagicMock()
    e.enforce.side_effect = lambda obj, action: (
        rule(obj) if obj.get("object__type") == "workflow" else True)
    return e


def _run_check(enforcer, definition=WORKFLOW_DEFINITION, action="GET"):
    le.claims_and_roles = {"tokens": ["u1"]}
    with patch(f"{MOD}.get_workflow_definition", return_value=definition), \
         patch(f"{MOD}.get_execution_input_assets", return_value=[]):
        return le._execution_access_check("E1", MAIN_ROW, action, config_row={},
                                          casbin_enforcer=enforcer)


def test_category_and_name_are_constraint_fields():
    # The fields the checks below rely on are the ones an admin can actually scope a rule on.
    assert "category" in PERMISSION_CONSTRAINT_FIELDS
    assert "name" in PERMISSION_CONSTRAINT_FIELDS


def test_category_scoped_deny_is_honored_on_an_execution_route():
    enforcer = _field_rule_enforcer(lambda obj: obj.get("category") != "restricted")
    allowed, reason = _run_check(enforcer)
    assert allowed is False
    assert reason == "workflow GET denied"


def test_name_scoped_deny_is_honored_on_an_execution_route():
    enforcer = _field_rule_enforcer(lambda obj: obj.get("name") != "Nightly Recon")
    allowed, reason = _run_check(enforcer)
    assert allowed is False
    assert reason == "workflow GET denied"


def test_category_scoped_allow_still_grants():
    # Positive control: the same fields that can deny must also be able to grant.
    enforcer = _field_rule_enforcer(lambda obj: obj.get("category") == "restricted")
    allowed, reason = _run_check(enforcer)
    assert allowed is True
    assert reason == ""


def test_name_scoped_allow_still_grants_on_abort():
    enforcer = _field_rule_enforcer(lambda obj: obj.get("name") == "Nightly Recon")
    allowed, reason = _run_check(enforcer, action="POST")
    assert allowed is True
    assert reason == ""


def test_object_matches_the_workflow_routes_shape():
    # The execution path and the workflow routes must present the same object for the same workflow, so
    # one rule cannot decide the two surfaces differently.
    enforcer = _field_rule_enforcer(lambda obj: True)
    _run_check(enforcer)
    execution_obj = next(call.args[0] for call in enforcer.enforce.call_args_list
                         if call.args[0].get("object__type") == "workflow")
    workflow_route_obj = ws._workflow_casbin_object(WORKFLOW_DEFINITION)
    assert execution_obj == workflow_route_obj


def test_ids_from_the_execution_row_stand_when_the_definition_is_gone():
    # A deleted workflow leaves its runs behind: the check still authorizes on the run's own ids.
    enforcer = _field_rule_enforcer(
        lambda obj: obj.get("databaseId") == "db1" and obj.get("workflowId") == "wf1")
    allowed, reason = _run_check(enforcer, definition={})
    assert allowed is True
    assert reason == ""


def test_empty_tokens_still_deny_without_reading_the_workflow():
    le.claims_and_roles = {"tokens": []}
    with patch(f"{MOD}.get_workflow_definition") as gwd:
        allowed, reason = le._execution_access_check("E1", MAIN_ROW, "GET", config_row={})
    assert allowed is False
    assert reason == "no tokens"
    gwd.assert_not_called()


def test_definition_read_is_memoized_across_rows_of_a_page():
    # A list page authorizes many rows against one workflow; the definition is read once for it.
    enforcer = _field_rule_enforcer(lambda obj: True)
    le.claims_and_roles = {"tokens": ["u1"]}
    with patch(f"{MOD}.get_workflow_definition", return_value=WORKFLOW_DEFINITION) as gwd, \
         patch(f"{MOD}.get_execution_input_assets", return_value=[]):
        for execution_id in ("E1", "E2", "E3"):
            le._execution_access_check(execution_id, MAIN_ROW, "GET", config_row={},
                                       casbin_enforcer=enforcer)
    assert gwd.call_count == 1
