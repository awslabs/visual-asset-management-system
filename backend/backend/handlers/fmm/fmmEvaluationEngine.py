"""FMM Evaluation Engine.

Core evaluation logic for vams-rules-v1 compliance schemas.
Evaluates metadata rules, relationship rules, and initiates pipeline rules.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from customLogging.logger import safeLogger
from models.fmm import (
    ComplianceRule,
    EnforcementLevel,
    EvaluationVerdict,
    MetadataCheck,
    MetadataRule,
    PipelineRule,
    RelationshipCheck,
    RelationshipRule,
    RuleResult,
    VamsRulesV1Schema,
    determine_verdict,
    resolve_schema_inheritance,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
dynamodb_client = boto3.client("dynamodb", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)
s3_client = boto3.client("s3", config=retry_config)
logger = safeLogger(service_name="FMMEvaluationEngine")

try:
    schema_table_name = os.environ["FMM_SCHEMA_STORAGE_TABLE_NAME"]
    compliance_table_name = os.environ["FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME"]
    evaluation_table_name = os.environ["FMM_EVALUATION_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["FMM_AUDIT_STORAGE_TABLE_NAME"]
    asset_metadata_table_name = os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"]
    metadata_schema_table_name = os.environ["METADATA_SCHEMA_STORAGE_TABLE_V2_NAME"]
    asset_links_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    workflow_table_name = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    s3_auxiliary_bucket = os.environ["S3_ASSETAUXILIARY_STORAGE_BUCKET"]
    execute_workflow_function_name = os.environ["EXECUTE_WORKFLOW_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

schema_table = dynamodb.Table(schema_table_name)
compliance_table = dynamodb.Table(compliance_table_name)
evaluation_table = dynamodb.Table(evaluation_table_name)
audit_table = dynamodb.Table(audit_table_name)
asset_metadata_table = dynamodb.Table(asset_metadata_table_name)
metadata_schema_table = dynamodb.Table(metadata_schema_table_name)
asset_table = dynamodb.Table(asset_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)
asset_links_table = dynamodb.Table(asset_links_table_name)
workflow_table = dynamodb.Table(workflow_table_name)


def evaluate_asset(
    database_id: str,
    asset_id: str,
    schema_name: str,
    actor: str = "system",
) -> Dict[str, Any]:
    """Run compliance evaluation for an asset against a schema.

    Returns the evaluation result dict with verdict, rule results, and state.
    """
    now = datetime.now(timezone.utc).isoformat()
    evaluation_id = str(uuid.uuid4())

    schema_body = _load_schema_body(schema_name)
    if schema_body is None:
        return _record_error(
            evaluation_id, database_id, asset_id, schema_name,
            f"Schema '{schema_name}' not found", now, actor,
        )

    if schema_body.get("schemaFormat") != "vams-rules-v1":
        return _record_error(
            evaluation_id, database_id, asset_id, schema_name,
            "Schema is not vams-rules-v1 format; skipping evaluation",
            now, actor,
        )

    try:
        parsed_schema = VamsRulesV1Schema(**schema_body)
    except (ValueError, TypeError) as e:
        return _record_error(
            evaluation_id, database_id, asset_id, schema_name,
            f"Invalid schema body: {e}", now, actor,
        )

    resolved_rules = _resolve_with_inheritance(parsed_schema, schema_body)
    typed_rules = _parse_resolved_rules(resolved_rules)

    rule_results: List[RuleResult] = []
    pipeline_rules: List[Tuple[str, PipelineRule]] = []

    for rule_name, rule in typed_rules.items():
        if isinstance(rule, MetadataRule):
            result = _evaluate_metadata_rule(
                rule_name, rule, database_id, asset_id
            )
            rule_results.append(result)
        elif isinstance(rule, RelationshipRule):
            result = _evaluate_relationship_rule(
                rule_name, rule, database_id, asset_id
            )
            rule_results.append(result)
        elif isinstance(rule, PipelineRule):
            pipeline_rules.append((rule_name, rule))

    has_pipeline_rules = len(pipeline_rules) > 0

    if has_pipeline_rules:
        verdict = EvaluationVerdict.pending_pipeline
    else:
        verdict = determine_verdict(rule_results)

    compliance_state = _verdict_to_state(verdict)

    violations = [
        r.message for r in rule_results
        if not r.passed and r.message
    ]

    evaluation_record = {
        "evaluationId": evaluation_id,
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": schema_name,
        "evaluatedAt": now,
        "timestamp": now,
        "status": "pending_pipeline" if has_pipeline_rules else "completed",
        "result": verdict.value,
        "verdict": verdict.value,
        "violations": violations,
        "ruleResults": json.dumps([r.dict() for r in rule_results]),
        "pipelineRulesPending": json.dumps(
            [{"ruleName": n, "rule": r.dict()} for n, r in pipeline_rules]
        ) if has_pipeline_rules else None,
        "actor": actor,
    }
    evaluation_table.put_item(Item=evaluation_record)

    previous_state = _get_current_compliance_state(database_id, asset_id)

    compliance_table.update_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        UpdateExpression=(
            "SET complianceState = :state, "
            "lastEvaluationId = :evalId, "
            "lastEvaluatedAt = :now, "
            "schemaName = :schema, "
            "updatedAt = :now"
        ),
        ExpressionAttributeValues={
            ":state": compliance_state,
            ":evalId": evaluation_id,
            ":now": now,
            ":schema": schema_name,
        },
    )

    if has_pipeline_rules:
        _invoke_pipeline_rules(
            pipeline_rules, evaluation_id, database_id, asset_id
        )

    _write_audit(
        database_id, asset_id,
        event_type="compliance_check",
        actor=actor,
        details={
            "evaluationId": evaluation_id,
            "schemaName": schema_name,
            "verdict": verdict.value,
            "ruleResultCount": len(rule_results),
            "pipelineRulesPending": len(pipeline_rules),
        },
        new_state=compliance_state,
        previous_state=previous_state,
    )

    if (
        previous_state == "quarantined"
        and compliance_state == "compliant"
    ):
        _write_audit(
            database_id, asset_id,
            event_type="quarantine_released",
            actor=actor,
            details={
                "evaluationId": evaluation_id,
                "reason": "Re-evaluation passed — auto-released",
            },
            previous_state="quarantined",
            new_state="compliant",
        )

    if verdict == EvaluationVerdict.quarantined:
        try:
            from handlers.fmm.fmmNotifications import notify_quarantine

            failed_rules = [
                r.ruleName for r in rule_results if not r.passed
            ]
            notify_quarantine(
                database_id, asset_id, schema_name, failed_rules
            )
        except Exception as e:
            logger.exception(f"Failed sending quarantine notification: {e}")

    return {
        "evaluationId": evaluation_id,
        "verdict": verdict.value,
        "complianceState": compliance_state,
        "ruleResults": [r.dict() for r in rule_results],
        "pipelineRulesPending": len(pipeline_rules),
    }


def _load_schema_body(schema_name: str) -> Optional[Dict[str, Any]]:
    """Load the latest version of a schema by name."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None

    body = items[0].get("schemaBody", "{}")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None
    return body


