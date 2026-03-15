#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.conditions import Attr
import json
from aws_lambda_powertools.utilities.typing import LambdaContext
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from urllib.parse import unquote_plus
from models.common import (
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse
)

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
    metadata_service_function = os.environ['METADATA_SERVICE_LAMBDA_FUNCTION_NAME']
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
            raise Exception(f"Error getting database default bucket details.")

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
        raise Exception(f"Error getting bucket details.")

def get_pipelines(databaseId, pipelineId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('pipelineId').eq(pipelineId),
        ScanIndexForward=False,
    )
    return response['Items']


def _metadata_service_lambda(payload):
    """Invoke metadata service lambda"""
    return client.invoke(
        FunctionName=metadata_service_function,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload).encode('utf-8')
    )


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


def get_asset_metadata(databaseId, assetId, event):
    """Get asset metadata using new metadata service"""
    try:
        # Build Lambda event for metadata service GET endpoint
        l_payload = {
            'requestContext': {
                'http': {
                    'path': f'/database/{databaseId}/assets/{assetId}/metadata',
                    'method': 'GET'
                },
                'authorizer': event['requestContext']['authorizer']
            },
            'pathParameters': {
                'databaseId': databaseId,
                'assetId': assetId
            },
            'queryStringParameters': {}
        }

        logger.info("Fetching asset metadata from metadata service")
        logger.info(l_payload)

        metadata_response = _metadata_service_lambda(l_payload)
        logger.info("Asset metadata response received")

        stream = metadata_response.get('Payload', "")
        response_body = {}
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info(f"Asset metadata payload status: {json_response.get('statusCode')}")
            if "body" in json_response and json_response.get('statusCode') == 200:
                response_body = json.loads(json_response['body'])

        return response_body
    except Exception as e:
        logger.exception(f"Failed fetching asset metadata: {e}")
        return {}


def get_file_metadata(databaseId, assetId, filePath, event):
    """Get file metadata using new metadata service"""
    try:
        # Build Lambda event for metadata service GET endpoint
        l_payload = {
            'requestContext': {
                'http': {
                    'path': f'/database/{databaseId}/assets/{assetId}/metadata/file',
                    'method': 'GET'
                },
                'authorizer': event['requestContext']['authorizer']
            },
            'pathParameters': {
                'databaseId': databaseId,
                'assetId': assetId
            },
            'queryStringParameters': {
                'filePath': filePath,
                'type': 'metadata'
            }
        }

        logger.info(f"Fetching file metadata from metadata service for file: {filePath}")
        logger.info(l_payload)

        metadata_response = _metadata_service_lambda(l_payload)
        logger.info("File metadata response received")

        stream = metadata_response.get('Payload', "")
        response_body = {}
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info(f"File metadata payload status: {json_response.get('statusCode')}")
            if "body" in json_response and json_response.get('statusCode') == 200:
                response_body = json.loads(json_response['body'])

        return response_body
    except Exception as e:
        logger.exception(f"Failed fetching file metadata: {e}")
        return {}


def get_file_attributes(databaseId, assetId, filePath, event):
    """Get file attributes using new metadata service"""
    try:
        # Build Lambda event for metadata service GET endpoint
        l_payload = {
            'requestContext': {
                'http': {
                    'path': f'/database/{databaseId}/assets/{assetId}/metadata/file',
                    'method': 'GET'
                },
                'authorizer': event['requestContext']['authorizer']
            },
            'pathParameters': {
                'databaseId': databaseId,
                'assetId': assetId
            },
            'queryStringParameters': {
                'filePath': filePath,
                'type': 'attribute'
            }
        }

        logger.info(f"Fetching file attributes from metadata service for file: {filePath}")
        logger.info(l_payload)

        metadata_response = _metadata_service_lambda(l_payload)
        logger.info("File attributes response received")

        stream = metadata_response.get('Payload', "")
        response_body = {}
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info(f"File attributes payload status: {json_response.get('statusCode')}")
            if "body" in json_response and json_response.get('statusCode') == 200:
                response_body = json.loads(json_response['body'])

        return response_body
    except Exception as e:
        logger.exception(f"Failed fetching file attributes: {e}")
        return {}


def simplify_metadata_array(metadata_array):
    """
    Convert verbose metadata array to simple key-value dictionary.
    Removes all schema fields and nested structure to reduce size for pipeline input.

    Args:
        metadata_array: List of metadata objects with full schema info

    Returns:
        Dictionary with metadataKey as key and metadataValue as value
    """
    simplified = {}
    for item in metadata_array:
        key = item.get('metadataKey', '')
        value = item.get('metadataValue', '')
        if key:  # Only add if key exists
            simplified[key] = value
    return simplified


