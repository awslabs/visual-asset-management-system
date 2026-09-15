#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance evaluation store: the AWS-facing side of the evaluation engine.

Owns every DynamoDB and Lambda access an evaluation needs — schema resolution, asset metadata
and link reads, metadata-schema field lookup, workflow-execution launch for pipeline rules,
evaluation / asset-state / audit writes — and orchestrates one evaluation through the pure
functions in `common/compliance/evaluationEngine.py`. The evaluate service, the trigger, the
cascade executor and the workflow callback all call into this module.

Evaluation record (COMPLIANCE_EVALUATION_STORAGE_TABLE, PK `evaluationId`):
    evaluationId, databaseId:assetId (AssetIndex PK), evaluatedAt (AssetIndex SK), databaseId,
    assetId, schemaName, status (completed | pending_pipeline | error | failed), verdict,
    violations, ruleResults (JSON list of RuleResult), pipelineRulesPending (JSON list of
    {ruleName, rule}), pipelineExecutions (list of {ruleName, executionId, status}),
    executionId + pipelineRuleName (ExecutionIdIndex; the first pipeline rule's execution),
    actor, completedAt, errorMessage.
Pipeline-execution tracking row (same table, one per started pipeline rule):
    evaluationId = "<parent evaluationId>#<ruleName>", recordType "pipelineExecution",
    parentEvaluationId, pipelineRuleName, executionId (ExecutionIdIndex), status (pending |
    completed), executionStatus, ruleResults, completedAt. The completion write is conditioned on
    `status = pending`, which is what makes a redelivered completion event a no-op; the parent's
    pipeline results are the aggregate of these rows.
    It carries no databaseId:assetId, so it stays out of the AssetIndex listing.
Asset-state record (COMPLIANCE_ASSET_STATE_STORAGE_TABLE, PK databaseId, SK assetId):
    schemaName (SchemaNameIndex PK), schemaSource (database | asset), complianceState
    (SchemaNameIndex SK), lastEvaluationId, lastEvaluatedAt, updatedAt, quarantine/exception
    fields written by the quarantine service.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.apiRoutes import API_EXECUTE_WORKFLOW
from common.compliance import evaluationEngine as engine
from common.dynamodb import query_all_items, to_update_expr
from common.resourceNames import ResourceKeys, get_table_name
from customLogging.logger import safeLogger
from models.compliance import (
    GLOBAL_DATABASE_ID,
    EvaluationVerdict,
    PipelineRule,
    RuleResult,
    determine_verdict,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)
logger = safeLogger(service_name="ComplianceEvaluationStore")

# Actor recorded on system-initiated evaluations and audit entries.
SYSTEM_ACTOR = "SYSTEM_USER"

# Tracking rows for pipeline-rule executions are keyed under the parent evaluation id.
PIPELINE_EXECUTION_RECORD_TYPE = "pipelineExecution"
PIPELINE_EXECUTION_KEY_SEPARATOR = "#"

# Statuses on a pipelineExecutions entry / tracking row.
PIPELINE_EXECUTION_PENDING = "pending"
PIPELINE_EXECUTION_COMPLETED = "completed"

try:
    schema_table_name = get_table_name(ResourceKeys.COMPLIANCE_SCHEMA_STORAGE_TABLE)
    asset_state_table_name = get_table_name(ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE)
    evaluation_table_name = get_table_name(ResourceKeys.COMPLIANCE_EVALUATION_STORAGE_TABLE)
    audit_table_name = get_table_name(ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    asset_links_table_name = get_table_name(ResourceKeys.ASSET_LINKS_STORAGE_TABLE_V2)
    asset_file_metadata_table_name = get_table_name(ResourceKeys.ASSET_FILE_METADATA_STORAGE_TABLE)
    metadata_schema_table_name = get_table_name(ResourceKeys.METADATA_SCHEMA_STORAGE_TABLE_V2)
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    # Set only on the Lambdas that launch pipeline-rule executions (evaluate service, trigger).
    execute_workflow_function_name = os.environ.get("EXECUTE_WORKFLOW_FUNCTION_NAME", "")
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

schema_table = dynamodb.Table(schema_table_name)
asset_state_table = dynamodb.Table(asset_state_table_name)
evaluation_table = dynamodb.Table(evaluation_table_name)
audit_table = dynamodb.Table(audit_table_name)
asset_table = dynamodb.Table(asset_table_name)
asset_links_table = dynamodb.Table(asset_links_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name)
metadata_schema_table = dynamodb.Table(metadata_schema_table_name)
workflow_table = dynamodb.Table(workflow_table_name)
pipeline_table = dynamodb.Table(pipeline_table_name)


def now_iso() -> str:
    """Current UTC time, ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


# --- Schema reads ---


def load_schema_item(schema_name: str) -> Optional[Dict[str, Any]]:
    """The latest `internalVersion` row of a schema, or None."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def load_schema_body(schema_name: str) -> Optional[Dict[str, Any]]:
    """The latest version's parsed schema body, or None when absent or unparseable."""
    item = load_schema_item(schema_name)
    if not item:
        return None
    return engine.parse_schema_body(item.get("schemaBody", "{}"))


def resolve_schema_rules(schema_body: Dict[str, Any]) -> Dict[str, Any]:
    """Merged raw rules for a schema body, following its `extends` chain through the table."""
    return engine.resolve_rules(schema_body, load_schema_body)


# --- Asset reads ---


def get_asset_item(database_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """The asset row, or None."""
    return asset_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}).get("Item")


def get_compliance_record(database_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """The asset's compliance state row, or None."""
    return asset_state_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}).get("Item")


