import os
import boto3
import botocore
import json

from common.constants import STANDARD_JSON_RESPONSE
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from botocore.exceptions import ClientError

claims_and_roles = {}
logger = safeLogger(service="WorkflowCommon")

dynamodb = boto3.resource('dynamodb')
sf_client = boto3.client('stepfunctions')
main_rest_response = STANDARD_JSON_RESPONSE

# update all workflows that are associated with a pipeline
def update_pipeline_workflows(self, pipelineData, event):

    workflow_table = dynamodb.Table(self.workflow_db_table_name)
    response = workflow_table.scan()
    workflow_table_items = response['Items']
    allowed = False

    updated_pipeline = pipelineData['functions'][0]

    global claims_and_roles
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

                    for i, step_name in enumerate(original_workflow["States"]):
                        if updated_pipeline['pipelineId'] in step_name:
                            try: 
                                original_workflow["States"][step_name]["Parameters"]["Payload"]["body"]["outputType"] = updated_pipeline['outputType']
                                original_workflow["States"][step_name]["Parameters"]["Payload"]["body"]["inputParameters"] = updated_pipeline['inputParameters']
                                original_workflow["States"][step_name]["Parameters"]["FunctionName"] = updated_pipeline['lambdaName']
                                original_workflow["States"][step_name]["TimeoutSeconds"] = int(updated_pipeline['taskTimeout'])
                                original_workflow["States"][step_name]["HeartbeatSeconds"] = int(updated_pipeline['taskHeartbeatTimeout'])
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