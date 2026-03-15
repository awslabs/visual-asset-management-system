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


# update all workflows that are associated with a pipeline
def update_pipeline_workflows(self, pipelineData, event):

    workflow_table = dynamodb.Table(self.workflow_db_table_name)
    response = workflow_table.scan()
    workflow_table_items = response['Items']
    allowed = False

    updated_pipeline = pipelineData['functions'][0]

    claims_and_roles = request_to_claims(event)
    updated_workflows = []

    for workflow in workflow_table_items:
        if workflow:
            workflow.update({
                "object__type": "workflow"
            })

        #Permission check
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(workflow, "PUT"):
                    allowed = True

        if allowed and "deleted" not in workflow['databaseId']:
            pipelines = workflow['specifiedPipelines']['functions']
            workflowId = workflow['workflowId']
            workflow_arn = workflow['workflow_arn']
            for index, p in enumerate(pipelines):
                pipelineId = p['pipelineId']
                if pipelineId == updated_pipeline['pipelineId']:
                    logger.info("Found match. Updating pipeline: " + pipelineId + ", in Workflow: ", workflowId)
                    workflow['specifiedPipelines']['functions'][index] = updated_pipeline
                    # Update workflow table item with new pipeline data
                    workflow_table.update_item(
                        Key={
                            'databaseId': updated_pipeline['databaseId'],
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

                    # update the workflow state machine
                    response = sf_client.describe_state_machine(
                        stateMachineArn=workflow_arn,
                    )
                    original_workflow = json.loads(response['definition'])

                    # Determine execution type from the updated pipeline data
                    # NOTE: pipelineExecutionType is immutable after creation, so the
                    # execution type in the ASL always matches the updated pipeline's type.
                    # No type-transition logic is needed.
                    exec_type = updated_pipeline.get('pipelineExecutionType', 'Lambda')

                    for step_name in original_workflow["States"]:
                        if updated_pipeline['pipelineId'] in step_name:
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
                            if updated_pipeline['pipelineId'] == original_workflow["States"][step_name]["Parameters"]["Payload"]["body"]["pipeline"]:
                                try:
                                    original_workflow["States"][step_name]["Parameters"]["Payload"]["body"]["outputType"] = updated_pipeline['outputType']
                                except KeyError:
                                    continue

                    new_workflow = json.dumps(original_workflow, indent=2)
                    logger.info("Submitting updated state machine")

                    sf_client.update_state_machine(
                        stateMachineArn=workflow_arn,
                        definition=new_workflow,
                        roleArn=response['roleArn'],
                        loggingConfiguration={
                            'destinations': [{
                                'cloudWatchLogsLogGroup': {
                                    'logGroupArn': response['loggingConfiguration']['destinations'][0]['cloudWatchLogsLogGroup']['logGroupArn']
                                }}],
                            'level': 'ALL'
                        },
                        tracingConfiguration={
                            'enabled': True
                        }
                    )
                    logger.info("Workflow StepFunction state machine updated successfully")
        else:
            logger.info("No permission on workflow to delete or re-create, or workflow already deleted...")

    return {
        "statusCode": 200 if allowed else 404,
        "message": updated_pipeline if allowed else {}
    }