def get_asset_metadata(database_id: str, asset_id: str) -> Dict[str, Any]:
    """The asset-level metadata as a typed key/value dict.

    Reads every row for the asset-root composite key `databaseId:assetId:/` from the
    `DatabaseIdAssetIdFilePathIndex` GSI, paged to exhaustion.
    """
    composite_key = f"{database_id}:{asset_id}:/"
    rows = query_all_items(
        asset_file_metadata_table,
        IndexName="DatabaseIdAssetIdFilePathIndex",
        KeyConditionExpression=Key("databaseId:assetId:filePath").eq(composite_key),
    )
    metadata: Dict[str, Any] = {}
    for row in rows:
        key = row.get("metadataKey")
        if key:
            metadata[key] = engine.coerce_metadata_value(
                row.get("metadataValue"), row.get("metadataValueType", "STRING"))
    return metadata


def get_metadata_schema_fields(
    database_id: str, schema_name: str,
) -> Optional[List[Dict[str, Any]]]:
    """Field definitions of the metadata schema named `schema_name` in `database_id`, falling
    back to the GLOBAL schema of that name. None when neither exists.

    `schemaName` is not a key on the metadata-schema table, so this reads the database's
    partition of the `DatabaseIdIndex` GSI to exhaustion and filters by name.
    """
    for candidate_database in _database_lookup_order(database_id):
        rows = query_all_items(
            metadata_schema_table,
            IndexName="DatabaseIdIndex",
            KeyConditionExpression=Key("databaseId").eq(candidate_database),
            FilterExpression=Attr("schemaName").eq(schema_name),
        )
        if rows:
            return engine.normalize_metadata_schema_fields(rows[0].get("fields", "[]"))
    return None


def _database_lookup_order(database_id: str) -> List[str]:
    """A database-scoped lookup followed by the GLOBAL fallback (once)."""
    if database_id == GLOBAL_DATABASE_ID:
        return [GLOBAL_DATABASE_ID]
    return [database_id, GLOBAL_DATABASE_ID]


