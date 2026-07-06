#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import boto3
import botocore
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import (
    APIGatewayProxyResponseV2,
    commonHeaders,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse
)

logger = safeLogger(service="WorkflowService")

# Claims/roles for the current request (set per-invocation in lambda_handler).
claims_and_roles = {}

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
sf_client = boto3.client('stepfunctions')

try:
    workflow_database = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e


def get_all_workflows(queryParams, showDeleted=False):
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

    return success(body={'message': result})


def get_workflows(databaseId, query_params, showDeleted=False):
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

    return success(body={'message': result})


def get_workflow(event, databaseId, workflowId, showDeleted=False):
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

    # A missing OR unauthorized workflow returns 404 with an empty body, preserving the
    # prior behavior where the absence and the no-permission cases are indistinguishable.
    if workflow and allowed:
        return success(body={'message': workflow})
    return validation_error(status_code=404, body={'message': {}}, event=event)


def delete_workflow(event, databaseId, workflowId):
    table = dynamodb.Table(workflow_database)
    if "#deleted" in databaseId:
        return validation_error(status_code=404, body={'message': 'Record not found'}, event=event)

    db_response = table.get_item(Key={'databaseId': databaseId, 'workflowId': workflowId})
    workflow = db_response.get('Item', {})
    if not workflow:
        return validation_error(status_code=404, body={'message': 'Record not found'}, event=event)

    allowed = False
    # Add Casbin Enforcer to check if the current user has permissions to DELETE the workflow (Tier 2):
    workflow.update({
        "object__type": "workflow"
    })
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(workflow, "DELETE"):
            allowed = True

    if not allowed:
        return authorization_error(body={'message': 'Action not allowed'})

    logger.info("Deleting workflow: ")
    logger.info(workflow)
    delete_stepfunction(workflow['workflow_arn'])
    workflow['databaseId'] = databaseId + "#deleted"
    table.put_item(Item=workflow)
    result = table.delete_item(Key={'databaseId': databaseId, 'workflowId': workflowId})
    logger.info(result)
    return success(body={'message': "Workflow deleted"})


def delete_stepfunction(workflowArn):
    logger.info("Deleting StepFunctions: " + workflowArn)
    response = sf_client.delete_state_machine(
        stateMachineArn=workflowArn
    )
    logger.info("StepFunctions Response: ")
    logger.info(response)
    return response


def handle_get_request(event, path_parameters, query_parameters, show_deleted):
    """Route GET requests: a workflowId lists one workflow; a databaseId (without
    workflowId) lists that database's workflows; neither lists all workflows."""
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
            return get_workflows(path_parameters['databaseId'], query_parameters, show_deleted)
        else:
            logger.info("Listing All Workflows")
            return get_all_workflows(query_parameters, show_deleted)
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
        return get_workflow(event, path_parameters['databaseId'], path_parameters['workflowId'], show_deleted)


def handle_delete_request(event, path_parameters):
    """Validate the path parameters and delete the workflow + its state machine."""
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
    return delete_workflow(event, path_parameters['databaseId'], path_parameters['workflowId'])


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for the workflow service API (GET list/get, DELETE)."""
    global claims_and_roles
    logger.info(event)
    claims_and_roles = request_to_claims(event)

    try:
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
            return handle_get_request(event, path_parameters, query_parameters, show_deleted)
        elif http_method == 'DELETE':
            return handle_delete_request(event, path_parameters)
        else:
            return authorization_error(body={'message': 'Method not allowed'})

    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
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
