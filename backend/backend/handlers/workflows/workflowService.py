#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import (
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse
)

logger = safeLogger(service="WorkflowService")

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
sf_client = boto3.client('stepfunctions')

try:
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    if not workflow_database:
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


def get_all_workflows(queryParams, showDeleted=False, claims_and_roles=None):
    deserializer = TypeDeserializer()
    paginator = dynamodb_client.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if showDeleted:
        operator = "CONTAINS"
    filter = {
        "databaseId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}"
        }
    }
    pageIterator = paginator.paginate(
        TableName=workflow_database,
        ScanFilter=filter,
        PaginationConfig={
            'MaxItems': int(queryParams['maxItems']),
            'PageSize': int(queryParams['pageSize']),
            'StartingToken': queryParams['startingToken']
        }
    ).build_full_result()

    logger.info("Fetching results")
    result = {}
    items = []
    for item in pageIterator['Items']:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}

        # Ensure autoTriggerOnFileExtensionsUpload field exists (return empty string if missing)
        if 'autoTriggerOnFileExtensionsUpload' not in deserialized_document:
            deserialized_document['autoTriggerOnFileExtensionsUpload'] = ''

        # Add Casbin Enforcer to check if the current user has permissions to GET the workflow (Tier 2):
        deserialized_document.update({
            "object__type": "workflow"
        })
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(deserialized_document, "GET"):
                items.append(deserialized_document)

    result['Items'] = items

    if 'NextToken' in pageIterator:
        result['NextToken'] = pageIterator['NextToken']

    return {
        "statusCode": 200,
        "message": result
    }


