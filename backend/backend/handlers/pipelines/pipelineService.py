#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from common.validators import validate
from botocore.exceptions import ClientError
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import (
    APIGatewayProxyResponseV2,
    success,
    validation_error,
    authorization_error,
    internal_error,
    general_error,
    VAMSGeneralErrorResponse,
)
from models.pipelines import PipelineResponseModel, GetPipelinesResponseModel

logger = safeLogger(service_name="PipelineService")

# Configure AWS clients
dynamodb = boto3.resource('dynamodb')
dynamodbClient = boto3.client('dynamodb')
lambda_client = boto3.client('lambda')

# Load environment variables
try:
    pipeline_database = os.environ.get("PIPELINE_STORAGE_TABLE_NAME")
    workflow_database = os.environ.get("WORKFLOW_STORAGE_TABLE_NAME")

    if not pipeline_database:
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


#######################
# Utility Functions
#######################

def _item_to_response_model(item):
    """Convert a DynamoDB item to a PipelineResponseModel"""
    # Parse userProvidedResource if present
    user_resource = {}
    if item.get('userProvidedResource'):
        try:
            user_resource = json.loads(item['userProvidedResource']) if isinstance(item['userProvidedResource'], str) else item['userProvidedResource']
        except (json.JSONDecodeError, TypeError):
            user_resource = {}

    return PipelineResponseModel(
        pipelineId=item.get('pipelineId', ''),
        databaseId=item.get('databaseId'),
        pipelineType=item.get('pipelineType'),
        pipelineExecutionType=item.get('pipelineExecutionType', 'Lambda'),
        description=item.get('description'),
        assetType=item.get('assetType'),
        outputType=item.get('outputType'),
        waitForCallback=item.get('waitForCallback', 'Disabled'),
        taskTimeout=item.get('taskTimeout'),
        taskHeartbeatTimeout=item.get('taskHeartbeatTimeout'),
        userProvidedResource=item.get('userProvidedResource'),
        lambdaName=user_resource.get('resourceId') if user_resource.get('resourceType', 'Lambda') == 'Lambda' else None,
        sqsQueueUrl=user_resource.get('resourceId') if user_resource.get('resourceType') == 'SQS' else None,
        eventBridgeBusArn=user_resource.get('resourceId') if user_resource.get('resourceType') == 'EventBridge' else None,
        eventBridgeSource=user_resource.get('eventSource') if user_resource.get('resourceType') == 'EventBridge' else None,
        eventBridgeDetailType=user_resource.get('eventDetailType') if user_resource.get('resourceType') == 'EventBridge' else None,
        inputParameters=item.get('inputParameters'),
        enabled=item.get('enabled', True),
        dateCreated=item.get('dateCreated'),
        dateUpdated=item.get('dateUpdated'),
    )


def get_all_pipelines(query_params, show_deleted=False, claims_and_roles=None):
    """Get all pipelines across all databases with Casbin filtering"""
    deserializer = TypeDeserializer()

    paginator = dynamodbClient.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if show_deleted:
        operator = "CONTAINS"
    db_filter = {
        "databaseId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}"
        }
    }
    page_iterator = paginator.paginate(
        TableName=pipeline_database,
        ScanFilter=db_filter,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    logger.info("Fetching results")
    items = []
    for item in page_iterator['Items']:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}

        # Tier 2: Object-level Casbin check
        deserialized_document.update({"object__type": "pipeline"})
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(deserialized_document, "GET"):
                items.append(_item_to_response_model(deserialized_document))

    result = GetPipelinesResponseModel(Items=items)

    if 'NextToken' in page_iterator:
        result.NextToken = page_iterator['NextToken']

    return result


