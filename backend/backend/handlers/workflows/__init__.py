# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
import json

from common.stepfunctions_builder import get_task_builder
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from botocore.exceptions import ClientError

logger = safeLogger(service="WorkflowCommon")

dynamodb = boto3.resource('dynamodb')
sf_client = boto3.client('stepfunctions')


def _scan_all_workflows(workflow_table):
    """Scan all workflows from the table, handling DynamoDB pagination."""
    items = []
    scan_kwargs = {}
    while True:
        response = workflow_table.scan(**scan_kwargs)
        items.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    return items


def _get_pipeline_id(pipeline_entry):
    """Get the pipeline identifier from a workflow pipeline entry.

    Workflows store the pipeline id as ``name`` (the model field) but
    ``format_pipeline`` in createPipeline also adds ``pipelineId``.
    Accept either so the match works regardless of which field is present.
    """
    return pipeline_entry.get('pipelineId') or pipeline_entry.get('name', '')


# update all workflows that are associated with a pipeline
def update_pipeline_workflows(self, pipelineData, event):

    workflow_table = dynamodb.Table(self.workflow_db_table_name)
    workflow_table_items = _scan_all_workflows(workflow_table)

    updated_pipeline = pipelineData['functions'][0]
    updated_pipeline_id = _get_pipeline_id(updated_pipeline)

    claims_and_roles = request_to_claims(event)

    any_updated = False

    for workflow in workflow_table_items:
        # Skip deleted workflows
        if "deleted" in workflow.get('databaseId', ''):
            continue

        if workflow:
            workflow.update({
                "object__type": "workflow"
            })

        # Permission check — reset per workflow
        allowed = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(workflow, "PUT"):
                allowed = True

        if not allowed:
            logger.info(f"No permission on workflow {workflow.get('workflowId', '?')}, skipping")
            continue

        pipelines = workflow.get('specifiedPipelines', {}).get('functions', [])
        workflowId = workflow['workflowId']
        workflow_arn = workflow.get('workflow_arn')
        workflow_database_id = workflow['databaseId']

        for index, p in enumerate(pipelines):
            pipeline_id = _get_pipeline_id(p)
            if pipeline_id != updated_pipeline_id:
                continue

            logger.info(f"Found match. Updating pipeline: {pipeline_id}, in Workflow: {workflowId}")
            workflow['specifiedPipelines']['functions'][index] = updated_pipeline
            any_updated = True

            # Update workflow table item with new pipeline data
            # Use the workflow's own databaseId as the partition key
            workflow_table.update_item(
                Key={
                    'databaseId': workflow_database_id,
                    'workflowId': workflowId,
                },
                UpdateExpression='set #sp=:p',
                ExpressionAttributeValues={
                    ':p': workflow['specifiedPipelines']
                },
                ExpressionAttributeNames={
                    "#sp": "specifiedPipelines"
                },
                ReturnValues="UPDATED_NEW"
            )
            logger.info("Workflow DynamoDB table item updated successfully")

            if not workflow_arn:
                logger.warning(f"No workflow_arn for workflow {workflowId}, skipping state machine update")
                continue

            # Update the workflow state machine
            sm_response = sf_client.describe_state_machine(
                stateMachineArn=workflow_arn,
            )
            original_workflow = json.loads(sm_response['definition'])

            # Determine execution type from the updated pipeline data
            # NOTE: pipelineExecutionType is immutable after creation, so the
            # execution type in the ASL always matches the updated pipeline's type.
            # No type-transition logic is needed.
            exec_type = updated_pipeline.get('pipelineExecutionType', 'Lambda')

            for step_name in original_workflow["States"]:
                if updated_pipeline_id in step_name:
                    try:
                        builder = get_task_builder(exec_type)

                        # Preserve transition fields from existing state
                        existing_state = original_workflow["States"][step_name]
                        transition = {}
                        if "Next" in existing_state:
                            transition["Next"] = existing_state["Next"]
                        if "End" in existing_state:
                            transition["End"] = existing_state["End"]

                        # Extract current payload based on execution type
                        if existing_state.get("Type") == "Task":
                            if exec_type == 'Lambda':
                                current_payload = existing_state.get("Parameters", {}).get("Payload", {})
                            elif exec_type == 'SQS':
                                current_payload = existing_state.get("Parameters", {}).get("MessageBody", {})
                            elif exec_type == 'EventBridge':
                                # EventBridge stores payload as Detail in Entries[0]
                                entries = existing_state.get("Parameters", {}).get("Entries", [{}])
                                current_payload = entries[0].get("Detail", {}) if entries else {}
                            else:
                                current_payload = {}

                            if current_payload.get("body"):
                                current_payload["body"]["outputType"] = updated_pipeline['outputType']
                                current_payload["body"]["inputParameters"] = updated_pipeline.get('inputParameters', '')

                            new_state = builder.build_task_state(updated_pipeline, step_name, current_payload)
                            new_state.update(transition)
                            original_workflow["States"][step_name] = new_state

                    except KeyError:
                        continue
                if "process-outputs" in step_name:
                    try:
                        process_pipeline = original_workflow["States"][step_name]["Parameters"]["Payload"]["body"]["pipeline"]
                        if updated_pipeline_id == process_pipeline:
                            original_workflow["States"][step_name]["Parameters"]["Payload"]["body"]["outputType"] = updated_pipeline['outputType']
                    except KeyError:
                        continue

            new_workflow = json.dumps(original_workflow, indent=2)
            logger.info("Submitting updated state machine")

            sf_client.update_state_machine(
                stateMachineArn=workflow_arn,
                definition=new_workflow,
                roleArn=sm_response['roleArn'],
                loggingConfiguration={
                    'destinations': [{
                        'cloudWatchLogsLogGroup': {
                            'logGroupArn': sm_response['loggingConfiguration']['destinations'][0]['cloudWatchLogsLogGroup']['logGroupArn']
                        }}],
                    'level': 'ALL'
                },
                tracingConfiguration={
                    'enabled': True
                }
            )
            logger.info("Workflow StepFunction state machine updated successfully")

    return {
        "statusCode": 200 if any_updated else 404,
        "message": updated_pipeline if any_updated else {}
    }
