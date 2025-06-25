#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
import botocore
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.conditions import Attr
import os
from common.validators import validate
from common.constants import STANDARD_JSON_RESPONSE
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from urllib.parse import unquote_plus

claims_and_roles = {}
logger = safeLogger(service="ExecuteWorkflow")

try:
    client = boto3.client('lambda')
    s3c = boto3.client('s3')
    sfn_client = boto3.client('stepfunctions')
    dynamodb = boto3.resource('dynamodb')
except Exception as e:
    logger.exception("Failed Loading Error Functions")

bucket_name_asset = None
bucket_name_assetAuxiliary = None

try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    pipeline_Database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]
    bucket_name_asset = os.environ["S3_ASSET_STORAGE_BUCKET"]
    bucket_name_assetAuxiliary = os.environ["S3_ASSETAUXILIARY_STORAGE_BUCKET"]
    metadata_read_function = os.environ['METADATA_READ_LAMBDA_FUNCTION_NAME']
except:
    logger.exception("Failed loading environment variables")


def get_pipelines(databaseId, pipelineId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('pipelineId').eq(pipelineId),
        ScanIndexForward=False,
    )
    return response['Items']


def _metadata_lambda(payload): return client.invoke(FunctionName=metadata_read_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))


def get_asset_metadata(databaseId, assetId, keyPrefix, event):
    try:
        l_payload = {
            "pathParameters": {
                "databaseId": databaseId,
                "assetId": assetId
            },
            "queryStringParameters": {
                "prefix": keyPrefix
            }
        }
        l_payload.update({
            "requestContext": {
                "http": {
                    "path": "event['requestContext']['http']['path']",
                    "method": event['requestContext']['http']['method']
                },
                "authorizer": event['requestContext']['authorizer']
            }
        })
        logger.info("Fetching metadata:")
        logger.info(l_payload)
        metadata_response = _metadata_lambda(l_payload)
        logger.info("metaData read response:")
        logger.info(metadata_response)
        stream = metadata_response.get('Payload', "")
        response_body = {}
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info("uploadAsset payload:", json_response)
            if "body" in json_response:
                response_body = json.loads(json_response['body'])

                # if "asset" in response_body:
                #     assets.append(response_body['asset'])
        return response_body
    except Exception as e:
        logger.exception("Failed fetching metadata")
        logger.exception(e)
        return {}


def launchWorkflow(inputAssetFileKey, workflow_arn, asset_id, workflow_id, database_id, executingUserName, executingRequestContext, inputMetadata = {}):

    logger.info("Launching workflow with arn: "+workflow_arn)

    #Modify asset key to turn + sympbols into spaces for the final processing entry
    inputAssetFileKey = unquote_plus(inputAssetFileKey)

    response = sfn_client.start_execution(
        stateMachineArn=workflow_arn,
        input=json.dumps({'bucketAsset': bucket_name_asset, 'bucketAssetAuxiliary': bucket_name_assetAuxiliary, 'inputAssetFileKey': inputAssetFileKey, 'databaseId': database_id,
                          'assetId': asset_id, 'inputMetadata': json.dumps(inputMetadata),
                          'workflowId': workflow_id, 'executingUserName': executingUserName, 'executingRequestContext': executingRequestContext})
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

def is_global_workflow(workflowId):
    table = dynamodb.Table(workflow_database)
    
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq('GLOBAL') & Key('workflowId').eq(workflowId)
    )
    
    return len(response.get('Items', [])) > 0


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
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(pipeline_state, "POST"):
                    allowed = True

        if not allowed:
            return (False, pipeline["name"])

    return (True, '')