def get_workflows(databaseId, query_params, showDeleted=False, claims_and_roles=None):
    paginator = dynamodb.meta.client.get_paginator('query')

    if showDeleted:
        databaseId = databaseId + "#deleted"

    page_iterator = paginator.paginate(
        TableName=workflow_database,
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    result = {
        "Items": []
    }

    for item in page_iterator['Items']:
        # Ensure autoTriggerOnFileExtensionsUpload field exists (return empty string if missing)
        if 'autoTriggerOnFileExtensionsUpload' not in item:
            item['autoTriggerOnFileExtensionsUpload'] = ''

        # Add Casbin Enforcer to check if the current user has permissions to GET the workflow (Tier 2):
        item.update({
            "object__type": "workflow"
        })
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(item, "GET"):
                result['Items'].append(item)

    if "NextToken" in page_iterator:
        result["NextToken"] = page_iterator["NextToken"]

    return {
        "statusCode": 200,
        "message": result
    }


def get_workflow(databaseId, workflowId, showDeleted=False, claims_and_roles=None):
    table = dynamodb.Table(workflow_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    db_response = table.get_item(Key={'databaseId': databaseId, 'workflowId': workflowId})
    workflow = db_response.get("Item", {})
    allowed = False

    if workflow:
        # Ensure autoTriggerOnFileExtensionsUpload field exists (return empty string if missing)
        if 'autoTriggerOnFileExtensionsUpload' not in workflow:
            workflow['autoTriggerOnFileExtensionsUpload'] = ''

        # Add Casbin Enforcer to check if the current user has permissions to GET the workflow (Tier 2):
        workflow.update({
            "object__type": "workflow"
        })
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(workflow, "GET"):
                allowed = True

    return {
        "statusCode": 200 if workflow and allowed else 404,
        "message": workflow if allowed else {}
    }


def delete_workflow(databaseId, workflowId, claims_and_roles=None):
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    }
    table = dynamodb.Table(workflow_database)
    if "#deleted" in databaseId:
        return response

    db_response = table.get_item(Key={'databaseId': databaseId, 'workflowId': workflowId})
    workflow = db_response.get('Item', {})
    if workflow:
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the workflow (Tier 2):
        workflow.update({
            "object__type": "workflow"
        })
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(workflow, "DELETE"):
                allowed = True

        if allowed:
            logger.info("Deleting workflow: ")
            logger.info(workflow)
            delete_stepfunction(workflow['workflow_arn'])
            workflow['databaseId'] = databaseId + "#deleted"
            table.put_item(
                Item=workflow
            )
            result = table.delete_item(Key={'databaseId': databaseId, 'workflowId': workflowId})
            logger.info(result)
            response['statusCode'] = 200
            response['message'] = "Workflow deleted"
        else:
            response['statusCode'] = 403
            response['message'] = "Action not allowed"
    return response


def delete_stepfunction(workflowArn):
    logger.info("Deleting StepFunctions: " + workflowArn)
    response = sf_client.delete_state_machine(
        stateMachineArn=workflowArn
    )
    logger.info("StepFunctions Response: ")
    logger.info(response)
    return response


def get_handler(event, path_parameters, query_parameters, show_deleted, claims_and_roles):
    """Handler for GET workflow requests"""
    try:
        if 'workflowId' not in path_parameters:
            if 'databaseId' in path_parameters:
                logger.info("Validating Parameters")
                (valid, message) = validate({
                    'databaseId': {
                        'value': path_parameters['databaseId'],
                        'validator': 'ID',
                        'allowGlobalKeyword': True
                    }
                })
                if not valid:
                    logger.error(message)
                    return validation_error(body={'message': message}, event=event)

                logger.info("Listing Workflows for Database: " + path_parameters['databaseId'])
                result = get_workflows(path_parameters['databaseId'], query_parameters, show_deleted, claims_and_roles)
                return success(body={'message': result['message']})
            else:
                logger.info("Listing All Workflows")
                result = get_all_workflows(query_parameters, show_deleted, claims_and_roles)
                return success(body={'message': result['message']})
        else:
            if 'databaseId' not in path_parameters:
                return validation_error(body={'message': 'No database ID in API Call'}, event=event)

            logger.info("Validating Parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': path_parameters['databaseId'],
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                },
                'workflowId': {
                    'value': path_parameters['workflowId'],
                    'validator': 'ID'
                }
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)

            logger.info("Getting Workflow: " + path_parameters['workflowId'])
            result = get_workflow(path_parameters['databaseId'], path_parameters['workflowId'], show_deleted, claims_and_roles)
            if result['statusCode'] == 200:
                return success(body={'message': result['message']})
            else:
                return validation_error(status_code=result['statusCode'], body={'message': result['message']}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in get_handler: {e}")
        return internal_error(event=event)


def delete_handler(event, path_parameters, claims_and_roles):
    """Handler for DELETE workflow requests"""
    try:
        if 'databaseId' not in path_parameters:
            return validation_error(body={'message': 'No database ID in API Call'}, event=event)
        if 'workflowId' not in path_parameters:
            return validation_error(body={'message': 'No workflow ID in API Call'}, event=event)

        logger.info("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': path_parameters['databaseId'],
                'validator': 'ID',
                'allowGlobalKeyword': True
            },
            'workflowId': {
                'value': path_parameters['workflowId'],
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)

        logger.info("Deleting Workflow: " + path_parameters['workflowId'])
        result = delete_workflow(path_parameters['databaseId'], path_parameters['workflowId'], claims_and_roles)
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


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for workflow service API"""
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

        # Get path and query parameters
        path_parameters = event.get('pathParameters', {})
        query_parameters = event.get('queryStringParameters', {})
        show_deleted = False
        if 'showDeleted' in query_parameters:
            show_deleted = query_parameters['showDeleted']

        validate_pagination_info(query_parameters)

        http_method = event['requestContext']['http']['method']
        logger.info(http_method)

        if http_method == 'GET':
            return get_handler(event, path_parameters, query_parameters, show_deleted, claims_and_roles)
        elif http_method == 'DELETE':
            return delete_handler(event, path_parameters, claims_and_roles)
        else:
            return authorization_error(body={'message': 'Method not allowed'})

    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] in ('LimitExceededException', 'ThrottlingException'):
            logger.exception("Throttling Error")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ThrottlingException: Too many requests within a given period.'},
                event=event
            )
        else:
            logger.exception(err)
            return internal_error(event=event)
    except Exception as e:
        logger.exception(e)
        return internal_error(event=event)
