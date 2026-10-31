#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The execution failure reasons a compliance read returns to its caller.

A failed pipeline rule's result names its workflow execution (`executionId`) and carries the
failure the execution recorded (`executionFailureReason`); the asset-state row keeps the same
reasons beside the quarantine reason while the asset is quarantined
(`quarantineExecutionFailures`). The reason is what the execution itself discloses — a file name,
a byte offset, a parser's message — so a compliance read returns it only to a caller allowed to
read that execution, decided exactly as GET /workflows/executions/{executionId}/details decides
it: the Tier-1 check on that route's path and the shared execution read rule
(`common.workflows.executionAccess.execution_access_decision`, asset action GET). For any other
caller the result keeps its generic message (`Pipeline execution FAILED`) and its `executionId`,
and only the reason is left out. An execution whose main row is gone is readable by nobody, so its
reason is left out too.

Every compliance read that returns a rule result or a quarantine reason passes it through one of
`rule_results_for_caller`, `evaluation_for_caller` or `state_record_for_caller`, with one
`ExecutionReadScope` per request. The scope memoizes each execution's decision and the rows the
decisions read, and decides at most MAX_EXECUTIONS_DECIDED_PER_REQUEST distinct executions; past
that bound a reason is left out (the caller reads it on the asset's own compliance record), so a
listing page costs a bounded number of reads.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config

from common.compliance.evaluationEngine import QUARANTINE_EXECUTION_FAILURES_FIELD
from common.resourceNames import ResourceKeys, get_table_name
from common.workflows import executionAccess
from customLogging.logger import safeLogger

logger = safeLogger(service_name="ComplianceExecutionFailureAccess")

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)