def get_asset_links(database_id: str, asset_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Every link row touching the asset: `parents` (asset is the `to` side) and `children`
    (asset is the `from` side), each paged to exhaustion."""
    asset_key = f"{database_id}:{asset_id}"
    parents = query_all_items(
        asset_links_table,
        IndexName="toAssetGSI",
        KeyConditionExpression=Key("toAssetDatabaseId:toAssetId").eq(asset_key),
    )
    children = query_all_items(
        asset_links_table,
        IndexName="fromAssetGSI",
        KeyConditionExpression=Key("fromAssetDatabaseId:fromAssetId").eq(asset_key),
    )
    return {engine.LINK_DIRECTION_PARENTS: parents, engine.LINK_DIRECTION_CHILDREN: children}


def get_child_links(database_id: str, asset_id: str) -> List[Dict[str, Any]]:
    """Link rows where the asset is the `from` side, paged to exhaustion."""
    return query_all_items(
        asset_links_table,
        IndexName="fromAssetGSI",
        KeyConditionExpression=Key("fromAssetDatabaseId:fromAssetId").eq(
            f"{database_id}:{asset_id}"),
    )


def get_parent_links(database_id: str, asset_id: str) -> List[Dict[str, Any]]:
    """Link rows where the asset is the `to` side, paged to exhaustion."""
    return query_all_items(
        asset_links_table,
        IndexName="toAssetGSI",
        KeyConditionExpression=Key("toAssetDatabaseId:toAssetId").eq(
            f"{database_id}:{asset_id}"),
    )


# --- Workflow / pipeline definition reads (V2 tables) ---


def get_workflow_item(database_id: str, workflow_id: str) -> Optional[Dict[str, Any]]:
    """The V2 workflow row, or None."""
    return workflow_table.get_item(
        Key={"databaseId": database_id, "workflowId": workflow_id}).get("Item")


def get_pipeline_item(database_id: str, pipeline_id: str) -> Optional[Dict[str, Any]]:
    """The V2 pipeline row, or None."""
    return pipeline_table.get_item(
        Key={"databaseId": database_id, "pipelineId": pipeline_id}).get("Item")


# --- Writes ---

# Placeholders a status condition adds beside the `#f<n>` / `:v<n>` aliases `to_update_expr`
# generates. A boto3 `Attr(...)` condition object is not used for this: boto3 renders it with its own
# `:v0` placeholder and merges that over the caller's ExpressionAttributeValues, which would replace
# the first SET value of the update with the condition's value.
_STATUS_CONDITION_NAME = "#cond_status"
_STATUS_CONDITION_VALUE_PREFIX = ":cond_status"


def status_condition(*allowed_statuses: str, allow_absent: bool = False,
                     ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """A `(ConditionExpression, names, values)` triple asserting the row's `status` is one of
    `allowed_statuses` — or, with `allow_absent`, that the row carries no `status` yet."""
    names = {_STATUS_CONDITION_NAME: "status"}
    values = {f"{_STATUS_CONDITION_VALUE_PREFIX}{index}": status
              for index, status in enumerate(allowed_statuses)}
    clauses = [f"{_STATUS_CONDITION_NAME} = {placeholder}" for placeholder in values]
    if allow_absent:
        clauses.insert(0, f"attribute_not_exists({_STATUS_CONDITION_NAME})")
    return " OR ".join(clauses), names, values


def is_conditional_check_failure(error: Exception) -> bool:
    """Whether a DynamoDB write error is a ConditionalCheckFailedException (the row no longer
    satisfies the write's status condition), as opposed to a real failure that must surface."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return (response.get("Error") or {}).get("Code", "") == "ConditionalCheckFailedException"
    return False


def update_item(table, key: Dict[str, Any], updates: Dict[str, Any], condition=None,
                **kwargs) -> Dict[str, Any]:
    """SET the given attributes on one row (attribute names are aliased, so reserved words such
    as `status` are safe). `condition` is a `status_condition(...)` triple; its placeholders are
    merged beside the update's own."""
    names, values, expression = to_update_expr(updates)
    if condition is not None:
        condition_expression, condition_names, condition_values = condition
        names = {**names, **condition_names}
        values = {**values, **condition_values}
        kwargs["ConditionExpression"] = condition_expression
    return table.update_item(
        Key=key,
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        **kwargs,
    )


def update_evaluation(evaluation_id: str, updates: Dict[str, Any], condition=None,
                      **kwargs) -> Dict[str, Any]:
    """SET attributes on an evaluation (or tracking) row, optionally under a status condition."""
    return update_item(evaluation_table, {"evaluationId": evaluation_id}, updates,
                       condition=condition, **kwargs)


def update_asset_state(database_id: str, asset_id: str, updates: Dict[str, Any]) -> None:
    """SET attributes on an asset's compliance state row (created when absent)."""
    update_item(asset_state_table, {"databaseId": database_id, "assetId": asset_id}, updates)


def write_audit(
    database_id: str,
    asset_id: str,
    event_type: str,
    actor: str,
    details: Optional[Dict[str, Any]] = None,
    previous_state: Optional[str] = None,
    new_state: Optional[str] = None,
    schema_name: Optional[str] = None,
    evaluation_id: Optional[str] = None,
    cascade_id: Optional[str] = None,
) -> None:
    """One compliance audit entry (AssetIndex on `databaseId:assetId`, EventTypeIndex on
    `eventType`)."""
    item = {
        "entryId": str(uuid.uuid4()),
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "timestamp": now_iso(),
        "eventType": event_type,
        "databaseId": database_id,
        "assetId": asset_id,
        "actor": actor,
        "details": json.dumps(details or {}),
    }
    if previous_state is not None:
        item["previousState"] = previous_state
    if new_state is not None:
        item["newState"] = new_state
    if schema_name is not None:
        item["schemaName"] = schema_name
    if evaluation_id is not None:
        item["evaluationId"] = evaluation_id
    if cascade_id is not None:
        item["cascadeId"] = cascade_id
    audit_table.put_item(Item=item)


# --- Pipeline-rule execution launch ---


def build_execute_workflow_request(rule: PipelineRule, database_id: str, asset_id: str,
                                   execution_group_id: str = "") -> Dict[str, Any]:
    """The `ExecuteWorkflowRequestV2Model` body for one pipeline rule: the whole asset as the
    single input file, the rule's template (and its inputParameters as template tags) keyed by
    the pipeline id, and a manual trigger. `execution_group_id` is the evaluation id, so every
    execution the evaluation launches shares one group and its audit entry and completion event
    name the evaluation they belong to."""
    ref = rule.pipelineRef
    parameters: Dict[str, Any] = {}
    if ref.templateId:
        parameters["templateId"] = ref.templateId
    template_tags = engine.template_tags_from_input_parameters(rule.inputParameters)
    if template_tags:
        parameters["templateTags"] = template_tags
    body: Dict[str, Any] = {
        "inputFiles": [{
            "databaseId": database_id,
            "assetId": asset_id,
            "relativeFileKey": "/",
        }],
        "pipelineExecutionParameters": {ref.pipelineId: parameters},
        "triggerType": "manual",
    }
    if execution_group_id:
        body["executionGroupId"] = execution_group_id
    return body


def build_execute_workflow_event(workflow_database_id: str, workflow_id: str,
                                 body: Dict[str, Any]) -> Dict[str, Any]:
    """The `lambdaCrossCall` event for `POST /workflows/{databaseId}/{workflowId}/execute`,
    attributed to SYSTEM_USER."""
    # Synthetic internal route: the path is the API_EXECUTE_WORKFLOW template with its parameters
    # filled, so the execute-workflow dispatcher matches it the way it matches an API request.
    path = (API_EXECUTE_WORKFLOW.path
            .replace("{workflowDatabaseId}", workflow_database_id)
            .replace("{workflowId}", workflow_id))
    return {
        "lambdaCrossCall": {"userName": SYSTEM_ACTOR},
        "requestContext": {"http": {"method": "POST", "path": path}},
        "pathParameters": {
            "workflowDatabaseId": workflow_database_id,
            "workflowId": workflow_id,
        },
        "queryStringParameters": {},
        "body": json.dumps(body),
    }


def invoke_execute_workflow(workflow_database_id: str, workflow_id: str,
                            body: Dict[str, Any]) -> Optional[str]:
    """Launch a workflow execution through the execute-workflow Lambda; the new executionId, or
    None when the launch was refused or the function is not configured on this Lambda."""
    if not execute_workflow_function_name:
        logger.error("EXECUTE_WORKFLOW_FUNCTION_NAME is not configured; pipeline rule not started")
        return None
    response = lambda_client.invoke(
        FunctionName=execute_workflow_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(build_execute_workflow_event(workflow_database_id, workflow_id, body)),
    )
    payload = json.loads(response["Payload"].read())
    if payload.get("statusCode") != 200:
        logger.error(
            f"Execute workflow refused for {workflow_database_id}/{workflow_id}: "
            f"status={payload.get('statusCode')}")
        return None
    response_body = payload.get("body", "{}")
    if isinstance(response_body, str):
        response_body = json.loads(response_body or "{}")
    message = response_body.get("message", {})
    execution_id = message.get("executionId", "") if isinstance(message, dict) else ""
    return execution_id or None


def pipeline_execution_record_id(evaluation_id: str, rule_name: str) -> str:
    """Tracking-row key for one pipeline rule of an evaluation."""
    return f"{evaluation_id}{PIPELINE_EXECUTION_KEY_SEPARATOR}{rule_name}"


def start_pipeline_rule_executions(
    pipeline_rules: Dict[str, PipelineRule],
    evaluation_id: str,
    database_id: str,
    asset_id: str,
    evaluated_at: str,
) -> Tuple[List[Dict[str, Any]], List[RuleResult]]:
    """Launch one workflow execution per pipeline rule.

    Returns (started, failed): `started` entries are `{ruleName, executionId, status}` (a tracking
    row is written for each); `failed` holds a failed RuleResult for every rule whose execution
    could not be launched.
    """
    started: List[Dict[str, Any]] = []
    failed: List[RuleResult] = []
    for rule_name, rule in pipeline_rules.items():
        ref = rule.pipelineRef
        execution_id = None
        try:
            if get_workflow_item(ref.databaseId, ref.workflowId) is None:
                logger.error(f"Pipeline rule '{rule_name}' references a workflow that does not exist")
            elif get_pipeline_item(ref.pipelineDatabaseId, ref.pipelineId) is None:
                logger.error(f"Pipeline rule '{rule_name}' references a pipeline that does not exist")
            else:
                execution_id = invoke_execute_workflow(
                    ref.databaseId, ref.workflowId,
                    build_execute_workflow_request(rule, database_id, asset_id,
                                                   execution_group_id=evaluation_id))
        except Exception as e:
            logger.exception(f"Failed launching the workflow for pipeline rule '{rule_name}': {e}")

        if not execution_id:
            failed.extend(engine.failed_pipeline_rule_results(
                {rule_name: rule}, "Pipeline execution could not be started"))
            continue

        evaluation_table.put_item(Item={
            "evaluationId": pipeline_execution_record_id(evaluation_id, rule_name),
            "recordType": PIPELINE_EXECUTION_RECORD_TYPE,
            "parentEvaluationId": evaluation_id,
            "pipelineRuleName": rule_name,
            "executionId": execution_id,
            "status": PIPELINE_EXECUTION_PENDING,
            "evaluatedAt": evaluated_at,
        })
        started.append({
            "ruleName": rule_name,
            "executionId": execution_id,
            "status": PIPELINE_EXECUTION_PENDING,
        })
        logger.info(f"Started workflow execution {execution_id} for pipeline rule '{rule_name}'")
    return started, failed


# --- Evaluation orchestration ---


def run_evaluation(
    database_id: str,
    asset_id: str,
    schema_name: str,
    actor: str = SYSTEM_ACTOR,
) -> Dict[str, Any]:
    """Evaluate an asset against a schema.

    Metadata and relationship rules complete synchronously; pipeline rules launch workflow
    executions and leave the evaluation `pending_pipeline` until the workflow callback finalizes
    it. Writes the evaluation record, the asset-state row and the audit entry, and returns
    `{evaluationId, verdict, complianceState, ruleResults, pipelineRulesPending}`.
    """
    evaluated_at = now_iso()
    evaluation_id = str(uuid.uuid4())

    schema_body = load_schema_body(schema_name)
    if schema_body is None:
        return _record_error(evaluation_id, database_id, asset_id, schema_name,
                             "Schema not found", evaluated_at, actor)
    if not engine.is_vams_rules_schema(schema_body):
        return _record_error(evaluation_id, database_id, asset_id, schema_name,
                             "Schema is not vams-rules-v1 format", evaluated_at, actor)

    typed_rules = engine.parse_resolved_rules(resolve_schema_rules(schema_body))
    _, _, pipeline_rules = engine.split_rules(typed_rules)

    asset_metadata = get_asset_metadata(database_id, asset_id)
    metadata_schema_fields = {}
    for rule in typed_rules.values():
        ref = getattr(rule, "metadataSchemaRef", None)
        if ref is not None:
            key = engine.metadata_schema_ref_key(ref)
            if key not in metadata_schema_fields:
                metadata_schema_fields[key] = get_metadata_schema_fields(ref.databaseId, ref.schemaName)
    asset_links = get_asset_links(database_id, asset_id)

    rule_results, pipeline_rules = engine.evaluate_rules(
        typed_rules, asset_metadata, metadata_schema_fields, asset_links)

    started: List[Dict[str, Any]] = []
    if pipeline_rules:
        started, launch_failures = start_pipeline_rule_executions(
            pipeline_rules, evaluation_id, database_id, asset_id, evaluated_at)
        rule_results.extend(launch_failures)
        pipeline_rules = {
            name: rule for name, rule in pipeline_rules.items()
            if any(entry["ruleName"] == name for entry in started)
        }

    has_pending = bool(started)
    verdict = EvaluationVerdict.pending_pipeline if has_pending else determine_verdict(rule_results)
    compliance_state = engine.verdict_to_state(verdict)

    record: Dict[str, Any] = {
        "evaluationId": evaluation_id,
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": schema_name,
        "evaluatedAt": evaluated_at,
        "status": (engine.EVALUATION_STATUS_PENDING_PIPELINE if has_pending
                   else engine.EVALUATION_STATUS_COMPLETED),
        "verdict": verdict.value,
        "violations": engine.violations(rule_results),
        "ruleResults": json.dumps([r.dict() for r in rule_results]),
        "actor": actor,
    }
    if has_pending:
        record["pipelineRulesPending"] = engine.pipeline_rules_to_json(pipeline_rules)
        record["pipelineExecutions"] = started
        record["executionId"] = started[0]["executionId"]
        record["pipelineRuleName"] = started[0]["ruleName"]
    else:
        record["completedAt"] = evaluated_at
    evaluation_table.put_item(Item=record)

    previous = get_compliance_record(database_id, asset_id) or {}
    previous_state = previous.get("complianceState")
    update_asset_state(database_id, asset_id, {
        "complianceState": compliance_state,
        "schemaName": schema_name,
        "lastEvaluationId": evaluation_id,
        "lastEvaluatedAt": evaluated_at,
        "updatedAt": evaluated_at,
        **_quarantine_state_fields(compliance_state, rule_results),
    })

    write_audit(
        database_id, asset_id,
        event_type="compliance_check",
        actor=actor,
        schema_name=schema_name,
        evaluation_id=evaluation_id,
        details={
            "verdict": verdict.value,
            "ruleResultCount": len(rule_results),
            "pipelineRulesPending": len(started),
        },
        previous_state=previous_state,
        new_state=compliance_state,
    )
    _audit_state_transition(database_id, asset_id, actor, evaluation_id, previous_state,
                            compliance_state, engine.failed_rule_names(rule_results), schema_name)

    return {
        "evaluationId": evaluation_id,
        "verdict": verdict.value,
        "complianceState": compliance_state,
        "ruleResults": [r.dict() for r in rule_results],
        "pipelineRulesPending": len(started),
    }


def _quarantine_state_fields(compliance_state, rule_results) -> Dict[str, Any]:
    """The asset-state attributes that describe a quarantine: the failed-rule messages are kept as
    the quarantine reason while the asset is quarantined and cleared on any other state."""
    if compliance_state == engine.STATE_QUARANTINED:
        reason = "; ".join(engine.violations(rule_results)) or "Compliance evaluation failed"
        return {"quarantineReason": reason[:1024]}
    return {"quarantineReason": None}


def _record_error(evaluation_id, database_id, asset_id, schema_name, error_message,
                  evaluated_at, actor) -> Dict[str, Any]:
    """An evaluation that could not run (schema missing or not vams-rules-v1)."""
    evaluation_table.put_item(Item={
        "evaluationId": evaluation_id,
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": schema_name,
        "evaluatedAt": evaluated_at,
        "completedAt": evaluated_at,
        "status": engine.EVALUATION_STATUS_ERROR,
        "verdict": EvaluationVerdict.error.value,
        "errorMessage": error_message,
        "actor": actor,
    })
    return {
        "evaluationId": evaluation_id,
        "verdict": EvaluationVerdict.error.value,
        "complianceState": engine.STATE_UNKNOWN,
        "ruleResults": [],
        "pipelineRulesPending": 0,
        "error": error_message,
    }


def _audit_state_transition(database_id, asset_id, actor, evaluation_id, previous_state,
                            new_state, failed_rules, schema_name) -> None:
    """Audit an auto-release out of quarantine, and notify subscribers of a new quarantine."""
    if previous_state == engine.STATE_QUARANTINED and new_state == engine.STATE_COMPLIANT:
        write_audit(
            database_id, asset_id,
            event_type="quarantine_released",
            actor=actor,
            evaluation_id=evaluation_id,
            details={"reason": "Re-evaluation passed"},
            previous_state=engine.STATE_QUARANTINED,
            new_state=engine.STATE_COMPLIANT,
        )
    if new_state == engine.STATE_QUARANTINED:
        try:
            from handlers.compliance.complianceNotifications import notify_quarantine
            notify_quarantine(database_id, asset_id, schema_name, failed_rules)
        except Exception as e:
            logger.exception(f"Failed sending quarantine notification: {e}")


# --- Pipeline-rule completion (workflow callback) ---


def find_evaluation_rows_by_execution_id(execution_id: str) -> List[Dict[str, Any]]:
    """Every evaluation-table row carrying `executionId` (the parent record of the first pipeline
    rule and the tracking row of each started rule), via the ExecutionIdIndex GSI."""
    return query_all_items(
        evaluation_table,
        IndexName="ExecutionIdIndex",
        KeyConditionExpression=Key("executionId").eq(execution_id),
    )


def get_evaluation(evaluation_id: str, consistent_read: bool = False) -> Optional[Dict[str, Any]]:
    """One evaluation-table row, or None. `consistent_read` reads the row as of the write that
    preceded the call rather than an eventually-consistent replica."""
    return evaluation_table.get_item(
        Key={"evaluationId": evaluation_id}, ConsistentRead=consistent_read).get("Item")


def resolve_pipeline_execution(execution_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """The parent evaluation record and the pipeline rule name a workflow execution belongs to,
    or None when the execution is not a compliance execution."""
    rows = find_evaluation_rows_by_execution_id(execution_id)
    if not rows:
        return None
    tracking = next((r for r in rows if r.get("recordType") == PIPELINE_EXECUTION_RECORD_TYPE), None)
    if tracking is not None:
        # A sibling rule's callback may have written the parent moments ago; the consistent read
        # sees it, so a finalized evaluation is recognized before any work is done for it.
        parent = get_evaluation(tracking["parentEvaluationId"], consistent_read=True)
        rule_name = tracking.get("pipelineRuleName", "")
    else:
        parent = rows[0]
        rule_name = parent.get("pipelineRuleName", "")
    if parent is None or not rule_name:
        return None
    return parent, rule_name


def complete_pipeline_rule(
    evaluation: Dict[str, Any],
    rule_name: str,
    execution_status: str,
    compliance_output: Optional[Dict[str, Any]],
    started_at: Optional[str],
    completed_at: Optional[str],
) -> Dict[str, Any]:
    """Record one pipeline rule's outcome on a `pending_pipeline` evaluation and, once every
    started pipeline rule has reported, finalize the evaluation's verdict, the asset state and
    the audit trail.

    Idempotent under at-least-once delivery and safe under concurrent completions:

      - the rule's outcome lands on its tracking row under a `status = pending` condition, so a
        redelivered completion event finds the row completed and records nothing twice;
      - the evaluation's pipeline results are aggregated from the tracking rows, read consistently
        after this rule's write, rather than from the caller's snapshot of the parent record — the
        callback whose tracking write was the last to land therefore sees every sibling completed;
      - the progress write and the finalize write both carry a `status = pending_pipeline`
        condition, so two callbacks that each see the other completed finalize exactly once, and a
        late progress write cannot overwrite a finalized evaluation with a partial result set.

    Returns `{evaluationId, ruleName, finalized, verdict?, complianceState?}`.
    """
    evaluation_id = evaluation["evaluationId"]
    pending_rules = engine.pipeline_rules_from_json(evaluation.get("pipelineRulesPending", "[]"))
    rule = pending_rules.get(rule_name)
    if rule is None:
        logger.warning(f"Evaluation {evaluation_id} holds no pending pipeline rule '{rule_name}'")
        return _not_finalized(evaluation_id, rule_name)

    rule_results = engine.evaluate_pipeline_rules(
        {rule_name: rule}, execution_status, compliance_output, started_at, completed_at)
    finished_at = now_iso()

    try:
        update_evaluation(pipeline_execution_record_id(evaluation_id, rule_name), {
            "status": PIPELINE_EXECUTION_COMPLETED,
            "executionStatus": execution_status,
            "ruleResults": json.dumps([r.dict() for r in rule_results]),
            "completedAt": finished_at,
        }, condition=status_condition(PIPELINE_EXECUTION_PENDING, allow_absent=True))
    except Exception as e:
        if not is_conditional_check_failure(e):
            raise
        logger.info(f"Evaluation {evaluation_id} rule '{rule_name}' was already recorded; "
                    "the redelivered completion is ignored")
        return _not_finalized(evaluation_id, rule_name)

    executions, pipeline_results, outstanding = _aggregate_tracking_rows(
        evaluation_id, evaluation.get("pipelineExecutions") or [])
    started_rule_names = {entry.get("ruleName") for entry in executions}
    base_results = [
        result for result in engine.rule_results_from_json(evaluation.get("ruleResults", "[]"))
        if result.ruleName not in started_rule_names
    ]
    all_results = base_results + pipeline_results
    progress = {
        "pipelineExecutions": executions,
        "ruleResults": json.dumps([r.dict() for r in all_results]),
    }

    if outstanding:
        try:
            update_evaluation(evaluation_id, progress,
                              condition=status_condition(engine.EVALUATION_STATUS_PENDING_PIPELINE))
        except Exception as e:
            if not is_conditional_check_failure(e):
                raise
            logger.info(f"Evaluation {evaluation_id} was finalized by another callback")
        return _not_finalized(evaluation_id, rule_name)

    verdict = determine_verdict(all_results)
    compliance_state = engine.verdict_to_state(verdict)
    try:
        update_evaluation(evaluation_id, {
            **progress,
            "status": engine.EVALUATION_STATUS_COMPLETED,
            "verdict": verdict.value,
            "violations": engine.violations(all_results),
            "completedAt": finished_at,
        }, condition=status_condition(engine.EVALUATION_STATUS_PENDING_PIPELINE))
    except Exception as e:
        if not is_conditional_check_failure(e):
            raise
        logger.info(f"Evaluation {evaluation_id} was finalized by another callback")
        return _not_finalized(evaluation_id, rule_name)

    database_id = evaluation["databaseId"]
    asset_id = evaluation["assetId"]
    schema_name = evaluation.get("schemaName", "")
    previous = get_compliance_record(database_id, asset_id) or {}
    previous_state = previous.get("complianceState")
    update_asset_state(database_id, asset_id, {
        "complianceState": compliance_state,
        "lastEvaluationId": evaluation_id,
        "lastEvaluatedAt": finished_at,
        "updatedAt": finished_at,
        **_quarantine_state_fields(compliance_state, all_results),
    })
    write_audit(
        database_id, asset_id,
        event_type="compliance_check",
        actor=SYSTEM_ACTOR,
        schema_name=schema_name,
        evaluation_id=evaluation_id,
        details={
            "verdict": verdict.value,
            "pipelineExecutionStatus": execution_status,
            "phase": "pipeline_callback",
        },
        previous_state=previous_state,
        new_state=compliance_state,
    )
    _audit_state_transition(database_id, asset_id, SYSTEM_ACTOR, evaluation_id, previous_state,
                            compliance_state, engine.failed_rule_names(all_results), schema_name)
    return {
        "evaluationId": evaluation_id,
        "ruleName": rule_name,
        "finalized": True,
        "verdict": verdict.value,
        "complianceState": compliance_state,
    }


def _not_finalized(evaluation_id: str, rule_name: str) -> Dict[str, Any]:
    return {"evaluationId": evaluation_id, "ruleName": rule_name, "finalized": False}


def _aggregate_tracking_rows(
    evaluation_id: str, started: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[RuleResult], List[str]]:
    """Read every started pipeline rule's tracking row with a consistent read and fold them into
    `(pipelineExecutions entries with their current status, the completed rules' results in
    start order, the names of the rules still outstanding)`."""
    executions: List[Dict[str, Any]] = []
    results: List[RuleResult] = []
    outstanding: List[str] = []
    for entry in started:
        rule_name = entry.get("ruleName", "")
        row = get_evaluation(pipeline_execution_record_id(evaluation_id, rule_name),
                             consistent_read=True) or {}
        status = row.get("status") or PIPELINE_EXECUTION_PENDING
        executions.append({**entry, "status": status})
        if status == PIPELINE_EXECUTION_COMPLETED:
            results.extend(engine.rule_results_from_json(row.get("ruleResults", "[]")))
        else:
            outstanding.append(rule_name)
    return executions, results, outstanding
