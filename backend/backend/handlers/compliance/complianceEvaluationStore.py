#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance evaluation store: the AWS-facing side of the evaluation engine.

Owns every DynamoDB, S3 and Lambda access an evaluation needs — schema resolution, asset metadata
and link reads, metadata-schema field lookup, the asset file listing and workflow / pipeline /
template reads that select a pipeline rule's input files, workflow-execution launch for pipeline
rules, evaluation / asset-state / audit writes — and orchestrates one evaluation through the pure
functions in `common/compliance/evaluationEngine.py`. The evaluate service, the trigger, the
cascade executor and the workflow callback all call into this module.

Evaluation record (COMPLIANCE_EVALUATION_STORAGE_TABLE, PK `evaluationId`):
    evaluationId, databaseId:assetId (AssetIndex PK), evaluatedAt (AssetIndex SK), databaseId,
    assetId, schemaName + schemaVersion (the schema actually evaluated), status (completed |
    pending_pipeline | error | failed), verdict, violations, ruleResults (JSON list of RuleResult,
    each `evaluated` or `error`), hasRuleErrors + errorRules (the rules the tooling could not
    evaluate; see `evaluationEngine.TOOLING_FAILURES_APPLY_ENFORCEMENT`), pipelineRulesPending (JSON
    list of {ruleName, rule}), pipelineExecutions (list of {ruleName, executionId, status}; the
    status is `starting` until the launch is attempted, `pending` once launched, `completed` once
    the completion event landed, `not_started` when the launch failed — that rule's result on the
    row is `status: error`), executionId + pipelineRuleName (ExecutionIdIndex; the first pipeline
    rule's execution),
    exceptionApplied (true when a quarantine exception was active for the evaluation), actor,
    completedAt, errorMessage.
Pipeline-execution tracking row (same table, one per started pipeline rule):
    evaluationId = "<parent evaluationId>#<ruleName>", recordType "pipelineExecution",
    parentEvaluationId, pipelineRuleName, executionId (ExecutionIdIndex), status (pending |
    completed), executionStatus, ruleResults, completedAt. The completion write is conditioned on
    `status = pending`, which is what makes a redelivered completion event a no-op; the parent's
    pipeline results are the aggregate of these rows.
    It carries no databaseId:assetId, so it stays out of the AssetIndex listing.
    The parent row is written before any execution is launched, so a completion event always finds
    it; a completion the rows cannot resolve yet (a tracking row whose parent is absent, or an
    execution of a still-launching evaluation whose tracking row is not written) is reported by
    `ComplianceExecutionUnresolved` so the callback's invocation fails and is retried instead of
    dropping the completion.
Asset-state record (COMPLIANCE_ASSET_STATE_STORAGE_TABLE, PK databaseId, SK assetId):
    schemaName (SchemaNameIndex PK) and schemaSource (database | asset) — the binding, written only
    by the schema binding service and the trigger's registration of an asset under its database
    binding, never by an evaluation — complianceState (SchemaNameIndex SK), lastEvaluationId,
    lastEvaluatedAt (the evaluation's `evaluatedAt`), lastEvaluationStatus (the evaluation's status:
    completed | pending_pipeline | error), updatedAt, quarantine/exception fields written by the
    quarantine service (an evaluation clears the exception fields when it supersedes the exception).
    An evaluation writes the row only while it owns it — it is the row's `lastEvaluationId` or began
    after the row's `lastEvaluatedAt` — so an older evaluation's outcome never replaces a newer one.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.apiRoutes import API_EXECUTE_WORKFLOW
from common.compliance import evaluationEngine as engine
from common.dynamodb import query_all_items, to_update_expr
from common.resourceNames import ResourceKeys, get_table_name
from common.s3 import list_all_objects
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)
from common.workflows import executionValidation as execution_validation
from common.workflows.executionRecords import pipeline_composite_key
from customLogging.logger import safeLogger
from models.compliance import (
    GLOBAL_DATABASE_ID,
    INPUT_FILES_MODE_EXPLICIT,
    INPUT_FILES_MODE_WHOLE_ASSET,
    EvaluationVerdict,
    PipelineRule,
    RuleResult,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)
s3_client = boto3.client("s3", config=retry_config)
logger = safeLogger(service_name="ComplianceEvaluationStore")

# Actor recorded on system-initiated evaluations and audit entries.
SYSTEM_ACTOR = "SYSTEM_USER"

# Tracking rows for pipeline-rule executions are keyed under the parent evaluation id.
PIPELINE_EXECUTION_RECORD_TYPE = "pipelineExecution"
PIPELINE_EXECUTION_KEY_SEPARATOR = "#"

# Statuses on a pipelineExecutions entry / tracking row. `starting` and `not_started` occur on the
# parent's entries only: the parent row lists every pipeline rule as `starting` before the first
# launch, and a rule whose launch failed becomes `not_started` (its `status: error` result is on the
# parent's ruleResults; it has no tracking row).
PIPELINE_EXECUTION_STARTING = "starting"
PIPELINE_EXECUTION_PENDING = "pending"
PIPELINE_EXECUTION_COMPLETED = "completed"
PIPELINE_EXECUTION_NOT_STARTED = "not_started"


class ComplianceExecutionUnresolved(Exception):
    """A workflow execution is a compliance execution but its evaluation rows cannot be resolved
    yet: its tracking row exists and names a parent evaluation row that is absent, or the execution
    group names a `pending_pipeline` evaluation still launching (`starting` rules) while no tracking
    row carries this execution id. The caller fails the invocation so the completion is retried,
    not dropped."""

# Audit event type written when an evaluation clears an exception granted against another schema
# name or version.
AUDIT_EXCEPTION_SUPERSEDED = "exception_superseded"
# Audit event type written when an evaluation could not evaluate one or more of its rules
# (details: the rule names), or could not run at all (a missing or non-vams-rules schema).
AUDIT_EVALUATION_ERROR = "evaluation_error"

# The `errorMessage` of an evaluation whose every rule errored, so no verdict could be reached.
ALL_RULES_ERRORED_MESSAGE = "No rule could be evaluated"

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
    pipeline_templates_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATES_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
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
pipeline_templates_table = dynamodb.Table(pipeline_templates_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)


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