def get_separate_metadata(databaseId, assetId, filePath, event):
    """Get asset metadata, file metadata, and file attributes separately using new metadata service"""
    try:
        # Always get asset metadata
        asset_metadata_response = get_asset_metadata(databaseId, assetId, event)

        # Extract metadata list from response (new format)
        asset_metadata = asset_metadata_response.get("metadata", [])

        # If a file path is provided, also get file metadata and attributes
        file_metadata = []
        file_attributes = []
        if filePath:
            file_metadata_response = get_file_metadata(databaseId, assetId, filePath, event)
            file_metadata = file_metadata_response.get("metadata", [])

            file_attributes_response = get_file_attributes(databaseId, assetId, filePath, event)
            file_attributes = file_attributes_response.get("metadata", [])

        logger.info(f"Retrieved metadata - Asset: {len(asset_metadata)} items, File: {len(file_metadata)} items, Attributes: {len(file_attributes)} items")

        return {
            "assetMetadata": {"metadata": asset_metadata},
            "fileMetadata": {"metadata": file_metadata},
            "fileAttributes": {"metadata": file_attributes}
        }
    except Exception as e:
        logger.exception(f"Failed fetching separate metadata: {e}")
        return {
            "assetMetadata": {"metadata": []},
            "fileMetadata": {"metadata": []},
            "fileAttributes": {"metadata": []}
        }


def launchWorkflow(inputAssetBucket, inputAssetLocationKey, inputAssetFileKey, workflow_arn, database_id, asset_id, workflow_database_id, workflow_id, executingUserName, executingRequestContext, inputMetadata = {}):

    logger.info("Launching workflow with arn: "+workflow_arn)

    #Modify asset key to turn + sympbols into spaces for the final processing entry
    inputAssetFileKey = unquote_plus(inputAssetFileKey)

    response = sfn_client.start_execution(
        stateMachineArn=workflow_arn,
        input=json.dumps({'bucketAsset': inputAssetBucket, 'bucketAssetAuxiliary': bucket_name_assetAuxiliary, 'inputAssetLocationKey': inputAssetLocationKey, 'inputAssetFileKey': inputAssetFileKey, 'databaseId': database_id,
                          'assetId': asset_id, 'inputMetadata': json.dumps(inputMetadata), 'workflowDatabaseId': workflow_database_id,
                          'workflowId': workflow_id, 'executingUserName': executingUserName, 'executingRequestContext': executingRequestContext})
    )
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
            'inputAssetFileKey': inputAssetFileKey,
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


def validate_pipelines(workflow, claims_and_roles):
    for pipeline in workflow['specifiedPipelines']['functions']:
        pipeline_state = get_pipelines(workflow['databaseId'], pipeline["name"])[0]
        if not pipeline_state['enabled']:
            logger.warning(f"Pipeline {pipeline['name']} is disabled")
            return (False, pipeline["name"])

        allowed = False
        if pipeline_state:
            # Add Casbin Enforcer to check if the current user has permissions to POST the pipeline (Tier 2):
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

