#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Workflow Callback handler (EventBridge-invoked).

Consumes the `workflow.execution.completed` event the workflow end-state and error-handler
lambdas put on the orchestration bus (`common.workflows.executionRecords.
workflow_execution_completed_event`). The detail carries `executionId`, `workflowDatabaseId`,
`workflowId`, `status`, `startedAt`, `completedAt` and an optional `executionGroupId`.

The execution is correlated to a compliance evaluation through the evaluation table's
ExecutionIdIndex. On a succeeded execution the pipeline's `compliance-output.json` is read from
the execution's recorded output-result rows (PipelineExecutions -> PipelineExecutionOutputResults,
V2 execution records) and the pipeline rule's checks run against its measurements; any other
terminal status fails the rule. Once every pipeline rule of the evaluation has reported, the
evaluation, the asset state and the audit trail are finalized.
"""

import json
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.compliance import evaluationEngine as engine
from common.dynamodb import query_all_items
from common.resourceNames import ResourceKeys, get_table_name
from common.workflows.executionRecords import WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE
from customLogging.logger import safeLogger
from handlers.compliance import complianceEvaluationStore as store

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceWorkflowCallback")

try:
    pipeline_executions_table_name = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
    output_results_table_name = get_table_name(
        ResourceKeys.PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

pipeline_executions_table = dynamodb.Table(pipeline_executions_table_name)
output_results_table = dynamodb.Table(output_results_table_name)


def lambda_handler(event, context):
    """Finalize the pipeline rule a completed workflow execution belongs to."""
    detail = event.get("detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            detail = {}
    detail_type = event.get("detail-type") or event.get("detailType") or ""
    if detail_type and detail_type != WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE:
        logger.info(f"Ignoring event of detail type {detail_type}")
        return {"statusCode": 200, "body": "Ignored"}

    execution_id = detail.get("executionId", "")
    if not execution_id:
        logger.info("Completion event carries no executionId, skipping")
        return {"statusCode": 200, "body": "No executionId"}

    try:
        return process_completed_execution(
            execution_id,
            execution_status=detail.get("status", ""),
            started_at=detail.get("startedAt"),
            completed_at=detail.get("completedAt"),
        )
    except Exception as e:
        logger.exception(f"Error finalizing compliance evaluation for execution {execution_id}: {e}")
        return {"statusCode": 500, "body": json.dumps({"message": "Callback failed"})}


def process_completed_execution(execution_id: str, execution_status: str,
                                started_at: Optional[str], completed_at: Optional[str]) -> Dict[str, Any]:
    """Resolve the execution's evaluation and pipeline rule, read the pipeline's compliance output
    on success, and record the rule's outcome."""
    resolved = store.resolve_pipeline_execution(execution_id)
    if resolved is None:
        logger.info(f"Execution {execution_id} is not a compliance execution, skipping")
        return {"statusCode": 200, "body": "Not a compliance execution"}
    evaluation, rule_name = resolved

    if evaluation.get("status") != engine.EVALUATION_STATUS_PENDING_PIPELINE:
        logger.info(f"Evaluation {evaluation['evaluationId']} is not pending a pipeline result")
        return {"statusCode": 200, "body": "Already processed"}

    compliance_output = None
    if execution_status == "SUCCEEDED":
        pending_rules = engine.pipeline_rules_from_json(evaluation.get("pipelineRulesPending", "[]"))
        rule = pending_rules.get(rule_name)
        pipeline_id = rule.pipelineRef.pipelineId if rule else ""
        compliance_output = read_compliance_output(execution_id, pipeline_id)

    outcome = store.complete_pipeline_rule(
        evaluation, rule_name, execution_status, compliance_output, started_at, completed_at)
    logger.info(
        f"Evaluation {outcome.get('evaluationId')} rule '{rule_name}' recorded "
        f"(finalized={outcome.get('finalized')}, verdict={outcome.get('verdict')})")
    return {"statusCode": 200, "body": json.dumps(outcome)}


def read_compliance_output(execution_id: str, pipeline_id: str) -> Optional[Dict[str, Any]]:
    """The `compliance-output.json` document a pipeline of the execution wrote, read from the
    execution's recorded output-result rows; None when no pipeline wrote one.

    The result rows are keyed by pipelineExecutionId, so the execution's PipelineExecutions rows
    (PipelineExecByWorkflowExecGSI) are read first — the rule's pipeline preferred, then the
    end-state pipeline, then any other — and each pipeline execution's result rows are read to
    exhaustion.
    """
    pipeline_rows = query_all_items(
        pipeline_executions_table,
        IndexName="PipelineExecByWorkflowExecGSI",
        KeyConditionExpression=Key("workflowExecutionId").eq(execution_id),
    )
    for row in _ordered_pipeline_rows(pipeline_rows, pipeline_id):
        pipeline_execution_id = row.get("pipelineExecutionId", "")
        if not pipeline_execution_id:
            continue
        result_rows = query_all_items(
            output_results_table,
            KeyConditionExpression=Key("pipelineExecutionId").eq(pipeline_execution_id),
        )
        for result in result_rows:
            path = result.get("relativeFilePath", "") or result.get("s3Key", "")
            if not path.endswith(engine.COMPLIANCE_OUTPUT_FILE_NAME):
                continue
            if result.get("resultsContentTruncated"):
                logger.warning(
                    f"Compliance output of pipeline execution {pipeline_execution_id} is truncated")
                continue
            document = engine.parse_compliance_output(result.get("resultsContent", ""))
            if document is not None:
                return document
    return None


def _ordered_pipeline_rows(rows: List[Dict[str, Any]], pipeline_id: str) -> List[Dict[str, Any]]:
    """Pipeline execution rows with the rule's pipeline first, then the end-state pipeline."""
    def rank(row):
        if pipeline_id and row.get("pipelineId") == pipeline_id:
            return 0
        if row.get("endStatePipeline") == "true":
            return 1
        return 2
    return sorted(rows, key=rank)