def _resolve_with_inheritance(
    parsed_schema: VamsRulesV1Schema,
    schema_body: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve schema inheritance chain."""
    if not parsed_schema.extends:
        return schema_body.get("rules", {})

    parent_body = _load_schema_body(parsed_schema.extends)
    if parent_body is None:
        logger.warning(
            f"Parent schema '{parsed_schema.extends}' not found, "
            "using child rules only"
        )
        return schema_body.get("rules", {})

    if parent_body.get("schemaFormat") == "vams-rules-v1":
        parent_parsed = VamsRulesV1Schema(**parent_body)
        parent_resolved = _resolve_with_inheritance(parent_parsed, parent_body)
        parent_body_for_merge = {"rules": parent_resolved}
    else:
        parent_body_for_merge = parent_body

    return resolve_schema_inheritance(schema_body, parent_body_for_merge)


def _parse_resolved_rules(
    rules: Dict[str, Any],
) -> Dict[str, ComplianceRule]:
    """Parse raw rule dicts into typed models."""
    parsed = {}
    for rule_name, rule_def in rules.items():
        if not isinstance(rule_def, dict):
            continue
        rule_type = rule_def.get("ruleType")
        try:
            if rule_type == "pipeline":
                parsed[rule_name] = PipelineRule(**rule_def)
            elif rule_type == "metadata":
                parsed[rule_name] = MetadataRule(**rule_def)
            elif rule_type == "relationship":
                parsed[rule_name] = RelationshipRule(**rule_def)
            else:
                logger.warning(f"Unknown rule type '{rule_type}' in rule '{rule_name}'")
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse rule '{rule_name}': {e}")
    return parsed


def _evaluate_metadata_rule(
    rule_name: str,
    rule: MetadataRule,
    database_id: str,
    asset_id: str,
) -> RuleResult:
    """Evaluate a metadata rule against an asset's metadata."""
    asset_metadata = _get_asset_metadata(database_id, asset_id)
    schema_fields = _get_metadata_schema_fields(
        rule.metadataSchemaRef.databaseId,
        rule.metadataSchemaRef.schemaName,
    )

    all_passed = True
    messages = []

    for check in rule.checks:
        passed, msg = _run_metadata_check(
            check, asset_metadata, schema_fields
        )
        if not passed:
            all_passed = False
            messages.append(msg)

    return RuleResult(
        ruleName=rule_name,
        ruleType="metadata",
        enforcement=rule.enforcement.value,
        passed=all_passed,
        message="; ".join(messages) if messages else None,
        measured={"metadataKeys": list(asset_metadata.keys())},
        expected={
            "schemaRef": f"{rule.metadataSchemaRef.databaseId}/{rule.metadataSchemaRef.schemaName}"
        },
    )


def _run_metadata_check(
    check: MetadataCheck,
    asset_metadata: Dict[str, Any],
    schema_fields: Optional[List[Dict[str, Any]]],
) -> Tuple[bool, str]:
    """Run a single metadata check. Returns (passed, message)."""
    if check.validateRequired and schema_fields is not None:
        required_from_schema = [
            f["field"] for f in schema_fields if f.get("required")
        ]
        missing = [f for f in required_from_schema if f not in asset_metadata]
        if missing:
            return False, f"Missing required schema fields: {missing}"

    if check.additionalRequiredFields:
        missing = [
            f for f in check.additionalRequiredFields if f not in asset_metadata
        ]
        if missing:
            return False, f"Missing additional required fields: {missing}"

    if check.validateTypes and schema_fields is not None:
        type_errors = []
        for field_def in schema_fields:
            field_name = field_def.get("field")
            expected_type = field_def.get("dataType")
            if field_name in asset_metadata and expected_type:
                actual_value = asset_metadata[field_name]
                if not _check_type_match(actual_value, expected_type):
                    type_errors.append(
                        f"{field_name}: expected {expected_type}"
                    )
        if type_errors:
            return False, f"Type mismatches: {type_errors}"

    return True, ""


def _check_type_match(value: Any, expected_type: str) -> bool:
    """Check if a metadata value matches the expected type."""
    type_map = {
        "STRING": str,
        "NUMBER": (int, float),
        "BOOLEAN": bool,
    }
    expected = type_map.get(expected_type.upper())
    if expected is None:
        return True
    return isinstance(value, expected)


def _evaluate_relationship_rule(
    rule_name: str,
    rule: RelationshipRule,
    database_id: str,
    asset_id: str,
) -> RuleResult:
    """Evaluate a relationship rule against an asset's links."""
    all_passed = True
    messages = []
    measured = {}

    for check in rule.checks:
        passed, msg, count = _run_relationship_check(
            check, database_id, asset_id
        )
        measured[check.name] = count
        if not passed:
            all_passed = False
            messages.append(msg)

    return RuleResult(
        ruleName=rule_name,
        ruleType="relationship",
        enforcement=rule.enforcement.value,
        passed=all_passed,
        message="; ".join(messages) if messages else None,
        measured=measured,
    )


def _run_relationship_check(
    check: RelationshipCheck,
    database_id: str,
    asset_id: str,
) -> Tuple[bool, str, int]:
    """Run a single relationship check. Returns (passed, message, count)."""
    asset_key = f"{database_id}:{asset_id}"

    if check.direction == "parents":
        gsi_name = "toAssetGSI"
        key_field = "toAssetDatabaseId:toAssetId"
    elif check.direction == "children":
        gsi_name = "fromAssetGSI"
        key_field = "fromAssetDatabaseId:fromAssetId"
    else:
        count_from = _count_links(
            "fromAssetGSI", "fromAssetDatabaseId:fromAssetId",
            asset_key, check.relationshipType,
        )
        count_to = _count_links(
            "toAssetGSI", "toAssetDatabaseId:toAssetId",
            asset_key, check.relationshipType,
        )
        count = count_from + count_to
        return _check_count(check, count)

    count = _count_links(gsi_name, key_field, asset_key, check.relationshipType)
    return _check_count(check, count)


def _count_links(
    gsi_name: str,
    key_field: str,
    asset_key: str,
    relationship_type: str,
) -> int:
    """Count asset links matching criteria."""
    from boto3.dynamodb.conditions import Attr

    response = asset_links_table.query(
        IndexName=gsi_name,
        KeyConditionExpression=Key(key_field).eq(asset_key),
        FilterExpression=Attr("relationshipType").eq(relationship_type),
        Select="COUNT",
    )
    return response.get("Count", 0)


def _check_count(
    check: RelationshipCheck, count: int
) -> Tuple[bool, str, int]:
    """Check if a link count satisfies min/max requirements."""
    if check.minCount is not None and count < check.minCount:
        return (
            False,
            f"{check.name}: found {count} links, minimum is {check.minCount}",
            count,
        )
    if check.maxCount is not None and count > check.maxCount:
        return (
            False,
            f"{check.name}: found {count} links, maximum is {check.maxCount}",
            count,
        )
    return True, "", count


def _get_asset_metadata(database_id: str, asset_id: str) -> Dict[str, Any]:
    """Fetch asset metadata as a flat key-value dict."""
    composite_key = f"{database_id}:{asset_id}:/"

    response = dynamodb_client.query(
        TableName=asset_metadata_table_name,
        IndexName="DatabaseIdAssetIdFilePathIndex",
        KeyConditionExpression="#pk = :pkValue",
        ExpressionAttributeNames={"#pk": "databaseId:assetId:filePath"},
        ExpressionAttributeValues={":pkValue": {"S": composite_key}},
    )

    metadata = {}
    for item in response.get("Items", []):
        key = item.get("metadataKey", {}).get("S")
        value_raw = item.get("metadataValue", {}).get("S")
        value_type = item.get("metadataValueType", {}).get("S", "STRING")
        if key:
            metadata[key] = _coerce_metadata_value(value_raw, value_type)

    return metadata


def _coerce_metadata_value(value: Optional[str], value_type: str) -> Any:
    """Coerce a metadata value string to its typed form."""
    if value is None:
        return None
    if value_type == "NUMBER":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    if value_type == "BOOLEAN":
        return value.lower() in ("true", "1", "yes")
    return value


def _get_metadata_schema_fields(
    database_id: str, schema_name: str
) -> Optional[List[Dict[str, Any]]]:
    """Fetch field definitions from a VAMS metadata schema."""
    from boto3.dynamodb.conditions import Attr

    response = metadata_schema_table.scan(
        FilterExpression=(
            Attr("databaseId").eq(database_id)
            & Attr("schemaName").eq(schema_name)
        ),
    )
    items = response.get("Items", [])
    if not items:
        response = metadata_schema_table.scan(
            FilterExpression=(
                Attr("databaseId").eq("GLOBAL")
                & Attr("schemaName").eq(schema_name)
            ),
        )
        items = response.get("Items", [])

    if not items:
        return None

    fields_raw = items[0].get("fields", "[]")
    if isinstance(fields_raw, str):
        try:
            fields_raw = json.loads(fields_raw)
        except (json.JSONDecodeError, TypeError):
            return None

    if isinstance(fields_raw, dict) and "fields" in fields_raw:
        fields_raw = fields_raw["fields"]

    if not isinstance(fields_raw, list):
        return None

    return [
        {
            "field": f.get("metadataFieldKeyName") or f.get("field", ""),
            "required": f.get("required", False),
            "type": f.get("metadataFieldValueType") or f.get("type", "string"),
        }
        for f in fields_raw
        if isinstance(f, dict)
    ]


def _invoke_pipeline_rules(
    pipeline_rules: List[Tuple[str, PipelineRule]],
    evaluation_id: str,
    database_id: str,
    asset_id: str,
) -> None:
    """Invoke workflow executions for pipeline rules via the V2 execute API.

    Uses a Lambda cross-call to the executeWorkflow handler which handles
    all pre-flight setup (manifest writing, record creation, proper SFN input
    construction). The evaluationId is stored on the FMM evaluation record
    along with the returned executionId so the callback can correlate
    completions.
    """
    asset_info = _get_asset_info(database_id, asset_id)
    if not asset_info:
        logger.error(
            f"Cannot invoke pipeline rules: asset {database_id}/{asset_id} "
            "not found or missing bucket info"
        )
        return

    for rule_name, rule in pipeline_rules:
        workflow_db_id = rule.pipelineRef.databaseId
        workflow_id = rule.pipelineRef.workflowId

        fmm_context = {
            "evaluationId": evaluation_id,
            "ruleName": rule_name,
            "checks": [c.dict() for c in rule.checks],
            "inputParameters": rule.inputParameters or {},
        }

        asset_file_key = asset_info["assetFileKey"]
        asset_prefix = asset_info["assetPrefix"]
        relative_key = asset_file_key
        if relative_key.startswith(asset_prefix):
            relative_key = relative_key[len(asset_prefix):]
        if not relative_key.startswith("/"):
            relative_key = "/" + relative_key

        execute_body: Dict[str, Any] = {
            "inputFiles": [{
                "databaseId": database_id,
                "assetId": asset_id,
                "relativeFileKey": relative_key,
            }],
        }
        if rule.pipelineRef.templateId:
            execute_body["pipelineExecutionParameters"] = {
                rule.pipelineRef.workflowId: {
                    "templateId": rule.pipelineRef.templateId,
                },
            }

        cross_call_event = {
            "lambdaCrossCall": {
                "tokens": ["SYSTEM_USER"],
                "sub": "SYSTEM_USER",
                "roles": ["admin"],
            },
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": f"/workflows/{workflow_db_id}/{workflow_id}/execute",
                }
            },
            "pathParameters": {
                "workflowDatabaseId": workflow_db_id,
                "workflowId": workflow_id,
            },
            "body": json.dumps(execute_body),
        }

        try:
            response = lambda_client.invoke(
                FunctionName=execute_workflow_function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(cross_call_event),
            )
            payload = json.loads(response["Payload"].read())
            status_code = payload.get("statusCode", 500)

            if status_code == 200:
                body = json.loads(payload.get("body", "{}"))
                message = body.get("message", {})
                if isinstance(message, str):
                    message = {}
                execution_id = message.get("executionId", "")
                logger.info(
                    f"Started workflow execution for rule '{rule_name}': "
                    f"{execution_id}"
                )
                _record_execution_mapping(
                    evaluation_id, rule_name, execution_id
                )
            else:
                logger.error(
                    f"Execute workflow failed for rule '{rule_name}': "
                    f"status={status_code}, body={payload.get('body', '')}"
                )
        except Exception as e:
            logger.exception(
                f"Failed to invoke workflow for rule '{rule_name}': {e}"
            )


