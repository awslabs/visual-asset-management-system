"""FMM Pipeline Callback Lambda.

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
from botocore.config import Config

from customLogging.logger import safeLogger
from models.fmm import (
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
logger = safeLogger(service_name="FMMPipelineCallback")

try:
    evaluation_table_name = os.environ["FMM_EVALUATION_STORAGE_TABLE_NAME"]
    compliance_table_name = os.environ["FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["FMM_AUDIT_STORAGE_TABLE_NAME"]
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
    """
    try:
        detail = event.get("detail", {})
        execution_status = detail.get("status", "UNKNOWN")
        execution_input = json.loads(detail.get("input", "{}"))
        execution_output = json.loads(detail.get("output", "{}"))

        evaluation_id = execution_input.get("evaluationId")
        if not evaluation_id:
            logger.info("No evaluationId in execution input, skipping")
            return {"statusCode": 200, "body": "Not a compliance execution"}

        evaluation = evaluation_table.get_item(
            Key={"evaluationId": evaluation_id}
        ).get("Item")

        if not evaluation:
            logger.warning(f"Evaluation {evaluation_id} not found")
            return {"statusCode": 404, "body": "Evaluation not found"}

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
            execution_status, execution_output, pending_rules,
            database_id, asset_id,
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
                from handlers.fmm.fmmNotifications import notify_quarantine

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


def _process_pipeline_results(
    execution_status: str,
    execution_output: Dict[str, Any],
    pending_rules: List[Dict[str, Any]],
    database_id: str,
    asset_id: str,
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
        execution_output, database_id, asset_id
    )

    if compliance_output is None:
        for pending in pending_rules:
            rule_name = pending["ruleName"]
            rule_data = pending["rule"]
            results.append(RuleResult(
                ruleName=rule_name,
                ruleType="pipeline",
                enforcement=rule_data.get("enforcement", "quarantine"),
                passed=False,
                message="No compliance output found from pipeline",
            ))
        return results

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


def _read_compliance_output(
    execution_output: Dict[str, Any],
    database_id: str,
    asset_id: str,
) -> Optional[Dict[str, Any]]:
    """Read compliance output from S3 pipeline metadata path."""
    metadata_path = execution_output.get("outputS3AssetMetadataPath")
    if not metadata_path:
        return None

    s3_key = f"{metadata_path}{asset_id}/compliance-output.json"
    if s3_key.startswith("s3://"):
        parts = s3_key.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    else:
        bucket = asset_bucket_name
        key = s3_key

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read().decode("utf-8")
        output = json.loads(body)
        if output.get("complianceOutput") is True:
            return output
        return None
    except s3_client.exceptions.NoSuchKey:
        logger.info(f"No compliance output at s3://{bucket}/{key}")
        return None
    except Exception as e:
        logger.warning(f"Failed reading compliance output: {e}")
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