def get_pipelines(database_id, query_params, show_deleted=False, claims_and_roles=None):
    """Get all pipelines for a specific database with Casbin filtering"""
    paginator = dynamodb.meta.client.get_paginator('query')

    if show_deleted:
        database_id = database_id + "#deleted"

    page_iterator = paginator.paginate(
        TableName=pipeline_database,
        KeyConditionExpression=Key('databaseId').eq(database_id),
        ScanIndexForward=False,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    items = []
    for item in page_iterator['Items']:
        # Tier 2: Object-level Casbin check
        item.update({"object__type": "pipeline"})
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(item, "GET"):
                items.append(_item_to_response_model(item))

    result = GetPipelinesResponseModel(Items=items)

    if "NextToken" in page_iterator:
        result.NextToken = page_iterator["NextToken"]

    return result


def get_pipeline(database_id, pipeline_id, show_deleted=False, claims_and_roles=None):
    """Get a single pipeline by ID with Casbin check"""
    table = dynamodb.Table(pipeline_database)
    if show_deleted:
        database_id = database_id + "#deleted"
    db_response = table.get_item(Key={'databaseId': database_id, 'pipelineId': pipeline_id})
    pipeline = db_response.get("Item", {})

    if pipeline:
        # Tier 2: Object-level Casbin check
        pipeline.update({"object__type": "pipeline"})
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(pipeline, "GET"):
                return _item_to_response_model(pipeline)

    return None


def delete_lambda(function_name):
    """Delete a Lambda function"""
    logger.info("Deleting lambda: " + function_name)
    try:
        lambda_client.delete_function(FunctionName=function_name)
    except Exception as e:
        logger.exception("Failed to delete lambda")
        logger.exception(e)


def _get_workflows_using_pipeline(database_id: str, pipeline_id: str) -> list:
    """Check if a pipeline is referenced by any active (non-deleted) workflows.

    Args:
        database_id: The database ID to scan workflows for.
        pipeline_id: The pipeline ID to look for.

    Returns:
        List of workflow IDs that reference this pipeline.
    """
    if not workflow_database:
        return []

    workflow_table = dynamodb.Table(workflow_database)
    referencing_workflows = []

    try:
        # Query workflows for this database
        response = workflow_table.query(
            KeyConditionExpression=Key('databaseId').eq(database_id)
        )

        for item in response.get('Items', []):
            specified = item.get('specifiedPipelines', {})
            functions = specified.get('functions', []) if isinstance(specified, dict) else []
            for fn in functions:
                fn_name = fn.get('name', '') if isinstance(fn, dict) else ''
                if fn_name == pipeline_id:
                    referencing_workflows.append(item.get('workflowId', 'unknown'))
                    break

        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = workflow_table.query(
                KeyConditionExpression=Key('databaseId').eq(database_id),
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            for item in response.get('Items', []):
                specified = item.get('specifiedPipelines', {})
                functions = specified.get('functions', []) if isinstance(specified, dict) else []
                for fn in functions:
                    fn_name = fn.get('name', '') if isinstance(fn, dict) else ''
                    if fn_name == pipeline_id:
                        referencing_workflows.append(item.get('workflowId', 'unknown'))
                        break

    except Exception as e:
        logger.warning(f"Error checking workflows for pipeline {pipeline_id}: {e}")

    return referencing_workflows


def _scan_all_workflows_for_pipeline(pipeline_id: str) -> list:
    """Scan all workflows across all databases for references to a GLOBAL pipeline.

    This uses a table scan since GLOBAL pipelines can be referenced by workflows
    in any database.  Only called for GLOBAL pipeline deletions.

    Args:
        pipeline_id: The pipeline ID to search for.

    Returns:
        List of workflow IDs that reference this pipeline.
    """
    if not workflow_database:
        return []

    workflow_table = dynamodb.Table(workflow_database)
    referencing_workflows = []

    try:
        scan_kwargs = {}
        while True:
            response = workflow_table.scan(**scan_kwargs)
            for item in response.get('Items', []):
                # Skip deleted workflows
                if '#deleted' in item.get('databaseId', ''):
                    continue
                specified = item.get('specifiedPipelines', {})
                functions = specified.get('functions', []) if isinstance(specified, dict) else []
                for fn in functions:
                    fn_name = fn.get('name', '') if isinstance(fn, dict) else ''
                    if fn_name == pipeline_id:
                        referencing_workflows.append(item.get('workflowId', 'unknown'))
                        break
            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    except Exception as e:
        logger.warning(f"Error scanning all workflows for pipeline {pipeline_id}: {e}")

    return referencing_workflows


def delete_pipeline(database_id, pipeline_id, claims_and_roles=None):
    """Delete a pipeline, including auto-created Lambda cleanup"""
    table = dynamodb.Table(pipeline_database)

    if "#deleted" in database_id:
        return {'statusCode': 404, 'message': 'Record not found'}

    db_response = table.get_item(Key={'databaseId': database_id, 'pipelineId': pipeline_id})
    pipeline = db_response.get("Item", {})

    if not pipeline:
        return {'statusCode': 404, 'message': 'Record not found'}

    # Check if pipeline is used by any active workflows.
    # For GLOBAL pipelines, workflows in any database may reference them,
    # so we scan all non-deleted workflows.
    referencing_workflows = _get_workflows_using_pipeline(database_id, pipeline_id)
    if database_id == "GLOBAL" and not referencing_workflows:
        referencing_workflows = _scan_all_workflows_for_pipeline(pipeline_id)
    if referencing_workflows:
        workflow_list = ', '.join(referencing_workflows[:5])
        suffix = f" and {len(referencing_workflows) - 5} more" if len(referencing_workflows) > 5 else ""
        return {
            'statusCode': 400,
            'message': f'Cannot delete pipeline. It is currently used by workflow(s): {workflow_list}{suffix}. '
                       f'Remove the pipeline from these workflows before deleting.'
        }

    # Tier 2: Object-level Casbin check
    pipeline.update({"object__type": "pipeline"})
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(pipeline, "DELETE"):
            return {'statusCode': 403, 'message': 'Action not allowed'}

    logger.info("Deleting pipeline: ")
    logger.info(pipeline)

    # Only attempt Lambda deletion for Lambda-type pipelines with auto-created functions
    user_resource = {}
    if pipeline.get('userProvidedResource'):
        try:
            user_resource = json.loads(pipeline['userProvidedResource']) if isinstance(pipeline['userProvidedResource'], str) else pipeline['userProvidedResource']
        except (json.JSONDecodeError, TypeError):
            user_resource = {}

    resource_type = user_resource.get('resourceType', 'Lambda')
    is_provided = user_resource.get('isProvided', True)

    if resource_type == 'Lambda' and not is_provided:
        delete_lambda(user_resource.get('resourceId', ''))

    # Soft-delete: move to #deleted namespace
    pipeline['databaseId'] = database_id + "#deleted"
    table.put_item(Item=pipeline)
    result = table.delete_item(Key={'databaseId': database_id, 'pipelineId': pipeline_id})
    logger.info(result)

    return {'statusCode': 200, 'message': 'Pipeline deleted'}


#######################
# Route Handlers
#######################

def get_handler(event, path_parameters, query_parameters, show_deleted, claims_and_roles):
    """Handler for GET requests - list or single pipeline"""
    try:
        if 'pipelineId' not in path_parameters:
            if 'databaseId' in path_parameters:
                # GET /database/{databaseId}/pipelines
                logger.info("Validating Parameters")
                (valid, message) = validate({
                    'databaseId': {
                        'value': path_parameters['databaseId'],
                        'validator': 'ID',
                        'allowGlobalKeyword': True
                    },
                })
                if not valid:
                    logger.error(message)
                    return validation_error(body={'message': message}, event=event)

                logger.info("Listing Pipelines for Database: " + path_parameters['databaseId'])
                result = get_pipelines(path_parameters['databaseId'], query_parameters, show_deleted, claims_and_roles)
                return success(body={'message': result.dict()})
            else:
                # GET /pipelines (all)
                logger.info("Listing All Pipelines")
                result = get_all_pipelines(query_parameters, show_deleted, claims_and_roles)
                return success(body={'message': result.dict()})
        else:
            # GET /database/{databaseId}/pipelines/{pipelineId}
            if 'databaseId' not in path_parameters:
                return validation_error(body={'message': 'No database ID in API Call'}, event=event)

            logger.info("Validating Parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': path_parameters['databaseId'],
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                },
                'pipelineId': {
                    'value': path_parameters['pipelineId'],
                    'validator': 'ID'
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)

            logger.info("Getting Pipeline: " + path_parameters['pipelineId'])
            pipeline = get_pipeline(path_parameters['databaseId'], path_parameters['pipelineId'], show_deleted, claims_and_roles)
            if pipeline:
                return success(body={'message': pipeline.dict()})
            else:
                return validation_error(status_code=404, body={'message': {}}, event=event)

    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in get_handler: {e}")
        return internal_error(event=event)


def delete_handler(event, path_parameters, claims_and_roles):
    """Handler for DELETE requests"""
    try:
        logger.info("Validating Parameters")
        for parameter in ['databaseId', 'pipelineId']:
            if parameter not in path_parameters:
                return validation_error(body={'message': 'Missing required parameter in API Call'}, event=event)
            (valid, message) = validate({
                parameter: {
                    'value': path_parameters[parameter],
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)

        logger.info("Deleting Pipeline: " + path_parameters['pipelineId'])
        result = delete_pipeline(path_parameters['databaseId'], path_parameters['pipelineId'], claims_and_roles)
        return APIGatewayProxyResponseV2(
            isBase64Encoded=False,
            statusCode=result['statusCode'],
            headers={
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache, no-store',
            },
            body=json.dumps({'message': result['message']})
        )
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in delete_handler: {e}")
        return internal_error(event=event)


#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for pipeline service API"""
    logger.info(event)

    try:
        path_parameters = event.get('pathParameters', {}) or {}
        query_parameters = event.get('queryStringParameters', {}) or {}

        # Parse showDeleted parameter
        show_deleted = False
        if 'showDeleted' in query_parameters:
            show_deleted_value = query_parameters['showDeleted']
            if isinstance(show_deleted_value, str):
                if show_deleted_value.lower() in ['true', '1', 'yes']:
                    show_deleted = True
                elif show_deleted_value.lower() in ['false', '0', 'no']:
                    show_deleted = False
                else:
                    return validation_error(
                        body={'message': 'showDeleted parameter must be a valid boolean value (true/false)'},
                        event=event
                    )
            else:
                show_deleted = bool(show_deleted_value)

        validate_pagination_info(query_parameters)

        http_method = event['requestContext']['http']['method']
        logger.info(http_method)

        # Tier 1: API-level authorization
        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Route to appropriate handler
        if http_method == 'GET':
            return get_handler(event, path_parameters, query_parameters, show_deleted, claims_and_roles)
        elif http_method == 'DELETE':
            return delete_handler(event, path_parameters, claims_and_roles)
        else:
            return authorization_error(body={'message': 'Method not allowed'})

    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return internal_error(event=event)
