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

bucket_name_assetAuxiliary = None

try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    pipeline_Database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]
    bucket_name_assetAuxiliary = os.environ["S3_ASSETAUXILIARY_STORAGE_BUCKET"]
    metadata_read_function = os.environ['METADATA_READ_LAMBDA_FUNCTION_NAME']
except:
    logger.exception("Failed loading environment variables")

buckets_table = dynamodb.Table(s3_asset_buckets_table)

def get_default_bucket_details(bucketId):
    """Get default S3 bucket details from database default bucket DynamoDB"""
    try:

        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucketId),
            Limit=1
        )
        # Use the first item from the query results
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        #Check to make sure we have what we need
        if not bucket_name or not base_assets_prefix:
            raise Exception(f"Error getting database default bucket details: {str(e)}")
        
        #Make sure we end in a slash for the path
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        # Remove leading slash from file path if present
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise Exception(f"Error getting bucket details: {str(e)}")

def get_pipelines(databaseId, pipelineId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('pipelineId').eq(pipelineId),
        ScanIndexForward=False,
    )
    return response['Items']


def _metadata_lambda(payload): return client.invoke(FunctionName=metadata_read_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))


def resolve_asset_file_path(asset_base_key: str, file_path: str) -> str:
    """
    Intelligently resolve the full S3 key, avoiding duplication if file_path already contains the asset base key.
    
    Args:
        asset_base_key: The base key from assetLocation (e.g., "assetId/" or "custom/path/")
        file_path: The file path from the request (may or may not include the base key)
        
    Returns:
        The properly resolved S3 key without duplication
    """
    # Normalize the asset base key to ensure it ends with '/'
    if asset_base_key and not asset_base_key.endswith('/'):
        asset_base_key = asset_base_key + '/'
    
    # Remove leading slash from file path if present
    if file_path.startswith('/'):
        file_path = file_path[1:]
    
    # Check if file_path already starts with the asset_base_key
    if file_path.startswith(asset_base_key):
        # File path already contains the base key, use as-is
        logger.info(f"File path '{file_path}' already contains base key '{asset_base_key}', using as-is")
        return file_path
    else:
        # File path doesn't contain base key, combine them
        resolved_path = asset_base_key + file_path
        logger.info(f"Combined base key '{asset_base_key}' with file path '{file_path}' to get '{resolved_path}'")
        return resolved_path

def get_asset_metadata(databaseId, assetId, keyPrefix, event):
    try:
        l_payload = {
            "pathParameters": {
                "databaseId": databaseId,
                "assetId": assetId
            }
        }

        #If keyprefix doesn't end-with a /, add additional data to get the files specific metadata too
        if not keyPrefix.endswith("/"):
            l_payload.update({
                "queryStringParameters": {
                "prefix": keyPrefix
            }
            })
        
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


def launchWorkflow(inputAssetBucket, inputAssetFileKey, workflow_arn, database_id, asset_id, workflow_database_id, workflow_id, executingUserName, executingRequestContext, inputMetadata = {}):

    logger.info("Launching workflow with arn: "+workflow_arn)

    #Modify asset key to turn + sympbols into spaces for the final processing entry
    inputAssetFileKey = unquote_plus(inputAssetFileKey)

    response = sfn_client.start_execution(
        stateMachineArn=workflow_arn,
        input=json.dumps({'bucketAsset': inputAssetBucket, 'bucketAssetAuxiliary': bucket_name_assetAuxiliary, 'inputAssetFileKey': inputAssetFileKey, 'databaseId': database_id,
                          'assetId': asset_id, 'inputMetadata': json.dumps(inputMetadata), 'workflowDatabaseId': workflow_database_id,
                          'workflowId': workflow_id, 'executingUserName': executingUserName, 'executingRequestContext': executingRequestContext})
    )
    # response = {
    #     'executionArn': "XXX:AAA",
    # }
    logger.info("Workflow Response: ")
    logger.info(response)
    executionId = response['executionArn'].split(":")[-1]

    #Create compiled partition key
    partitionKey = f"${database_id}:${asset_id}"
    #Create compiled workflow LSI key
    workflowLsi = f"${workflow_database_id}:${workflow_id}"

    table = dynamodb.Table(workflow_execution_database)
    table.put_item(
        Item={
            'databaseId:assetId': partitionKey, #pk
            'executionId': executionId, #sk
            'workflowDatabaseId:workflowId': workflowLsi, #sk LSI
            'databaseId': database_id,
            'assetId': asset_id,
            'workflowId': workflow_id,
            'workflowDatabaseId': workflow_database_id,
            'workflow_arn': workflow_arn,
            'execution_arn': response['executionArn'],
            'startDate': "",
            'stopDate': "",
            'executionStatus': "NEW"
        }
    )
    return executionId


