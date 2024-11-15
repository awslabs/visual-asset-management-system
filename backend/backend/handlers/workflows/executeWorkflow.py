#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
import botocore
from boto3.dynamodb.conditions import Key
import os
from common.validators import validate
from common.constants import STANDARD_JSON_RESPONSE
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="ExecuteWorkflow")

try:
    client = boto3.client('lambda')
    s3c = boto3.client('s3')
    sfn_client = boto3.client('stepfunctions')
    dynamodb = boto3.resource('dynamodb')
except Exception as e:
    logger.exception("Failed Loading Error Functions")

bucket_name = None

try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    pipeline_Database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
except:
    logger.exception("Failed loading environment variables")


def get_pipelines(databaseId, pipelineId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('pipelineId').eq(pipelineId),
        ScanIndexForward=False,
    )
    return response['Items']


def launchWorkflow(key, workflow_arn, asset_id, workflow_id, database_id):

    logger.info("Launching workflow with arn: "+workflow_arn)
    response = sfn_client.start_execution(
        stateMachineArn=workflow_arn,
        input=json.dumps({'bucket': bucket_name, 'key': key, 'databaseId': database_id,
                          'assetId': asset_id, 'workflowId': workflow_id})
    )
    # response = {
    #     'executionArn': "XXX:AAA",
    # }
    logger.info("Workflow Response: ")
    logger.info(response)
    executionId = response['executionArn'].split(":")[-1]
    table = dynamodb.Table(workflow_execution_database)
    table.put_item(
        Item={
            'pk': f'{asset_id}-{workflow_id}',
            'sk': executionId,
            'database_id': database_id,
            'asset_id': asset_id,
            'workflow_id': workflow_id,
            'workflow_arn': workflow_arn,
            'execution_arn': response['executionArn'],
            'execution_id': executionId,
            'assets': []
        }
    )
    return executionId


def get_asset(databaseId, assetId):
    table = dynamodb.Table(asset_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId)
    )
    return response['Items']


def get_workflow(databaseId, workflowId):
    table = dynamodb.Table(workflow_database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('workflowId').eq(workflowId)
    )
    return response['Items']


def validate_pipelines(databaseId, workflow):
    for pipeline in workflow['specifiedPipelines']['functions']:
        pipeline_state = get_pipelines(databaseId, pipeline["name"])[0]
        if not pipeline_state['enabled']:
            logger.warning(f"Pipeline {pipeline['name']} is disabled")
            return (False, pipeline["name"])

        allowed = False
        if pipeline_state:
            # Add Casbin Enforcer to check if the current user has permissions to POST the pipeline:
            pipeline.update({
                "object__type": "pipeline"
            })
            for user_name in claims_and_roles["tokens"]:
                casbin_enforcer = CasbinEnforcer(user_name)
                if casbin_enforcer.enforce(f"user::{user_name}", pipeline_state, "POST"):
                    allowed = True
                    break

        if not allowed:
            return (False, pipeline["name"])

    return (True, '')


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)
    logger.info(event)
    try:
        pathParams = event.get('pathParameters', {})
        logger.info(pathParams)
        # Check for missing fields - TODO: would need to keep these synchronized
        #
        required_field_names = ['databaseId', 'workflowId', 'assetId']
        missing_field_names = list(set(required_field_names).difference(pathParams))
        if missing_field_names:
            message = 'Missing path parameter(s) (%s) in API call' % (', '.join(missing_field_names))
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response
        logger.info("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParams.get('databaseId', ''),
                'validator': 'ID'
            },
            'workflowId': {
                'value': pathParams.get('workflowId', ''),
                'validator': 'ID'
            },
            'assetId': {
                'value': pathParams.get('assetId', ''),
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        httpMethod = event['requestContext']['http']['method']
        logger.info(httpMethod)

        method_allowed_on_api = False
        request_object = {
            "object__type": "api",
            "route__path": event['requestContext']['http']['path']
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", request_object, httpMethod):
                method_allowed_on_api = True
                break

        if method_allowed_on_api:
            assetResponse = get_asset(pathParams['databaseId'], pathParams['assetId'])
            logger.info(assetResponse)
            if bool(assetResponse):
                asset = assetResponse[0]
                asset_allowed = False
                # Add Casbin Enforcer to check if the current user has permissions to POST the asset:
                asset.update({
                    "object__type": "asset"
                })
                for user_name in claims_and_roles["tokens"]:
                    casbin_enforcer = CasbinEnforcer(user_name)
                    if casbin_enforcer.enforce(f"user::{user_name}", asset, "POST"):
                        asset_allowed = True
                        break
                if asset_allowed:
                    workflowResponse = get_workflow(pathParams['databaseId'], pathParams['workflowId'])
                    logger.info(workflowResponse)
                    if bool(workflowResponse):
                        workflow = workflowResponse[0] 
                        workflow_allowed = False
                        # Add Casbin Enforcer to check if the current user has permissions to POST the workflow:
                        workflow.update({
                            "object__type": "workflow"
                        })
                        for user_name in claims_and_roles["tokens"]:
                            casbin_enforcer = CasbinEnforcer(user_name)
                            if casbin_enforcer.enforce(f"user::{user_name}", workflow, "POST"):
                                workflow_allowed = True
                                break

                        if workflow_allowed:
                            (status, pipelineName) = validate_pipelines(pathParams['databaseId'], workflow)
                            if not status:
                                logger.error("Not all pipelines are enabled/accessible")
                                response['statusCode'] = 400
                                response['body'] = json.dumps({'message': f'{pipelineName} is not enabled/accessible'})
                            else:
                                logger.info("All pipelines are enabled. Continuing to run run workflow")


                            logger.info("Launching Workflow:"
                                        )
                            executionId = launchWorkflow(asset['assetLocation']['Key'], workflow['workflow_arn'], 
                                                         pathParams['assetId'], workflow['workflowId'], pathParams['databaseId'])
                            response["statusCode"] = 200
                            response['body'] = json.dumps({'message': executionId})
                            return response
                        else:
                            response['statusCode'] = 403
                            response['body'] = json.dumps({"message": "Not Authorized"})
                            return response
                    else:
                        response['statusCode'] = 404
                        response['body'] = json.dumps({"message": "Workflow does not exist"})
                        return response
                else:
                    response['statusCode'] = 403
                    response['body'] = json.dumps({"message": "Not Authorized"})
                    return response
            else:
                response['statusCode'] = 404
                response['body'] = json.dumps({"message": "Asset does not exist"})
                return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] == 'LimitExceededException' or err.response['Error']['Code'] == 'ThrottlingException': 
            logger.exception("Throttling Error")
            response['statusCode'] = err.response['ResponseMetadata']['HTTPStatusCode']
            response['body'] = json.dumps({"message": "ThrottlingException: Too many requests within a given period."})
            return response
        elif err.response['Error']['Code'] == 'ExecutionLimitExceeded':
            logger.exception("ExecutionLimitExceeded")
            response['statusCode'] = err.response['ResponseMetadata']['HTTPStatusCode']
            response['body'] = json.dumps({"message": "ExecutionLimitExceeded: Reached the maximum state machine execution limit of 1,000,000"})
            return response
        else:
            logger.exception(err)
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
            return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