def get_workflow_executions(databaseId, assetId, workflowDatabaseId, workflowId, file_key=None):
        logger.info("Getting current executions")
        if file_key:
            logger.info(f"Filtering executions by file key: {file_key}")

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
                # If file_key is provided, filter by it
                if file_key:
                    item_file_key = item.get('inputAssetFileKey', '')
                    if item_file_key != file_key:
                        continue

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


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    logger.info(event)

    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)

        # Check if method is allowed on API (Tier 1)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Parse request body if present
        request_body = {}
        if event.get('body'):
            try:
                request_body = json.loads(event['body'])
                logger.info("Request body: %s", request_body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': 'Invalid JSON in request body'}, event=event)

        pathParams = event.get('pathParameters', {})
        logger.info(pathParams)

        # Check for missing fields
        required_field_names = ['databaseId', 'workflowId', 'assetId']
        missing_field_names = list(set(required_field_names).difference(pathParams))
        if missing_field_names:
            message = 'Missing path parameter(s) (%s) in API call' % (', '.join(missing_field_names))
            return validation_error(body={'message': message}, event=event)

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
            return validation_error(body={'message': message}, event=event)

        # Validate that workflow database ID is either "GLOBAL" or matches asset database ID
        asset_database_id = pathParams['databaseId']
        workflow_database_id = request_body.get('workflowDatabaseId', '')

        if workflow_database_id != 'GLOBAL' and workflow_database_id != asset_database_id:
            logger.error(f"Workflow database ID validation failed. Workflow can only be executed on assets from the same database or from global workflows.")
            return validation_error(
                body={'message': 'Workflow can only be executed on assets from the same database or from global workflows'},
                event=event
            )

        assetResponse = get_asset(pathParams['databaseId'], pathParams['assetId'])
        logger.info(assetResponse)
        if not bool(assetResponse):
            return validation_error(status_code=404, body={'message': 'Asset does not exist'}, event=event)

        asset = assetResponse[0]
        # Add Casbin Enforcer to check if the current user has permissions to POST the asset (Tier 2):
        asset.update({
            "object__type": "asset"
        })

        executingUserName = ''
        executingRequestContext = event['requestContext']
        asset_allowed = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset, "POST"):
                asset_allowed = True
                executingUserName = claims_and_roles["tokens"][0]

        if not asset_allowed:
            return authorization_error()

        workflowResponse = get_workflow(request_body.get('workflowDatabaseId'), pathParams['workflowId'])
        logger.info(workflowResponse)
        if not bool(workflowResponse):
            return validation_error(status_code=404, body={'message': 'Workflow does not exist'}, event=event)

        workflow = workflowResponse[0]
        # Add Casbin Enforcer to check if the current user has permissions to POST the workflow (Tier 2):
        workflow.update({
            "object__type": "workflow"
        })
        workflow_allowed = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(workflow, "POST"):
                workflow_allowed = True

        if not workflow_allowed:
            return authorization_error()

        (status, pipelineName) = validate_pipelines(workflow, claims_and_roles)
        if not status:
            logger.error("Not all pipelines are enabled/accessible")
            return validation_error(body={'message': 'Pipeline is not enabled/accessible'}, event=event)

        logger.info("All pipelines are enabled. Continuing to run workflow")

        # Determine which file key to use
        asset_file_key = asset['assetLocation']['Key']

        # Get bucket name from bucketId using get_default_bucket_details
        bucketDetails = get_default_bucket_details(asset['bucketId'])
        asset_bucket = bucketDetails['bucketName']

        file_key = asset_file_key
        relative_file_path = None
        if request_body and 'fileKey' in request_body:
            file_key = resolve_asset_file_path(file_key, request_body['fileKey'])
            # Extract relative path for metadata lookup
            relative_file_path = request_body['fileKey']
            logger.info(f"Using file key from request: {file_key}, relative path: {relative_file_path}")
        else:
            logger.info(f"Using asset's base prefix key (no particular file): {file_key}")

        #Get current executions for workflow on asset with file key filter. If currently one running, error.
        executionResults = get_workflow_executions(pathParams['databaseId'], pathParams['assetId'], request_body.get('workflowDatabaseId'), pathParams['workflowId'], file_key)
        if len(executionResults['Items']) > 0:
            logger.error(f"Workflow has a currently running execution on the file: {file_key}")
            return validation_error(body={'message': 'Workflow has a currently running execution on this file'}, event=event)

        # Get separate metadata (asset, file metadata, file attributes) using new metadata service
        metadata_result = get_separate_metadata(pathParams['databaseId'], pathParams['assetId'], relative_file_path, event)

        # Simplify metadata arrays to reduce JSON size for pipeline input
        simplified_asset_metadata = simplify_metadata_array(
            metadata_result.get("assetMetadata", {}).get("metadata", [])
        )
        simplified_file_metadata = simplify_metadata_array(
            metadata_result.get("fileMetadata", {}).get("metadata", [])
        )
        simplified_file_attributes = simplify_metadata_array(
            metadata_result.get("fileAttributes", {}).get("metadata", [])
        )

        logger.info(f"Simplified metadata - Asset: {len(simplified_asset_metadata)} keys, File: {len(simplified_file_metadata)} keys, Attributes: {len(simplified_file_attributes)} keys")

        # Build input metadata structure with simplified format
        inputMetadata = {
            "VAMS": {
                "assetData": {
                    "assetName": asset.get("assetName", ""),
                    "description": asset.get("description", ""),
                    "tags": asset.get("tags", [])
                },
                "assetMetadata": simplified_asset_metadata,
                "fileMetadata": simplified_file_metadata,
                "fileAttributes": simplified_file_attributes
            },
        }

        logger.info("Launching Workflow:")
        executionId = launchWorkflow(asset_bucket, asset_file_key, file_key, workflow['workflow_arn'], pathParams['databaseId'],
                                     pathParams['assetId'], request_body.get('workflowDatabaseId'), workflow['workflowId'],
                                     executingUserName, executingRequestContext, inputMetadata)
        return success(body={'message': executionId})

    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] in ('LimitExceededException', 'ThrottlingException'):
            logger.exception("Throttling Error")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ThrottlingException: Too many requests within a given period.'},
                event=event
            )
        elif err.response['Error']['Code'] == 'ExecutionLimitExceeded':
            logger.exception("ExecutionLimitExceeded")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ExecutionLimitExceeded: Reached the maximum state machine execution limit of 1,000,000'},
                event=event
            )
        else:
            logger.exception(err)
            return internal_error(event=event)
    except Exception as e:
        logger.exception(e)
        return internal_error(event=event)