def _record_execution_mapping(
    evaluation_id: str,
    rule_name: str,
    execution_id: str,
) -> None:
    """Store the mapping between FMM evaluation and workflow execution.

    Stores the executionId (which is the SFN execution name) so the
    callback can correlate completions by extracting the execution name
    from the EventBridge executionArn.
    """
    try:
        mapping_entry = {
            "ruleName": rule_name,
            "executionId": execution_id,
        }
        evaluation_table.update_item(
            Key={"evaluationId": evaluation_id},
            UpdateExpression=(
                "SET executionId = :eid, "
                "pipelineRuleName = :rn, "
                "executionMappings = list_append("
                "if_not_exists(executionMappings, :empty), :mapping)"
            ),
            ExpressionAttributeValues={
                ":eid": execution_id,
                ":rn": rule_name,
                ":empty": [],
                ":mapping": [mapping_entry],
            },
        )
    except Exception as e:
        logger.exception(
            f"Failed to record execution mapping for {evaluation_id}: {e}"
        )


def _get_asset_info(database_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """Look up asset bucket and first file key for workflow execution."""
    response = asset_table.query(
        KeyConditionExpression=(
            Key("databaseId").eq(database_id)
            & Key("assetId").eq(asset_id)
        ),
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None

    asset = items[0]
    asset_location = asset.get("assetLocation", {})
    asset_prefix = asset_location.get("Key", "")
    bucket_id = asset.get("bucketId")

    if not bucket_id or not asset_prefix:
        return None

    bucket_response = s3_asset_buckets_table.query(
        KeyConditionExpression=Key("bucketId").eq(bucket_id),
        Limit=1,
    )
    bucket_items = bucket_response.get("Items", [])
    if not bucket_items:
        return None

    bucket_name = bucket_items[0].get("bucketName")
    if not bucket_name:
        return None

    prefix = asset_prefix if asset_prefix.endswith("/") else asset_prefix + "/"
    s3_resp = s3_client.list_objects_v2(
        Bucket=bucket_name, Prefix=prefix, MaxKeys=20,
    )
    first_file_key = None
    for obj in s3_resp.get("Contents", []):
        key = obj.get("Key", "")
        if key and not key.endswith("/"):
            first_file_key = key
            break

    if not first_file_key:
        return None

    return {
        "bucketName": bucket_name,
        "assetPrefix": prefix,
        "assetFileKey": first_file_key,
    }



def _get_current_compliance_state(
    database_id: str, asset_id: str
) -> Optional[str]:
    """Fetch the current compliance state for an asset."""
    response = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = response.get("Item")
    if item:
        return item.get("complianceState")
    return None


def _verdict_to_state(verdict: EvaluationVerdict) -> str:
    """Map evaluation verdict to compliance state string."""
    mapping = {
        EvaluationVerdict.compliant: "compliant",
        EvaluationVerdict.non_compliant: "non_compliant",
        EvaluationVerdict.quarantined: "quarantined",
        EvaluationVerdict.pending_pipeline: "pending_evaluation",
        EvaluationVerdict.error: "unknown",
    }
    return mapping.get(verdict, "unknown")


def _record_error(
    evaluation_id: str,
    database_id: str,
    asset_id: str,
    schema_name: str,
    error_msg: str,
    now: str,
    actor: str,
) -> Dict[str, Any]:
    """Record an evaluation error."""
    evaluation_table.put_item(
        Item={
            "evaluationId": evaluation_id,
            "databaseId": database_id,
            "assetId": asset_id,
            "schemaName": schema_name,
            "timestamp": now,
            "status": "error",
            "verdict": "error",
            "errorMessage": error_msg,
            "actor": actor,
        }
    )
    return {
        "evaluationId": evaluation_id,
        "verdict": "error",
        "complianceState": "unknown",
        "error": error_msg,
    }


def _write_audit(
    database_id: str,
    asset_id: str,
    event_type: str,
    actor: str,
    details: Optional[Dict[str, Any]] = None,
    previous_state: Optional[str] = None,
    new_state: Optional[str] = None,
):
    """Write an audit log entry."""
    now = datetime.now(timezone.utc).isoformat()
    audit_table.put_item(
        Item={
            "entryId": str(uuid.uuid4()),
            "databaseId:assetId": f"{database_id}:{asset_id}",
            "timestamp": now,
            "eventType": event_type,
            "databaseId": database_id,
            "assetId": asset_id,
            "actor": actor,
            "details": json.dumps(details or {}),
            "previousState": previous_state,
            "newState": new_state,
        }
    )