def get_asset(databaseId, assetId):
    table = dynamodb.Table(asset_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId)
    )
    return response['Items']


def get_workflow(workflowDatabaseId, workflowId):
    table = dynamodb.Table(workflow_database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(workflowDatabaseId) & Key('workflowId').eq(workflowId)
    )
    return response['Items']


def validate_pipelines(workflow):
    for pipeline in workflow['specifiedPipelines']['functions']:
        pipeline_state = get_pipelines(workflow['databaseId'], pipeline["name"])[0]
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

def get_workflow_executions(databaseId, assetId, workflowDatabaseId, workflowId):
        logger.info("Getting current executions")

        paginator = dynamodb.meta.client.get_paginator('query')

        partitionKey = f'${databaseId}:${assetId}'
        if workflowId == '':
            keyExpression = Key('databaseId:assetId').eq(partitionKey)
        else:
            workflowLsi = f"${workflowDatabaseId}:${workflowId}"
            keyExpression = Key('databaseId:assetId').eq(partitionKey) & Key('workflowDatabaseId:workflowId').eq(workflowLsi)

        page_iterator = paginator.paginate(
            TableName=workflow_execution_database,
            IndexName='WorkflowLSI',
            KeyConditionExpression=keyExpression,
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
                IndexName='WorkflowLSI',
                KeyConditionExpression=keyExpression,
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
                execution_arn = execution_arn + ":" + item['executionId']

                startDate = item.get('startDate', "")
                stopDate = item.get('stopDate', "")

                #If our table doesn't have a stopDate on a execution, continue to look for a running execution
                if not stopDate or stopDate == "":
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
                            'workflowDatabaseId': item['workflowDatabaseId'],
                            'workflowId': item['workflowId'],
                            'executionId': execution['name'],
                            'executionStatus': execution['status'],
                            'startDate': startDate,
                        })

            except Exception as e:
                logger.exception(e)
                logger.info("Continuing with trying to fetch exceutions...")

        logger.info(f"Returning existing execution results: {result}")
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
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Invalid JSON in request body"})
            return response

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
                'validator': 'ASSET_ID'
            },
            'workflowDatabaseId': {
                'value': request_body.get('workflowDatabaseId', ''),
                'validator': 'ID',
                'allowGlobalKeyword': True
            },
            'assetKey': {
                'value': request_body.get('fileKey', ''),
                'validator': 'ASSET_PATH',
                'isFolder': False,
                'optional': True
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


                    workflowResponse = get_workflow(request_body.get('workflowDatabaseId'), pathParams['workflowId'])
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
                            (status, pipelineName) = validate_pipelines(workflow)
                            if not status:
                                logger.error("Not all pipelines are enabled/accessible")
                                response['statusCode'] = 400
                                response['body'] = json.dumps({'message': 'Pipeline is not enabled/accessible'})
                            else:
                                logger.info("All pipelines are enabled. Continuing to run workflow")

                            #Get current executions for workflow on asset. If currently one running, error.
                            executionResults = get_workflow_executions(pathParams['databaseId'], pathParams['assetId'], request_body.get('workflowDatabaseId'), pathParams['workflowId'], )
                            if len(executionResults['Items']) > 0:
                                logger.error("Workflow has a currently running execution on the asset")
                                response['statusCode'] = 400
                                response['body'] = json.dumps({'message': 'Workflow has a currently running execution on the asset'})
                                return response

                            ##Formulate pipeline input metadata for VAMS
                            #TODO: Implement additional user input fields on execute (from a new UX popup?)
                            
                            # Determine which file key to use
                            # If fileKey is provided in request body, use it, otherwise use asset's base prefix key
                            file_key = asset['assetLocation']['Key']
                            
                            # Get bucket name from bucketId using get_default_bucket_details
                            bucketDetails = get_default_bucket_details(asset['bucketId'])
                            asset_bucket = bucketDetails['bucketName']
                            
                            if request_body and 'fileKey' in request_body:
                                file_key = resolve_asset_file_path(file_key, request_body['fileKey'])
                                logger.info(f"Using file key from request: {file_key}")
                            else:
                                logger.info(f"Using asset's base prefix key (no particular file): {file_key}")
                            
                            metadataResponse = get_asset_metadata(pathParams['databaseId'], pathParams['assetId'], file_key, event)
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
                            executionId = launchWorkflow(asset_bucket, file_key, workflow['workflow_arn'], pathParams['databaseId'],
                                                         pathParams['assetId'], request_body.get('workflowDatabaseId'), workflow['workflowId'],
                                                         executingUserName, executingRequestContext, inputMetadata)
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