def get_workflow_executions(assetId, workflowId):
        logger.info("Getting current executions")
        pk = f'{assetId}-{workflowId}'

        paginator = dynamodb.meta.client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=workflow_execution_database,
            KeyConditionExpression=Key('pk').eq(pk),
            ScanIndexForward=False,
            PaginationConfig={
                'MaxItems': 500,
                'PageSize': 500,
                'StartingToken': None
            }
        ).build_full_result()

        items = []
        items.extend(page_iterator['Items'])

        while 'NextToken' in page_iterator:
            nextToken = page_iterator['NextToken']
            page_iterator = paginator.paginate(
                TableName=workflow_execution_database,
                KeyConditionExpression=Key('pk').eq(pk),
                ScanIndexForward=False,
                PaginationConfig={
                    'MaxItems': 500,
                    'PageSize': 500,
                    'StartingToken': nextToken
                }
            ).build_full_result()
            items.extend(page_iterator['Items'])

        result = {
            "Items": []
        }

        logger.info(items)
        for item in items:
            try:
                workflow_arn = item['workflow_arn']
                execution_arn = workflow_arn.replace("stateMachine", "execution")
                execution_arn = execution_arn + ":" + item['execution_id']

                startDate = item.get('startDate', "")
                stopDate = item.get('stopDate', "")

                #If our table doesn't have a stopDate on a execution, continue to look for a running execution
                if not stopDate:
                    logger.info("Fetching SFN execution information")
                    execution = sfn_client.describe_execution(
                        executionArn=execution_arn
                    )
                    startDate = execution.get('startDate', "")
                    if startDate:
                        startDate = startDate.strftime("%m/%d/%Y, %H:%M:%S")
                    stopDate = execution.get('stopDate', "")
                    if stopDate:
                        stopDate = stopDate.strftime("%m/%d/%Y, %H:%M:%S")

                    #Add to results as even our step functions say a execution is still running
                    if not stopDate:
                        logger.info("Adding to results: " + execution['name'])
                        result["Items"].append({
                            'executionId': execution['name'],
                            'executionStatus': execution['status'],
                            'startDate': startDate,
                        })

            except Exception as e:
                logger.exception(e)
                logger.info("Continuing with trying to fetch exceutions...")

        return result


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)
    logger.info(event)

    # Parse request body if present
    request_body = {}
    if event.get('body'):
        try:
            request_body = json.loads(event['body'])
            logger.info("Request body: %s", request_body)
        except:
            logger.warning("Failed to parse request body as JSON")

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

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

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

                executingUserName = ''
                executingRequestContext = event['requestContext']
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(asset, "POST"):
                        asset_allowed = True
                        executingUserName = claims_and_roles["tokens"][0]
                if asset_allowed:
                    # Check if workflow is Global 
                    isGlobalWorkflow = is_global_workflow(pathParams['workflowId'])

                    # If global workflow, adjust path parameter
                    if isGlobalWorkflow:
                        workflowResponse = get_workflow("GLOBAL", pathParams['workflowId'])
                    else:
                        workflowResponse = get_workflow(pathParams['databaseId'], pathParams['workflowId'])
                    logger.info(workflowResponse)
                    if bool(workflowResponse):
                        workflow = workflowResponse[0]
                        workflow_allowed = False
                        # Add Casbin Enforcer to check if the current user has permissions to POST the workflow:
                        workflow.update({
                            "object__type": "workflow"
                        })
                        if len(claims_and_roles["tokens"]) > 0:
                            casbin_enforcer = CasbinEnforcer(claims_and_roles)
                            if casbin_enforcer.enforce(workflow, "POST"):
                                workflow_allowed = True

                        if workflow_allowed:
                            # If global workflow, use request body to get workflow database ID
                            if request_body.get('workflowDatabaseId'):
                                (status, pipelineName) = validate_pipelines(request_body.get('workflowDatabaseId'), workflow)
                            else:
                                (status, pipelineName) = validate_pipelines(pathParams['databaseId'], workflow)
                            if not status:
                                logger.error("Not all pipelines are enabled/accessible")
                                response['statusCode'] = 400
                                response['body'] = json.dumps({'message': f'{pipelineName} is not enabled/accessible'})
                            else:
                                logger.info("All pipelines are enabled. Continuing to run workflow")

                            #Get current executions for workflow on asset. If currently one running, error.
                            executionResults = get_workflow_executions(pathParams['assetId'], pathParams['workflowId'])
                            if len(executionResults['Items']) > 0:
                                logger.error("Workflow has a currently running execution on the asset")
                                response['statusCode'] = 400
                                response['body'] = json.dumps({'message': 'Workflow has a currently running execution on the asset'})
                                return response

                            ##Formulate pipeline input metadata for VAMS
                            #TODO: Implement additional user input fields on execute (from a new UX popup?)
                            # If global workflow, adjust path parameter
                            if pathParams['databaseId'] == "global":
                                metadataResponse = get_asset_metadata("GLOBAL", pathParams['assetId'], asset['assetLocation']['Key'], event)
                            else:
                                metadataResponse = get_asset_metadata(pathParams['databaseId'], pathParams['assetId'], asset['assetLocation']['Key'], event)
                            metadata = metadataResponse.get("metadata", {})

                            #remove databaseId/assetId from metadata if exists
                            metadata.pop('databaseId', None)
                            metadata.pop('assetId', None)

                            inputMetadata = {
                                "VAMS": {
                                    "assetData": {
                                        "assetName":asset.get("assetName", ""),
                                        "description": asset.get("description", ""),
                                        "tags": asset.get("tags", [])
                                    },
                                    "assetMetadata": metadata
                                },
                                #"User": {}
                            }

                            logger.info("Launching Workflow:")
                            # If global workflow, adjust path parameter
                            if pathParams['databaseId'] == "global":
                                executionId = launchWorkflow(asset['assetLocation']['Key'], workflow['workflow_arn'],
                                                            pathParams['assetId'], workflow['workflowId'],
                                                            "GLOBAL", executingUserName, executingRequestContext, inputMetadata)
                            else: 
                                executionId = launchWorkflow(asset['assetLocation']['Key'], workflow['workflow_arn'],
                                                            pathParams['assetId'], workflow['workflowId'],
                                                            pathParams['databaseId'], executingUserName, executingRequestContext, inputMetadata)
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