try:
    workflow_executions_table_name = get_table_name(
        ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    execution_configuration_table_name = get_table_name(
        ResourceKeys.WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE)
    execution_inputs_table_name = get_table_name(
        ResourceKeys.WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE)
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    execution_asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

workflow_executions_table = dynamodb.Table(workflow_executions_table_name)
execution_configuration_table = dynamodb.Table(execution_configuration_table_name)
execution_inputs_table = dynamodb.Table(execution_inputs_table_name)
workflow_table = dynamodb.Table(workflow_table_name)
execution_asset_table = dynamodb.Table(execution_asset_table_name)

# The rule-result field this module governs (and each `quarantineExecutionFailures` entry's).
FAILURE_REASON_FIELD = "executionFailureReason"

# Bound on the distinct executions one request decides (a listing page of quarantined assets each
# with its own failed execution); each decision reads the execution's main and configuration rows,
# its input rows, its workflow and its assets.
MAX_EXECUTIONS_DECIDED_PER_REQUEST = 50


class ExecutionReadScope:
    """One request's execution-read decisions for one caller: the caller's CasbinEnforcer (None for
    a request without tokens, which may read no execution), each execution's decision, and the
    rows the decisions read, so an execution, workflow or asset shared by several results is read
    and decided once."""

    def __init__(self, casbin_enforcer):
        self.casbin_enforcer = casbin_enforcer
        self.decisions: Dict[str, bool] = {}
        self.workflows: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.assets: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self.enforced: Dict[Tuple[str, ...], bool] = {}


def new_scope(casbin_enforcer) -> ExecutionReadScope:
    """A request's scope for the caller whose enforcer is given (None: no tokens)."""
    return ExecutionReadScope(casbin_enforcer)


def _enforce(scope: ExecutionReadScope, obj: Dict[str, Any], action: str) -> bool:
    """The caller's Casbin decision, memoized on what identifies one object within the request: the
    route path of an api object, and the ids of an entity (an asset resolves to one row per
    request, a workflow or database object is built from its ids and its one definition row)."""
    key = (obj.get("object__type", ""), obj.get("route__path", ""), obj.get("databaseId", ""),
           obj.get("assetId", ""), obj.get("workflowId", ""), action)
    if key not in scope.enforced:
        scope.enforced[key] = bool(scope.casbin_enforcer.enforce(obj, action))
    return scope.enforced[key]


def _workflow_object(scope: ExecutionReadScope, workflow_database_id: str,
                     workflow_id: str) -> Dict[str, Any]:
    """The workflow Casbin object the execution routes check, from the definition row (read once per
    workflow; an unreadable definition contributes no attributes, as on the execution routes)."""
    key = (workflow_database_id, workflow_id)
    if key not in scope.workflows:
        try:
            scope.workflows[key] = executionAccess.read_workflow_definition(
                workflow_table, workflow_database_id, workflow_id)
        except Exception:
            logger.exception("Could not read the workflow definition for an execution "
                             "authorization object")
            scope.workflows[key] = {}
    return executionAccess.workflow_casbin_object(
        scope.workflows[key], workflow_database_id, workflow_id)


def _asset_row(scope: ExecutionReadScope, database_id: str,
               asset_id: str) -> Optional[Dict[str, Any]]:
    """An asset row the rule checks (archived rows included), read once per request; a copy, since
    the rule adds the object type to it."""
    key = (database_id, asset_id)
    if key not in scope.assets:
        scope.assets[key] = executionAccess.read_asset_row_including_archived(
            execution_asset_table, database_id, asset_id)
    row = scope.assets[key]
    return dict(row) if row else None


def _decide(scope: ExecutionReadScope, execution_id: str) -> bool:
    """Whether the caller may GET the execution's details: the route's Tier-1 check on its path,
    then the shared read rule over the execution's rows. A missing main row is a denial."""
    if not _enforce(scope, executionAccess.execution_details_api_object(execution_id), "GET"):
        return False
    main_item = executionAccess.read_execution_main_row(workflow_executions_table, execution_id)
    if not main_item:
        return False
    allowed, reason = executionAccess.execution_access_decision(
        main_item, "GET",
        enforce=lambda obj, action: _enforce(scope, obj, action),
        workflow_object=lambda database_id, workflow_id: _workflow_object(
            scope, database_id, workflow_id),
        config_row=lambda: executionAccess.read_execution_configuration_row(
            execution_configuration_table, execution_id),
        input_assets=lambda: executionAccess.read_execution_input_assets(
            execution_inputs_table, execution_id),
        asset_row=lambda database_id, asset_id: _asset_row(scope, database_id, asset_id),
    )
    if not allowed:
        logger.info(f"Execution {execution_id}'s failure reason is withheld: {reason}")
    return allowed


def may_read_execution(scope: ExecutionReadScope, execution_id: Optional[str]) -> bool:
    """Whether the scope's caller may read the execution, decided once per execution and request.
    False without an execution id, without an enforcer, and for an execution past the request's
    MAX_EXECUTIONS_DECIDED_PER_REQUEST bound."""
    if not execution_id or scope.casbin_enforcer is None:
        return False
    if execution_id not in scope.decisions:
        if len(scope.decisions) >= MAX_EXECUTIONS_DECIDED_PER_REQUEST:
            return False
        scope.decisions[execution_id] = _decide(scope, execution_id)
    return scope.decisions[execution_id]


def _result_for_caller(scope: ExecutionReadScope, result: Any) -> Any:
    """One rule result (a dict) without its failure reason unless the caller may read its
    execution; anything else as it is."""
    if not isinstance(result, dict) or not result.get(FAILURE_REASON_FIELD):
        return result
    if may_read_execution(scope, result.get("executionId")):
        return result
    return {key: value for key, value in result.items() if key != FAILURE_REASON_FIELD}


def rule_results_for_caller(scope: ExecutionReadScope, rule_results: Any) -> Any:
    """Rule results as the caller may read them, in the shape given: a list of result dicts, or the
    JSON-encoded list an evaluation row stores (re-encoded only when a reason was left out, so the
    text is otherwise returned as stored). Anything else is returned as it is."""
    if isinstance(rule_results, list):
        return [_result_for_caller(scope, result) for result in rule_results]
    if isinstance(rule_results, str):
        try:
            decoded = json.loads(rule_results)
        except (json.JSONDecodeError, TypeError):
            return rule_results
        if not isinstance(decoded, list):
            return rule_results
        filtered = [_result_for_caller(scope, result) for result in decoded]
        if filtered == decoded:
            return rule_results
        return json.dumps(filtered, default=str)
    return rule_results


def evaluation_for_caller(scope: ExecutionReadScope, evaluation: Any) -> Any:
    """An evaluation row (or evaluate response) with its rule results as the caller may read
    them."""
    if not isinstance(evaluation, dict) or "ruleResults" not in evaluation:
        return evaluation
    return {**evaluation,
            "ruleResults": rule_results_for_caller(scope, evaluation["ruleResults"])}


def state_record_for_caller(scope: ExecutionReadScope, record: Any) -> Any:
    """An asset-state record with its `quarantineExecutionFailures` as the caller may read them:
    each entry keeps its rule name and execution id, and its reason only when the caller may read
    that execution."""
    if not isinstance(record, dict):
        return record
    failures = record.get(QUARANTINE_EXECUTION_FAILURES_FIELD)
    if not isinstance(failures, list):
        return record
    return {**record,
            QUARANTINE_EXECUTION_FAILURES_FIELD: [_result_for_caller(scope, entry)
                                                  for entry in failures]}
