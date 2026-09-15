#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Workflow Callback Lambda.

Triggered by EventBridge when a Step Functions workflow execution completes.
Reads pipeline compliance output from S3, compares measurements against
tolerances defined in the schema, and finalizes the evaluation verdict.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from customLogging.logger import safeLogger
from models.compliance import (
    EnforcementLevel,
    EvaluationVerdict,
    PipelineCheck,
    PipelineRule,
    RuleResult,
    Tolerance,
    ToleranceOperator,
    determine_verdict,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
s3_client = boto3.client("s3", config=retry_config)
logger = safeLogger(service_name="ComplianceWorkflowCallback")

try:
    evaluation_table_name = os.environ["COMPLIANCE_EVALUATION_STORAGE_TABLE_NAME"]
    compliance_table_name = os.environ["COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["COMPLIANCE_AUDIT_STORAGE_TABLE_NAME"]
    asset_bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

evaluation_table = dynamodb.Table(evaluation_table_name)
compliance_table = dynamodb.Table(compliance_table_name)
audit_table = dynamodb.Table(audit_table_name)


def lambda_handler(event, context):
    """Handle Step Functions execution completion event from EventBridge.

    Event structure (EventBridge detail):
    {
        "executionArn": "arn:...",
        "stateMachineArn": "arn:...",
        "status": "SUCCEEDED" | "FAILED" | "TIMED_OUT" | "ABORTED",
        "input": "{...}",
        "output": "{...}"
    }

    Correlates the execution to an Compliance evaluation via the ExecutionArnIndex GSI
    on the evaluation table (populated when the evaluation engine invokes
    workflow executions via Lambda cross-call).
    """
    try:
        detail = event.get("detail", {})
        execution_status = detail.get("status", "UNKNOWN")
        execution_input = json.loads(detail.get("input") or "{}")
        execution_output = json.loads(detail.get("output") or "{}")
        execution_arn = detail.get("executionArn", "")
        start_date = detail.get("startDate")
        stop_date = detail.get("stopDate")

        evaluation = _find_evaluation_by_execution(execution_arn)
        if not evaluation:
            logger.info(
                f"No Compliance evaluation found for execution {execution_arn}, skipping"
            )
            return {"statusCode": 200, "body": "Not a compliance execution"}

        evaluation_id = evaluation["evaluationId"]

        if evaluation.get("status") != "pending_pipeline":
            logger.info(
                f"Evaluation {evaluation_id} is not pending_pipeline, "
                f"current status: {evaluation.get('status')}"
            )
            return {"statusCode": 200, "body": "Already processed"}

        database_id = evaluation["databaseId"]
        asset_id = evaluation["assetId"]
        schema_name = evaluation["schemaName"]

        existing_results = json.loads(
            evaluation.get("ruleResults", "[]")
        )
        pending_rules = json.loads(
            evaluation.get("pipelineRulesPending", "[]")
        )

        pipeline_results = _process_pipeline_results(
            execution_status, execution_input, execution_output,
            pending_rules, database_id, asset_id,
            start_date, stop_date,
        )

        all_results = [
            RuleResult(**r) for r in existing_results
        ] + pipeline_results

        verdict = determine_verdict(all_results)
        compliance_state = _verdict_to_state(verdict)
        now = datetime.now(timezone.utc).isoformat()

        evaluation_table.update_item(
            Key={"evaluationId": evaluation_id},
            UpdateExpression=(
                "SET #status = :status, "
                "verdict = :verdict, "
                "ruleResults = :results, "
                "pipelineRulesPending = :none, "
                "completedAt = :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "completed",
                ":verdict": verdict.value,
                ":results": json.dumps(
                    [r.dict() for r in all_results]
                ),
                ":none": None,
                ":now": now,
            },
        )

        previous_record = compliance_table.get_item(
            Key={"databaseId": database_id, "assetId": asset_id}
        ).get("Item", {})
        previous_state = previous_record.get(
            "complianceState", "pending_evaluation"
        )

        compliance_table.update_item(
            Key={"databaseId": database_id, "assetId": asset_id},
            UpdateExpression=(
                "SET complianceState = :state, updatedAt = :now"
            ),
            ExpressionAttributeValues={
                ":state": compliance_state,
                ":now": now,
            },
        )

        _write_audit(
            database_id, asset_id,
            event_type="compliance_check",
            actor="system",
            details={
                "evaluationId": evaluation_id,
                "schemaName": schema_name,
                "verdict": verdict.value,
                "pipelineExecutionStatus": execution_status,
                "phase": "pipeline_callback",
            },
            previous_state=previous_state,
            new_state=compliance_state,
        )

        if previous_state == "quarantined" and compliance_state == "compliant":
            _write_audit(
                database_id, asset_id,
                event_type="quarantine_released",
                actor="system",
                details={
                    "evaluationId": evaluation_id,
                    "reason": "Pipeline re-evaluation passed — auto-released",
                },
                previous_state="quarantined",
                new_state="compliant",
            )

        if verdict == EvaluationVerdict.quarantined:
            try:
                from handlers.compliance.complianceNotifications import notify_quarantine

                failed_rules = [
                    r.ruleName for r in all_results if not r.passed
                ]
                notify_quarantine(
                    database_id, asset_id, schema_name, failed_rules
                )
            except Exception as e:
                logger.exception(
                    f"Failed sending quarantine notification: {e}"
                )

        logger.info(
            f"Evaluation {evaluation_id} completed: "
            f"verdict={verdict.value}, state={compliance_state}"
        )
        return {"statusCode": 200, "body": json.dumps({
            "evaluationId": evaluation_id,
            "verdict": verdict.value,
        })}

    except Exception as e:
        logger.exception("Error in pipeline callback")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def _find_evaluation_by_execution(
    execution_arn: str,
) -> Optional[Dict[str, Any]]:
    """Look up an Compliance evaluation record by the workflow execution ARN.

    The SFN execution name (last segment of the ARN) equals the VAMS
    executionId stored on the evaluation record. Primary lookup: query
    the ExecutionArnIndex GSI (covers legacy records that stored the full
    ARN). Fallback: scan pending evaluations matching the executionId
    extracted from the ARN.
    """
    if not execution_arn:
        return None

    # Extract execution name (= VAMS executionId) from the ARN
    execution_name = execution_arn.rsplit(":", 1)[-1] if ":" in execution_arn else ""

    # Primary: GSI lookup by full ARN (legacy records)
    try:
        response = evaluation_table.query(
            IndexName="ExecutionArnIndex",
            KeyConditionExpression=Key("executionArn").eq(execution_arn),
            Limit=1,
        )
        items = response.get("Items", [])
        if items:
            return items[0]
    except Exception as e:
        logger.exception(
            f"Failed querying ExecutionArnIndex for {execution_arn}: {e}"
        )

    # Fallback: scan pending evaluations matching executionId
    if not execution_name:
        return None
    try:
        response = evaluation_table.scan(
            FilterExpression=(
                Attr("status").eq("pending_pipeline")
                & Attr("executionId").eq(execution_name)
            ),
            Limit=200,
        )
        items = response.get("Items", [])
        if items:
            return items[0]
    except Exception as e:
        logger.exception(
            f"Failed scanning for pending evaluations: {e}"
        )

    # Final fallback: check executionMappings list
    try:
        response = evaluation_table.scan(
            FilterExpression=Attr("status").eq("pending_pipeline"),
            Limit=200,
        )
        for item in response.get("Items", []):
            mappings = item.get("executionMappings", [])
            for m in mappings:
                if m.get("executionId") == execution_name:
                    return item
    except Exception as e:
        logger.exception(
            f"Failed scanning executionMappings: {e}"
        )
    return None


def _process_pipeline_results(
    execution_status: str,
    execution_input: Dict[str, Any],
    execution_output: Dict[str, Any],
    pending_rules: List[Dict[str, Any]],
    database_id: str,
    asset_id: str,
    start_date: Optional[Any] = None,
    stop_date: Optional[Any] = None,
) -> List[RuleResult]:
    """Process pipeline execution results against pending rules."""
    results = []

    if execution_status != "SUCCEEDED":
        for pending in pending_rules:
            rule_name = pending["ruleName"]
            rule_data = pending["rule"]
            results.append(RuleResult(
                ruleName=rule_name,
                ruleType="pipeline",
                enforcement=rule_data.get("enforcement", "quarantine"),
                passed=False,
                message=f"Pipeline execution {execution_status}",
            ))
        return results

    compliance_output = _read_compliance_output(
        execution_input, execution_output, database_id, asset_id
    )

    if compliance_output is None:
        compliance_output = _compute_default_metrics(
            execution_status, start_date, stop_date
        )

    if compliance_output.get("status") == "error":
        errors = compliance_output.get("errors", [])
        for pending in pending_rules:
            rule_name = pending["ruleName"]
            rule_data = pending["rule"]
            results.append(RuleResult(
                ruleName=rule_name,
                ruleType="pipeline",
                enforcement=rule_data.get("enforcement", "quarantine"),
                passed=False,
                message=f"Pipeline reported error: {errors}",
            ))
        return results

    measurements = compliance_output.get("measurements", {})

    for pending in pending_rules:
        rule_name = pending["ruleName"]
        rule_data = pending["rule"]
        rule = PipelineRule(**rule_data)

        rule_passed = True
        messages = []
        measured = {}
        expected = {}

        for check in rule.checks:
            value = measurements.get(check.outputField)
            measured[check.outputField] = value

            if value is None:
                rule_passed = False
                messages.append(
                    f"{check.name}: output field '{check.outputField}' missing"
                )
                continue

            check_passed, msg = _compare_tolerance(
                check.name, value, check.tolerance
            )
            if not check_passed:
                rule_passed = False
                messages.append(msg)

            expected[check.outputField] = {
                "operator": check.tolerance.operator.value,
                "value": check.tolerance.value,
                "min": check.tolerance.min,
                "max": check.tolerance.max,
            }

        results.append(RuleResult(
            ruleName=rule_name,
            ruleType="pipeline",
            enforcement=rule.enforcement.value,
            passed=rule_passed,
            message="; ".join(messages) if messages else None,
            measured=measured,
            expected=expected,
        ))

    return results


def _compute_default_metrics(
    execution_status: str,
    start_date: Optional[Any],
    stop_date: Optional[Any],
) -> Dict[str, Any]:
    """Compute generic compliance measurements from execution results.

    Provides default metrics for pipelines that do not produce their own
    compliance-output.json. Available measurements:
      - execution_success: 1.0 if pipeline succeeded, 0.0 otherwise
      - processing_duration_seconds: wall-clock execution time
    """
    measurements: Dict[str, Any] = {}

    measurements["execution_success"] = (
        1.0 if execution_status == "SUCCEEDED" else 0.0
    )

    if start_date is not None and stop_date is not None:
        try:
            duration = (float(stop_date) - float(start_date)) / 1000.0
            measurements["processing_duration_seconds"] = round(duration, 2)
        except (ValueError, TypeError):
            pass

    return {
        "complianceOutput": True,
        "status": "success",
        "measurements": measurements,
        "errors": [],
    }


def _read_compliance_output(
    execution_input: Dict[str, Any],
    execution_output: Dict[str, Any],
    database_id: str,
    asset_id: str,
) -> Optional[Dict[str, Any]]:
    """Read compliance-output.json from the pipeline's metadata output path.

    Strategy:
    1. Try the execution output's process-output body which contains
       metadataPathKey (the relative path within the asset bucket).
    2. Fall back to listing objects under the asset prefix looking for
       compliance-output.json from this execution.
    """
    bucket = execution_input.get("bucketAsset", asset_bucket_name)

    metadata_path_key = _extract_metadata_path_key(execution_output)
    if metadata_path_key:
        key = f"{metadata_path_key}compliance-output.json"
        output = _try_read_s3_json(bucket, key)
        if output and output.get("complianceOutput") is True:
            return output

    evaluation_id = execution_input.get("evaluationId", "")
    if evaluation_id:
        key = f"compliance/{database_id}/{asset_id}/{evaluation_id}/compliance-output.json"
        output = _try_read_s3_json(bucket, key)
        if output and output.get("complianceOutput") is True:
            return output

    return None


def _extract_metadata_path_key(execution_output: Dict[str, Any]) -> Optional[str]:
    """Extract the metadata path key from the workflow execution output.

    The process-output Lambda (last step in workflow) receives metadataPathKey
    in its body. The execution output may have nested state output structures.
    """
    if isinstance(execution_output, dict):
        body = execution_output.get("body", {})
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                body = {}
        if isinstance(body, dict):
            path_key = body.get("metadataPathKey")
            if path_key:
                return path_key

        for key, val in execution_output.items():
            if key.startswith("process-outputs") and isinstance(val, dict):
                nested_output = val.get("output", val)
                if isinstance(nested_output, dict):
                    nested_body = nested_output.get("body", {})
                    if isinstance(nested_body, str):
                        try:
                            nested_body = json.loads(nested_body)
                        except (json.JSONDecodeError, TypeError):
                            continue
                    if isinstance(nested_body, dict):
                        path_key = nested_body.get("metadataPathKey")
                        if path_key:
                            return path_key
    return None


def _try_read_s3_json(
    bucket: str, key: str
) -> Optional[Dict[str, Any]]:
    """Attempt to read and parse a JSON file from S3."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read().decode("utf-8")
        return json.loads(body)
    except s3_client.exceptions.NoSuchKey:
        logger.info(f"No object at s3://{bucket}/{key}")
        return None
    except Exception as e:
        logger.warning(f"Failed reading s3://{bucket}/{key}: {e}")
        return None


def _compare_tolerance(
    check_name: str, value: float, tolerance: Tolerance
) -> Tuple[bool, str]:
    """Compare a measured value against a tolerance. Returns (passed, message)."""
    try:
        measured = float(value)
    except (ValueError, TypeError):
        return False, f"{check_name}: value '{value}' is not numeric"

    op = tolerance.operator

    if op == ToleranceOperator.lte:
        if measured <= tolerance.value:
            return True, ""
        return (
            False,
            f"{check_name}: {measured} > {tolerance.value} (max)",
        )

    if op == ToleranceOperator.gte:
        if measured >= tolerance.value:
            return True, ""
        return (
            False,
            f"{check_name}: {measured} < {tolerance.value} (min)",
        )

    if op == ToleranceOperator.eq:
        epsilon = tolerance.epsilon or 0.001
        if abs(measured - tolerance.value) <= epsilon:
            return True, ""
        return (
            False,
            f"{check_name}: {measured} != {tolerance.value} (epsilon={epsilon})",
        )

    if op == ToleranceOperator.between:
        if tolerance.min <= measured <= tolerance.max:
            return True, ""
        return (
            False,
            f"{check_name}: {measured} not in [{tolerance.min}, {tolerance.max}]",
        )

    return False, f"{check_name}: unknown operator '{op}'"


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