def get_template_item(pipeline_database_id: str, pipeline_id: str,
                      template_id: str) -> Optional[Dict[str, Any]]:
    """A pipeline's template row (its `overrides` apply to the pipeline systemConfig), or None."""
    return pipeline_templates_table.get_item(Key={
        "pipelineDatabaseId:pipelineId": pipeline_composite_key(pipeline_database_id, pipeline_id),
        "templateId": template_id,
    }).get("Item")


# --- Asset file listing ---


def asset_s3_location(asset: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """`(bucket name, key prefix ending in '/')` under which an asset's files live: the bucket row
    named by the asset's `bucketId` and the asset's `assetLocation.Key`. None when either is not
    resolvable."""
    location = asset.get("assetLocation") or {}
    key = location.get("Key") if isinstance(location, dict) else None
    bucket_id = asset.get("bucketId")
    if not key or not bucket_id:
        return None
    response = s3_asset_buckets_table.query(
        KeyConditionExpression=Key("bucketId").eq(bucket_id), Limit=1)
    rows = response.get("Items") or []
    bucket_name = rows[0].get("bucketName") if rows else None
    if not bucket_name:
        return None
    return bucket_name, key if key.endswith("/") else key + "/"


def resolve_asset_s3_location(database_id: str, asset_id: str) -> Optional[Tuple[str, str]]:
    """`asset_s3_location` of the asset row; None (logged) when the asset or its location cannot
    be resolved."""
    asset = get_asset_item(database_id, asset_id)
    location = asset_s3_location(asset) if asset else None
    if location is None:
        logger.error(f"Asset {database_id}:{asset_id} has no resolvable S3 location")
    return location


def list_file_keys_under(bucket: str, prefix: str) -> List[str]:
    """The asset-relative `/…` keys of every object under an asset's S3 prefix, paged to exhaustion,
    folder markers dropped, sorted. An archived file's current version is a delete marker, so it is
    not listed."""
    keys = set()
    for entry in list_all_objects(bucket, prefix, client=s3_client):
        key = entry.get("Key", "")
        if not key or key.endswith("/"):
            continue
        relative = key[len(prefix):] if key.startswith(prefix) else key
        keys.add("/" + relative.lstrip("/"))
    return sorted(keys)


def list_asset_file_keys(database_id: str, asset_id: str) -> Optional[List[str]]:
    """The asset-relative `/…` keys of the asset's current files (`list_file_keys_under` its S3
    location). None when the asset or its location cannot be resolved."""
    location = resolve_asset_s3_location(database_id, asset_id)
    if location is None:
        return None
    return list_file_keys_under(*location)


def object_change_source(bucket: str, key: str) -> str:
    """The `vams-changesource` object metadata of an object's current version, read with a HEAD;
    "" when the object carries none or cannot be read. Unreadable is reported as unknown rather than
    as a workflow write, so a missing permission or a deleted object leaves the file in play."""
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
    except Exception as e:
        logger.info(f"Could not read the change provenance of an object under {bucket}: {e}")
        return ""
    return (head.get("Metadata") or {}).get(VAMS_CHANGE_SOURCE_METADATA_KEY, "") or ""


def asset_file_change_source(location: Tuple[str, str], relative_key: str) -> str:
    """`object_change_source` of an asset-relative `/…` key under the asset's S3 location."""
    bucket, prefix = location
    return object_change_source(bucket, prefix + relative_key.lstrip("/"))


# --- Pipeline-rule input selection ---

# The only client-visible texts for an input selection that cannot be launched. They name neither
# the asset's files nor the filters involved (backend Rule 11); the specifics are logged.
INPUT_SELECTION_REFUSED = "Pipeline rule input selection is not accepted by the workflow"
INPUT_SELECTION_NOT_ONE_FILE = "Pipeline rule input selection did not match exactly one file"
INPUT_SELECTION_MISSING_FILE = "Pipeline rule input selection names a file the asset does not have"

# The asset root as an execute-request relativeFileKey.
WHOLE_ASSET_KEY = "/"

ARITY_NONE = "none"
ARITY_ONE = "one"


def resolve_pipeline_rule_inputs(
    rule: PipelineRule,
    database_id: str,
    asset_id: str,
    workflow: Dict[str, Any],
    pipeline: Dict[str, Any],
    template: Optional[Dict[str, Any]],
    asset_file_keys: Callable[[], Optional[List[str]]],
    file_change_source: Optional[Callable[[str], str]] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """The execute-request `inputFiles` a pipeline rule's `inputFiles` selection resolves to against
    the asset's current files, or `(None, message)` when the selection cannot be launched.

    The pipeline's effective config is its systemConfig with the chosen template's `overrides`
    merged; the arity is the workflow's `inputFileArity`, falling back to the pipeline's when the
    workflow declares none. `wholeAsset` sends the asset root; `matching` lists the asset's files,
    applies the workflow's, then the pipeline's, then the rule's own filters, drops the files a
    workflow execution wrote, and sorts by key; `explicit` requires every listed key to exist and
    sends the keys as given. The resolved selection then passes the same cross-entity validation the
    execute-workflow handler runs (arity, asset scope, filters, a disabled or archived pipeline), so
    a selection the workflow would refuse is reported here.

    `asset_file_keys` is read only by the modes that need the listing. `file_change_source` maps an
    asset-relative key to its `vams-changesource` object metadata (a HEAD on the object); `matching`
    calls it for the files that survive the filter chain and drops those a workflow execution wrote
    (`workflowExecution`) — such files are pipeline outputs, not sources, and a rule whose own
    pipeline writes back into the asset would otherwise select its previous output the next time
    round. A file whose provenance cannot be read ("") stays selected.
    """
    selection = rule.inputFiles
    workflow_config = workflow.get("systemConfig") or {}
    template_overrides = (template or {}).get("overrides") if template else None
    pipeline_config = execution_validation.resolve_effective_pipeline_config(
        pipeline.get("systemConfig") or {}, template_overrides)
    arity = execution_validation._arity(
        workflow_config if workflow_config.get("inputFileArity") else pipeline_config)

    def as_inputs(keys: List[str]) -> List[Dict[str, Any]]:
        return [{"databaseId": database_id, "assetId": asset_id, "relativeFileKey": key}
                for key in keys]

    if selection.mode == INPUT_FILES_MODE_WHOLE_ASSET:
        inputs = as_inputs([WHOLE_ASSET_KEY])
    else:
        file_keys = asset_file_keys()
        if file_keys is None:
            return None, INPUT_SELECTION_REFUSED
        if selection.mode == INPUT_FILES_MODE_EXPLICIT:
            missing = [key for key in selection.keys or [] if key not in file_keys]
            if missing:
                logger.info(f"Pipeline rule names {len(missing)} file(s) the asset "
                            f"{database_id}:{asset_id} does not have")
                return None, INPUT_SELECTION_MISSING_FILE
            inputs = as_inputs(sorted(selection.keys or []))
        else:
            candidates = as_inputs(file_keys)
            candidates = execution_validation.apply_input_file_filters(
                candidates, workflow_config.get("inputFileFilters"))
            candidates = execution_validation.apply_input_file_filters(
                candidates, pipeline_config.get("inputFileFilters"))
            if selection.filter:
                candidates = execution_validation.apply_input_file_filters(
                    candidates, {"allow": selection.filter})
            if file_change_source is not None:
                candidates = _without_workflow_outputs(candidates, file_change_source,
                                                       database_id, asset_id)
            candidates.sort(key=lambda entry: entry["relativeFileKey"])
            inputs = [] if arity == ARITY_NONE else candidates
        if arity == ARITY_ONE and len(inputs) != 1:
            logger.info(f"Pipeline rule input selection matched {len(inputs)} file(s) of asset "
                        f"{database_id}:{asset_id}; the workflow accepts exactly one")
            return None, INPUT_SELECTION_NOT_ONE_FILE

    ref = rule.pipelineRef
    errors, _ = execution_validation.validate_execution(workflow_config, [{
        "pipelineId": ref.pipelineId,
        "pipelineDatabaseId": ref.pipelineDatabaseId,
        "enabled": pipeline.get("enabled", True),
        "archived": pipeline.get("archived", False),
        "systemConfig": pipeline_config,
    }], inputs)
    if errors:
        logger.info(f"Pipeline rule input selection for {database_id}:{asset_id} refused by "
                    f"workflow {ref.databaseId}/{ref.workflowId}: {errors}")
        return None, INPUT_SELECTION_REFUSED
    return inputs, None


def _without_workflow_outputs(candidates: List[Dict[str, Any]],
                              file_change_source: Callable[[str], str],
                              database_id: str, asset_id: str) -> List[Dict[str, Any]]:
    """The candidates minus the files whose `vams-changesource` is `workflowExecution`."""
    kept = []
    for entry in candidates:
        if file_change_source(entry["relativeFileKey"]) == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION:
            continue
        kept.append(entry)
    dropped = len(candidates) - len(kept)
    if dropped:
        logger.info(f"Pipeline rule input selection for {database_id}:{asset_id} leaves out "
                    f"{dropped} file(s) a workflow execution wrote")
    return kept


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


def update_asset_state(database_id: str, asset_id: str, updates: Dict[str, Any],
                       condition=None) -> None:
    """SET attributes on an asset's compliance state row (created when absent), optionally under a
    `(ConditionExpression, names, values)` condition."""
    update_item(asset_state_table, {"databaseId": database_id, "assetId": asset_id}, updates,
                condition=condition)


_OWNERSHIP_AT_NAME = "#ownedEvaluatedAt"
_OWNERSHIP_ID_NAME = "#ownedEvaluationId"


def evaluation_ownership_condition(evaluation_id: str, evaluated_at: str,
                                   ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """The write-time form of `engine.evaluation_owns_state_row`: the asset-state row records no
    evaluation yet, or its last evaluation began before this one, or it is this evaluation. Applied
    to every state write an evaluation makes, so two evaluations finalizing within the same instant
    cannot let the older one land after the newer one's read."""
    names = {_OWNERSHIP_AT_NAME: "lastEvaluatedAt", _OWNERSHIP_ID_NAME: "lastEvaluationId"}
    values = {":ownedEvaluatedAt": evaluated_at, ":ownedEvaluationId": evaluation_id}
    expression = (f"attribute_not_exists({_OWNERSHIP_AT_NAME}) OR "
                  f"{_OWNERSHIP_AT_NAME} < :ownedEvaluatedAt OR "
                  f"{_OWNERSHIP_ID_NAME} = :ownedEvaluationId")
    return expression, names, values


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
    `eventType`). Best-effort: a failed write is logged and never raised, so the mutation it
    describes — already applied — is not reported as failed."""
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
    try:
        audit_table.put_item(Item=item)
    except Exception as e:
        logger.exception(
            f"Failed writing audit entry {event_type} for {database_id}:{asset_id}: {e}")


# --- Pipeline-rule execution launch ---


def build_execute_workflow_request(rule: PipelineRule, inputs: List[Dict[str, Any]],
                                   execution_group_id: str = "") -> Dict[str, Any]:
    """The `ExecuteWorkflowRequestV2Model` body for one pipeline rule: the resolved input files
    (`resolve_pipeline_rule_inputs`), the rule's template (and its inputParameters as template
    tags) keyed by the pipeline id, and a manual trigger. `execution_group_id` is the evaluation
    id, so every execution the evaluation launches shares one group and its audit entry and
    completion event name the evaluation they belong to."""
    ref = rule.pipelineRef
    parameters: Dict[str, Any] = {}
    if ref.templateId:
        parameters["templateId"] = ref.templateId
    template_tags = engine.template_tags_from_input_parameters(rule.inputParameters)
    if template_tags:
        parameters["templateTags"] = template_tags
    body: Dict[str, Any] = {
        "inputFiles": [dict(entry) for entry in inputs],
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


LAUNCH_FAILED_MESSAGE = "Pipeline execution could not be started"


def start_pipeline_rule_executions(
    pipeline_rules: Dict[str, PipelineRule],
    evaluation_id: str,
    database_id: str,
    asset_id: str,
    evaluated_at: str,
) -> Tuple[List[Dict[str, Any]], List[RuleResult]]:
    """Launch one workflow execution per pipeline rule.

    Returns (started, errored): `started` entries are `{ruleName, executionId, status}` (a tracking
    row is written for each); `errored` holds a `status: error` RuleResult for every rule whose
    execution could not be launched — a workflow, pipeline or template that does not exist or a
    refused launch (`LAUNCH_FAILED_MESSAGE`), or an input selection the workflow does not accept (one
    of the INPUT_SELECTION_* messages). These are rules the tooling could not evaluate, so
    `evaluationEngine.TOOLING_FAILURES_APPLY_ENFORCEMENT` decides whether they count against the
    asset. The asset's location is resolved and its files listed once per evaluation, on the first
    rule that needs them, and each file's change provenance is read at most once.
    """
    started: List[Dict[str, Any]] = []
    errored: List[RuleResult] = []
    cache: Dict[str, Any] = {}
    provenance: Dict[str, str] = {}

    def asset_location() -> Optional[Tuple[str, str]]:
        if "location" not in cache:
            cache["location"] = resolve_asset_s3_location(database_id, asset_id)
        return cache["location"]

    def asset_file_keys() -> Optional[List[str]]:
        if "keys" not in cache:
            location = asset_location()
            cache["keys"] = list_file_keys_under(*location) if location else None
        return cache["keys"]

    def file_change_source(relative_key: str) -> str:
        if relative_key not in provenance:
            location = asset_location()
            provenance[relative_key] = (
                asset_file_change_source(location, relative_key) if location else "")
        return provenance[relative_key]

    for rule_name, rule in pipeline_rules.items():
        ref = rule.pipelineRef
        execution_id = None
        failure_message = LAUNCH_FAILED_MESSAGE
        try:
            workflow = get_workflow_item(ref.databaseId, ref.workflowId)
            pipeline = get_pipeline_item(ref.pipelineDatabaseId, ref.pipelineId)
            template = None
            if ref.templateId and pipeline is not None:
                template = get_template_item(ref.pipelineDatabaseId, ref.pipelineId, ref.templateId)
            if workflow is None:
                logger.error(f"Pipeline rule '{rule_name}' references a workflow that does not exist")
            elif pipeline is None:
                logger.error(f"Pipeline rule '{rule_name}' references a pipeline that does not exist")
            elif ref.templateId and template is None:
                logger.error(f"Pipeline rule '{rule_name}' references a template that does not exist")
            else:
                inputs, selection_error = resolve_pipeline_rule_inputs(
                    rule, database_id, asset_id, workflow, pipeline, template, asset_file_keys,
                    file_change_source)
                if selection_error:
                    failure_message = selection_error
                else:
                    execution_id = invoke_execute_workflow(
                        ref.databaseId, ref.workflowId,
                        build_execute_workflow_request(rule, inputs,
                                                       execution_group_id=evaluation_id))
        except Exception as e:
            logger.exception(f"Failed launching the workflow for pipeline rule '{rule_name}': {e}")

        if not execution_id:
            errored.extend(engine.errored_pipeline_rule_results({rule_name: rule}, failure_message))
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
    return started, errored


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
    it. Writes the evaluation record, the asset-state row and the audit entries, and returns
    `{evaluationId, verdict, complianceState, ruleResults, pipelineRulesPending, schemaVersion,
    exceptionApplied, hasRuleErrors, errorRules}`.

    The asset-state row receives the state and the evaluation pointers only: the binding
    (`schemaName` / `schemaSource`) is never written here, so an evaluation against another schema
    leaves the binding as it is and records the schema it used on the evaluation row alone. The row
    is written only while this evaluation owns it (`engine.evaluation_owns_state_row`): an evaluation
    that began before the row's last one leaves the row alone and is recorded on its own row only.

    A pipeline rule the tooling could not evaluate (its input selection refused, its launch not
    started) is a `status: error` result. With `engine.TOOLING_FAILURES_APPLY_ENFORCEMENT` off, such
    a rule does not bear on the verdict: the evaluation row records it under `hasRuleErrors` /
    `errorRules` and an `evaluation_error` audit entry names it; when no rule remains the verdict is
    `error`, the evaluation `status` is `error` and the asset's `complianceState` is left as it was.

    An exception granted against this schema name and version keeps the asset released: the
    verdict is recorded as computed, the evaluation row carries `exceptionApplied`, and the state
    becomes `exception` on a failing verdict. An exception granted against another schema or an
    earlier version is superseded — cleared, audited, and the verdict applied normally.
    """
    evaluated_at = now_iso()
    evaluation_id = str(uuid.uuid4())

    schema_item = load_schema_item(schema_name)
    if schema_item is None:
        return _record_error(evaluation_id, database_id, asset_id, schema_name,
                             "Schema not found", evaluated_at, actor)
    schema_body = engine.parse_schema_body(schema_item.get("schemaBody", "{}"))
    if not engine.is_vams_rules_schema(schema_body):
        return _record_error(evaluation_id, database_id, asset_id, schema_name,
                             "Schema is not vams-rules-v1 format", evaluated_at, actor)
    schema_version = engine.schema_version_number(schema_item.get("internalVersion"))

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

    base_record = _evaluation_record_base(evaluation_id, database_id, asset_id, schema_name,
                                          schema_version, evaluated_at, actor)
    started: List[Dict[str, Any]] = []
    if pipeline_rules:
        # The parent row goes down before the first launch, listing every pipeline rule as
        # `starting`: a completion event that lands while the launches are still under way finds
        # its parent, and the rules not yet launched keep the evaluation from finalizing until the
        # launch outcome is recorded below.
        evaluation_table.put_item(Item={
            **base_record,
            "status": engine.EVALUATION_STATUS_PENDING_PIPELINE,
            "verdict": EvaluationVerdict.pending_pipeline.value,
            "violations": engine.violations(rule_results),
            "ruleResults": json.dumps([r.dict() for r in rule_results]),
            "hasRuleErrors": False,
            "pipelineRulesPending": engine.pipeline_rules_to_json(pipeline_rules),
            "pipelineExecutions": [{"ruleName": name, "status": PIPELINE_EXECUTION_STARTING}
                                   for name in pipeline_rules],
        })
        started, launch_errors = start_pipeline_rule_executions(
            pipeline_rules, evaluation_id, database_id, asset_id, evaluated_at)
        rule_results.extend(launch_errors)

    has_pending = bool(started)
    verdict = (EvaluationVerdict.pending_pipeline if has_pending
               else engine.determine_verdict(rule_results))
    status = engine.evaluation_status_for(verdict)
    error_rules = engine.errored_rule_names(rule_results)

    previous = get_compliance_record(database_id, asset_id) or {}
    exception_applies, compliance_state = _state_for_verdict(
        verdict, previous, schema_name, schema_version)
    audit_details = {
        "verdict": verdict.value,
        "ruleResultCount": len(rule_results),
        "pipelineRulesPending": len(started),
        **({"exceptionApplied": True} if exception_applies else {}),
        **({"errorRules": error_rules} if error_rules else {}),
    }
    result = {
        "evaluationId": evaluation_id,
        "verdict": verdict.value,
        "complianceState": compliance_state if compliance_state is not None
        else previous.get("complianceState", engine.STATE_UNKNOWN),
        "ruleResults": [r.dict() for r in rule_results],
        "pipelineRulesPending": len(started),
        "schemaVersion": schema_version,
        "exceptionApplied": exception_applies,
        "hasRuleErrors": bool(error_rules),
        "errorRules": error_rules,
    }

    if not has_pending:
        # No execution is in flight (none was launched, or none started), so the row is written
        # whole as a completed evaluation; when pipeline rules were listed this replaces the
        # `starting` row and nothing can have written to it in between.
        record: Dict[str, Any] = {
            **base_record,
            "status": status,
            "verdict": verdict.value,
            "violations": engine.violations(rule_results),
            "ruleResults": json.dumps([r.dict() for r in rule_results]),
            "completedAt": evaluated_at,
            **_rule_error_fields(error_rules),
        }
        if exception_applies:
            record["exceptionApplied"] = True
        if verdict == EvaluationVerdict.error:
            record["errorMessage"] = ALL_RULES_ERRORED_MESSAGE
        evaluation_table.put_item(Item=record)
        if error_rules:
            _write_evaluation_error_audit(database_id, asset_id, actor, schema_name, evaluation_id,
                                          {"ruleNames": error_rules, "verdict": verdict.value})
        _write_evaluation_state(
            database_id, asset_id, actor, evaluation_id, evaluated_at, evaluated_at, schema_name,
            schema_version, previous, compliance_state, status, rule_results, audit_details)
        return result

    # The pending state row and its audit go down before the launch outcome opens the evaluation
    # to finalization, so a callback that finalizes right after finds the row it supersedes.
    if error_rules:
        _write_evaluation_error_audit(database_id, asset_id, actor, schema_name, evaluation_id,
                                      {"ruleNames": error_rules, "verdict": verdict.value})
    _write_evaluation_state(
        database_id, asset_id, actor, evaluation_id, evaluated_at, evaluated_at, schema_name,
        schema_version, previous, compliance_state, status, rule_results, audit_details)

    launched = {name: rule for name, rule in pipeline_rules.items()
                if any(entry["ruleName"] == name for entry in started)}
    launch_outcome = {
        "pipelineExecutions": _launch_outcome_entries(pipeline_rules, started),
        "pipelineRulesPending": engine.pipeline_rules_to_json(launched),
        "executionId": started[0]["executionId"],
        "pipelineRuleName": started[0]["ruleName"],
        "ruleResults": json.dumps([r.dict() for r in rule_results]),
        **_rule_error_fields(error_rules),
    }
    if exception_applies:
        launch_outcome["exceptionApplied"] = True
    update_evaluation(evaluation_id, launch_outcome)

    # A completion that landed during the launches could not finalize while a sibling rule was
    # still `starting`; once every launched rule has reported, this run finalizes the evaluation.
    current = get_evaluation(evaluation_id, consistent_read=True)
    if current is not None and current.get("status") == engine.EVALUATION_STATUS_PENDING_PIPELINE:
        outcome = _finalize_when_complete(current, phase="launch")
        if outcome["finalized"]:
            result.update({
                "verdict": outcome["verdict"],
                "complianceState": outcome["complianceState"],
                "pipelineRulesPending": 0,
            })
    return result


def _evaluation_record_base(evaluation_id, database_id, asset_id, schema_name, schema_version,
                            evaluated_at, actor) -> Dict[str, Any]:
    """The attributes every evaluation row carries whatever its outcome."""
    record: Dict[str, Any] = {
        "evaluationId": evaluation_id,
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": schema_name,
        "evaluatedAt": evaluated_at,
        "actor": actor,
    }
    if schema_version is not None:
        record["schemaVersion"] = schema_version
    return record


def _launch_outcome_entries(pipeline_rules, started: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The `pipelineExecutions` entries once every launch was attempted, in rule order: a launched
    rule with its execution id (`pending`), a rule whose launch failed as `not_started`."""
    by_name = {entry["ruleName"]: entry for entry in started}
    return [
        dict(by_name[name]) if name in by_name
        else {"ruleName": name, "status": PIPELINE_EXECUTION_NOT_STARTED}
        for name in pipeline_rules
    ]


def _state_for_verdict(verdict: EvaluationVerdict, previous: Dict[str, Any], schema_name: str,
                       schema_version: Any) -> Tuple[bool, Optional[str]]:
    """`(exception applies, compliance state)` an evaluation's verdict produces against the asset's
    current state row. A verdict of `error` produces no state (None): the asset keeps the state it
    has, and an exception on the row is neither applied nor superseded."""
    if verdict == EvaluationVerdict.error:
        return False, None
    exception_applies = engine.exception_applies(previous, schema_name, schema_version)
    state = (engine.exception_state(verdict) if exception_applies
             else engine.verdict_to_state(verdict))
    return exception_applies, state


def _rule_error_fields(error_rules: List[str]) -> Dict[str, Any]:
    """The evaluation-row attributes that record the rules the tooling could not evaluate."""
    fields: Dict[str, Any] = {"hasRuleErrors": bool(error_rules)}
    if error_rules:
        fields["errorRules"] = list(error_rules)
    return fields


def _write_evaluation_state(database_id, asset_id, actor, evaluation_id, evaluated_at, updated_at,
                            schema_name, schema_version, previous, compliance_state,
                            evaluation_status, rule_results, audit_details) -> bool:
    """Apply an evaluation's outcome to the asset-state row and the audit trail: the state and the
    evaluation pointers (`lastEvaluationId`, `lastEvaluatedAt` = the evaluation's `evaluatedAt`,
    `lastEvaluationStatus`; never the binding), the quarantine reason, the clearing of an exception
    the evaluation supersedes (audited as `exception_superseded`), the `compliance_check` entry, and
    the release audit / quarantine notification a state transition calls for.

    The row is written only while this evaluation owns it (`engine.evaluation_owns_state_row` on
    the row as read, and `evaluation_ownership_condition` on the write itself, so an evaluation that
    lands between the read and the write cannot be overwritten by an older one); an evaluation a
    newer one has already superseded leaves the row and the audit trail alone (logged), so its
    outcome is recorded on its own evaluation row only. A `compliance_state` of None (a verdict of
    `error`) writes the evaluation pointers and status and leaves the state, the quarantine reason
    and any exception as they are. Returns whether the row was written."""
    if not engine.evaluation_owns_state_row(previous, evaluation_id, evaluated_at):
        logger.info(f"Evaluation {evaluation_id} of {database_id}:{asset_id} is older than the "
                    f"asset's last evaluation {previous.get('lastEvaluationId')}; the asset state "
                    "is left as it is")
        return False
    ownership = evaluation_ownership_condition(evaluation_id, evaluated_at)

    updates: Dict[str, Any] = {
        "lastEvaluationId": evaluation_id,
        "lastEvaluatedAt": evaluated_at,
        "lastEvaluationStatus": evaluation_status,
        "updatedAt": updated_at,
    }
    if compliance_state is None:
        # An asset with no state yet is `unknown` (what a missing row reads as), so the row an error
        # evaluation creates is a well-formed one.
        if "complianceState" not in previous:
            updates["complianceState"] = engine.STATE_UNKNOWN
        return _write_owned_state(database_id, asset_id, evaluation_id, updates, ownership)

    previous_state = previous.get("complianceState")
    updates["complianceState"] = compliance_state
    updates.update(_quarantine_state_fields(compliance_state, rule_results))
    superseded = engine.exception_is_superseded(previous, schema_name, schema_version)
    if superseded:
        updates.update(engine.cleared_exception_fields())
    if not _write_owned_state(database_id, asset_id, evaluation_id, updates, ownership):
        return False

    if superseded:
        write_audit(
            database_id, asset_id,
            event_type=AUDIT_EXCEPTION_SUPERSEDED,
            actor=actor,
            schema_name=schema_name,
            evaluation_id=evaluation_id,
            details={
                "exceptionSchemaName": previous.get(engine.EXCEPTION_SCHEMA_NAME_FIELD),
                "exceptionSchemaVersion": engine.schema_version_number(
                    previous.get(engine.EXCEPTION_SCHEMA_VERSION_FIELD)),
                "schemaName": schema_name,
                "schemaVersion": schema_version,
            },
            previous_state=previous_state,
            new_state=compliance_state,
        )
    write_audit(
        database_id, asset_id,
        event_type="compliance_check",
        actor=actor,
        schema_name=schema_name,
        evaluation_id=evaluation_id,
        details=audit_details,
        previous_state=previous_state,
        new_state=compliance_state,
    )
    _audit_state_transition(database_id, asset_id, actor, evaluation_id, previous_state,
                            compliance_state, engine.failed_rule_names(rule_results), schema_name)
    return True


def _write_owned_state(database_id, asset_id, evaluation_id, updates, ownership) -> bool:
    """The asset-state write under the evaluation's ownership condition; a conditional failure
    means a newer evaluation wrote the row first, which is logged and reported as not written."""
    try:
        update_asset_state(database_id, asset_id, updates, condition=ownership)
    except Exception as e:
        if not is_conditional_check_failure(e):
            raise
        logger.info(f"Evaluation {evaluation_id} of {database_id}:{asset_id} lost the asset-state "
                    "write to a newer evaluation; the asset state is left as it is")
        return False
    return True


def _write_evaluation_error_audit(database_id, asset_id, actor, schema_name, evaluation_id,
                                  details) -> None:
    """The `evaluation_error` audit entry: an evaluation that could not evaluate some or all of its
    rules, or could not run at all. Written whatever the asset-state row's ordering says, since it
    describes the evaluation rather than the asset's state."""
    write_audit(
        database_id, asset_id,
        event_type=AUDIT_EVALUATION_ERROR,
        actor=actor,
        schema_name=schema_name,
        evaluation_id=evaluation_id,
        details=details,
    )


def _quarantine_state_fields(compliance_state, rule_results) -> Dict[str, Any]:
    """The asset-state attributes that describe a quarantine: the failed-rule messages are kept as
    the quarantine reason while the asset is quarantined and cleared on any other state (an
    `exception` state keeps its violations on the evaluation row only)."""
    if compliance_state == engine.STATE_QUARANTINED:
        reason = "; ".join(engine.violations(rule_results)) or "Compliance evaluation failed"
        return {"quarantineReason": reason[:1024]}
    return {"quarantineReason": None}


def _record_error(evaluation_id, database_id, asset_id, schema_name, error_message,
                  evaluated_at, actor) -> Dict[str, Any]:
    """An evaluation that could not run (schema missing or not vams-rules-v1): an `error` evaluation
    row, an `evaluation_error` audit entry, and — while this evaluation owns the asset-state row —
    the row's evaluation pointers with `lastEvaluationStatus: error`. The asset's `complianceState`
    is left as it is."""
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
    _write_evaluation_error_audit(database_id, asset_id, actor, schema_name, evaluation_id,
                                  {"ruleNames": [], "errorMessage": error_message})
    previous = get_compliance_record(database_id, asset_id) or {}
    _write_evaluation_state(
        database_id, asset_id, actor, evaluation_id, evaluated_at, evaluated_at, schema_name,
        None, previous, None, engine.EVALUATION_STATUS_ERROR, [], audit_details={})
    return {
        "evaluationId": evaluation_id,
        "verdict": EvaluationVerdict.error.value,
        "complianceState": previous.get("complianceState", engine.STATE_UNKNOWN),
        "ruleResults": [],
        "pipelineRulesPending": 0,
        "schemaVersion": None,
        "exceptionApplied": False,
        "hasRuleErrors": False,
        "errorRules": [],
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


# Evaluation rows read newest-first while looking for the last verdict; an asset's history is
# rarely deeper than this before a verdict-bearing evaluation appears.
LATEST_VERDICT_LOOKBACK = 25
VERDICT_BEARING = (
    EvaluationVerdict.compliant.value,
    EvaluationVerdict.non_compliant.value,
    EvaluationVerdict.quarantined.value,
)


def latest_verdict_evaluation(database_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """The asset's newest evaluation whose verdict maps to a compliance state (compliant,
    non_compliant or quarantined), skipping error and still-pending evaluations; None when the
    asset has never had one. The AssetIndex is read newest-first in pages of
    LATEST_VERDICT_LOOKBACK until a verdict-bearing row is found or the history is exhausted."""
    query: Dict[str, Any] = {
        "IndexName": "AssetIndex",
        "KeyConditionExpression": Key("databaseId:assetId").eq(f"{database_id}:{asset_id}"),
        "ScanIndexForward": False,
        "Limit": LATEST_VERDICT_LOOKBACK,
    }
    while True:
        response = evaluation_table.query(**query)
        for row in response.get("Items", []):
            if row.get("verdict") in VERDICT_BEARING:
                return row
        if "LastEvaluatedKey" not in response:
            return None
        query["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def evaluation_still_launching(evaluation_id: Optional[str]) -> bool:
    """Whether `evaluation_id` names a `pending_pipeline` evaluation row with a pipeline rule still
    `starting` — its launches are under way, so an execution of its group whose tracking row is not
    written yet belongs to it. False for no id, no such row, or a row past its launches."""
    if not evaluation_id:
        return False
    row = get_evaluation(evaluation_id, consistent_read=True) or {}
    if row.get("recordType") is not None or row.get("status") != engine.EVALUATION_STATUS_PENDING_PIPELINE:
        return False
    return any(entry.get("status") == PIPELINE_EXECUTION_STARTING
               for entry in row.get("pipelineExecutions") or [])


def resolve_pipeline_execution(execution_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """The parent evaluation record and the pipeline rule name a workflow execution belongs to,
    or None when the execution is not a compliance execution (no row names it). A tracking row
    whose parent evaluation row is absent raises `ComplianceExecutionUnresolved`: the execution is a
    compliance execution whose completion must not be dropped."""
    rows = find_evaluation_rows_by_execution_id(execution_id)
    if not rows:
        return None
    tracking = next((r for r in rows if r.get("recordType") == PIPELINE_EXECUTION_RECORD_TYPE), None)
    if tracking is not None:
        # A sibling rule's callback may have written the parent moments ago; the consistent read
        # sees it, so a finalized evaluation is recognized before any work is done for it.
        parent = get_evaluation(tracking["parentEvaluationId"], consistent_read=True)
        rule_name = tracking.get("pipelineRuleName", "")
        if parent is None:
            raise ComplianceExecutionUnresolved(
                f"Execution {execution_id} is tracked under evaluation "
                f"{tracking['parentEvaluationId']}, which has no row")
    else:
        parent = rows[0]
        rule_name = parent.get("pipelineRuleName", "")
    if not rule_name:
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
        redelivered completion event finds the row completed and records nothing twice — it then
        re-drives the finalization from the rows as they stand, so a delivery that failed after the
        tracking write (and was retried) converges instead of leaving the evaluation pending;
      - the parent is re-read consistently after this rule's write and the evaluation's pipeline
        results are aggregated from the tracking rows, rather than from the caller's snapshot of the
        parent record — the callback whose tracking write was the last to land therefore sees every
        sibling completed, and the launch outcome `run_evaluation` records after its launches;
      - a rule still `starting` on the parent (its launch not yet attempted) keeps the evaluation
        open, and the progress write is skipped while any rule is, so a stale execution list never
        overwrites the recorded launch outcome; `run_evaluation` finalizes itself when every
        launched rule reported during the launches;
      - the progress write and the finalize write both carry a `status = pending_pipeline`
        condition, so two callbacks that each see the other completed finalize exactly once, and a
        late progress write cannot overwrite a finalized evaluation with a partial result set;
      - the asset-state row is written only while this evaluation owns it (it is the row's
        `lastEvaluationId`, or it began after the row's `lastEvaluatedAt`); a callback landing
        after a newer evaluation finalizes its evaluation row only.

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
                    "the redelivered completion finalizes whatever the rows now allow")

    current = get_evaluation(evaluation_id, consistent_read=True) or evaluation
    if current.get("status") != engine.EVALUATION_STATUS_PENDING_PIPELINE:
        logger.info(f"Evaluation {evaluation_id} was finalized by another callback")
        return _not_finalized(evaluation_id, rule_name)
    outcome = _finalize_when_complete(current, phase="pipeline_callback",
                                      execution_status=execution_status)
    return {**outcome, "ruleName": rule_name}


def apply_finalized_state_if_missing(evaluation: Dict[str, Any]) -> bool:
    """Write the asset state a finalized evaluation decided when that write never landed: the
    asset-state row still names the evaluation as its last one with `lastEvaluationStatus`
    `pending_pipeline`. The outcome is recomputed from the evaluation row's verdict and rule
    results, exactly as the finalize computed it; a row that records another evaluation, or this
    one's final status, is left alone. Returns whether the state was written."""
    if evaluation.get("status") == engine.EVALUATION_STATUS_PENDING_PIPELINE:
        return False
    evaluation_id = evaluation["evaluationId"]
    database_id = evaluation["databaseId"]
    asset_id = evaluation["assetId"]
    previous = get_compliance_record(database_id, asset_id) or {}
    if previous.get("lastEvaluationId") != evaluation_id \
            or previous.get("lastEvaluationStatus") != engine.EVALUATION_STATUS_PENDING_PIPELINE:
        return False

    logger.info(f"Evaluation {evaluation_id} of {database_id}:{asset_id} is finalized but the "
                "asset state still awaits it; the state is written from the evaluation row")
    schema_name = evaluation.get("schemaName", "")
    schema_version = (_evaluated_schema_version(evaluation)
                      if previous.get(engine.EXCEPTION_GRANTED_FIELD) else None)
    verdict = EvaluationVerdict(evaluation.get("verdict", EvaluationVerdict.error.value))
    all_results = engine.rule_results_from_json(evaluation.get("ruleResults", "[]"))
    error_rules = engine.errored_rule_names(all_results)
    exception_applies, compliance_state = _state_for_verdict(
        verdict, previous, schema_name, schema_version)
    return _write_evaluation_state(
        database_id, asset_id, SYSTEM_ACTOR, evaluation_id,
        evaluation.get("evaluatedAt") or now_iso(), now_iso(), schema_name, schema_version,
        previous, compliance_state, evaluation.get("status", ""), all_results,
        audit_details={
            "verdict": verdict.value,
            "phase": "pipeline_callback",
            **({"exceptionApplied": True} if exception_applies else {}),
            **({"errorRules": error_rules} if error_rules else {}),
        })


def _finalize_when_complete(evaluation: Dict[str, Any], phase: str,
                            execution_status: Optional[str] = None) -> Dict[str, Any]:
    """Finalize a `pending_pipeline` evaluation once every launched pipeline rule has reported:
    the verdict over the synchronous results and the tracking rows' results, the evaluation row
    (under a `status = pending_pipeline` condition, so it happens once), the asset state and the
    audit trail. While a rule is outstanding the evaluation's progress is recorded instead — unless
    a rule is still `starting`, when the launch outcome has yet to land and the execution list on
    the row is not this caller's to write. Returns `{evaluationId, finalized, verdict?,
    complianceState?}`."""
    evaluation_id = evaluation["evaluationId"]
    finished_at = now_iso()
    executions, pipeline_results, outstanding = _aggregate_tracking_rows(
        evaluation_id, evaluation.get("pipelineExecutions") or [])
    tracked_rule_names = {entry.get("ruleName") for entry in executions
                          if entry.get("status") != PIPELINE_EXECUTION_NOT_STARTED}
    base_results = [
        result for result in engine.rule_results_from_json(evaluation.get("ruleResults", "[]"))
        if result.ruleName not in tracked_rule_names
    ]
    all_results = base_results + pipeline_results
    progress = {
        "pipelineExecutions": executions,
        "ruleResults": json.dumps([r.dict() for r in all_results]),
    }

    if outstanding:
        launches_over = all(entry.get("status") != PIPELINE_EXECUTION_STARTING
                            for entry in executions)
        if launches_over:
            try:
                update_evaluation(evaluation_id, progress,
                                  condition=status_condition(engine.EVALUATION_STATUS_PENDING_PIPELINE))
            except Exception as e:
                if not is_conditional_check_failure(e):
                    raise
                logger.info(f"Evaluation {evaluation_id} was finalized by another callback")
        return {"evaluationId": evaluation_id, "finalized": False}

    database_id = evaluation["databaseId"]
    asset_id = evaluation["assetId"]
    schema_name = evaluation.get("schemaName", "")
    previous = get_compliance_record(database_id, asset_id) or {}
    # The schema version matters only to a row that carries an exception to scope it against.
    schema_version = (_evaluated_schema_version(evaluation)
                      if previous.get(engine.EXCEPTION_GRANTED_FIELD) else None)

    verdict = engine.determine_verdict(all_results)
    status = engine.evaluation_status_for(verdict)
    error_rules = engine.errored_rule_names(all_results)
    exception_applies, compliance_state = _state_for_verdict(
        verdict, previous, schema_name, schema_version)
    finalize: Dict[str, Any] = {
        **progress,
        "status": status,
        "verdict": verdict.value,
        "violations": engine.violations(all_results),
        "completedAt": finished_at,
        **_rule_error_fields(error_rules),
    }
    if exception_applies:
        finalize["exceptionApplied"] = True
    try:
        update_evaluation(evaluation_id, finalize,
                          condition=status_condition(engine.EVALUATION_STATUS_PENDING_PIPELINE))
    except Exception as e:
        if not is_conditional_check_failure(e):
            raise
        logger.info(f"Evaluation {evaluation_id} was finalized by another callback")
        return {"evaluationId": evaluation_id, "finalized": False}

    # The row's `lastEvaluatedAt` is the evaluation's start, the instant its inputs were read, so a
    # synchronous evaluation begun during the pipeline run is the newer of the two.
    _write_evaluation_state(
        database_id, asset_id, SYSTEM_ACTOR, evaluation_id,
        evaluation.get("evaluatedAt") or finished_at, finished_at, schema_name, schema_version,
        previous, compliance_state, status, all_results,
        audit_details={
            "verdict": verdict.value,
            **({"pipelineExecutionStatus": execution_status} if execution_status else {}),
            "phase": phase,
            **({"exceptionApplied": True} if exception_applies else {}),
            **({"errorRules": error_rules} if error_rules else {}),
        })
    return {
        "evaluationId": evaluation_id,
        "finalized": True,
        "verdict": verdict.value,
        "complianceState": compliance_state if compliance_state is not None
        else previous.get("complianceState", engine.STATE_UNKNOWN),
    }


def _evaluated_schema_version(evaluation: Dict[str, Any]) -> Optional[int]:
    """The `internalVersion` an evaluation ran against: the version recorded on the evaluation row,
    or — for a row written without one — the schema's current version."""
    version = engine.schema_version_number(evaluation.get("schemaVersion"))
    if version is not None:
        return version
    current = load_schema_item(evaluation.get("schemaName", "")) or {}
    return engine.schema_version_number(current.get("internalVersion"))


def _not_finalized(evaluation_id: str, rule_name: str) -> Dict[str, Any]:
    return {"evaluationId": evaluation_id, "ruleName": rule_name, "finalized": False}


def _aggregate_tracking_rows(
    evaluation_id: str, started: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[RuleResult], List[str]]:
    """Read every pipeline rule's tracking row with a consistent read and fold them into
    `(pipelineExecutions entries with their current status, the completed rules' results in
    rule order, the names of the rules still outstanding)`. A rule whose launch failed
    (`not_started`) has reported — its error result sits on the parent row — and has no tracking
    row; a rule still `starting` has no tracking row yet and is outstanding; a launched rule is
    outstanding until its tracking row is `completed`. The tracking row's execution id, when it
    has one, is the entry's."""
    executions: List[Dict[str, Any]] = []
    results: List[RuleResult] = []
    outstanding: List[str] = []
    for entry in started:
        rule_name = entry.get("ruleName", "")
        if entry.get("status") == PIPELINE_EXECUTION_NOT_STARTED:
            executions.append(dict(entry))
            continue
        row = get_evaluation(pipeline_execution_record_id(evaluation_id, rule_name),
                             consistent_read=True) or {}
        status = row.get("status") or entry.get("status") or PIPELINE_EXECUTION_PENDING
        merged = {**entry, "status": status}
        if row.get("executionId"):
            merged["executionId"] = row["executionId"]
        executions.append(merged)
        if status == PIPELINE_EXECUTION_COMPLETED:
            results.extend(engine.rule_results_from_json(row.get("ruleResults", "[]")))
        else:
            outstanding.append(rule_name)
    return executions, results, outstanding
