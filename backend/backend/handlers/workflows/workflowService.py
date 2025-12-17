#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from common.validators import validate
from common.constants import STANDARD_JSON_RESPONSE
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info

claims_and_roles = {}
logger = safeLogger(service="WorkflowService")

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
sf_client = boto3.client('stepfunctions')
main_rest_response = STANDARD_JSON_RESPONSE
workflow_database = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

try:
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps({"message": "Failed Loading Environment Variables"})


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

        # Add Casbin Enforcer to check if the current user has permissions to GET the workflow:
        deserialized_document.update({
            "object__type": "workflow"
        })
        if len(claims_and_roles["tokens"]) > 0:
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
        
        # Add Casbin Enforcer to check if the current user has permissions to GET the workflow:
        item.update({
            "object__type": "workflow"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(item, "GET"):
                result['Items'].append(item)

    if "NextToken" in page_iterator:
        result["NextToken"] = page_iterator["NextToken"]

    return {
        "statusCode": 200,
        "message": result
    }


def get_workflow(databaseId, workflowId, showDeleted=False):
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
        
        # Add Casbin Enforcer to check if the current user has permissions to GET the workflow:
        workflow.update({
            "object__type": "workflow"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(workflow, "GET"):
                allowed = True

    return {
        "statusCode": 200 if workflow and allowed else 404,
        "message": workflow if allowed else {}
    }


def delete_workflow(databaseId, workflowId):
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
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the workflow:
        workflow.update({
            "object__type": "workflow"
        })
        if len(claims_and_roles["tokens"]) > 0:
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

    logger.info("Deleting StepFunctions: "+workflowArn)
    response = sf_client.delete_state_machine(
        stateMachineArn=workflowArn
    )
    logger.info("StepFunctions Response: ")
    logger.info(response)

    return response

def get_handler(event, response, pathParameters, queryParameters, showDeleted):
    if 'workflowId' not in pathParameters:
        if 'databaseId' in pathParameters:

            logger.info("Validating Parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': pathParameters['databaseId'],
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                }
            })

            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            logger.info("Listing Workflows for Database: "+pathParameters['databaseId'])
            result = get_workflows(pathParameters['databaseId'], queryParameters, showDeleted)
            response['body'] = json.dumps({"message": result['message']})
            response['statusCode'] = result['statusCode']
            logger.info(response)
            return response
        else:
            logger.info("Listing All Workflows")
            result = get_all_workflows(queryParameters, showDeleted)
            response['body'] = json.dumps({"message": result['message']})
            response['statusCode'] = result['statusCode']
            logger.info(response)
            return response
    else:
        if 'databaseId' not in pathParameters:
            message = "No database ID in API Call"
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response

        logger.info("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParameters['databaseId'],
                'validator': 'ID',
                'allowGlobalKeyword': True
            },
            'workflowId': {
                'value': pathParameters['workflowId'],
                'validator': 'ID'
            }
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        logger.info("Getting Workflow: "+pathParameters['workflowId'])
        result = get_workflow(pathParameters['databaseId'], pathParameters['workflowId'], showDeleted)
        response['body'] = json.dumps({"message": result['message']})
        response['statusCode'] = result['statusCode']
        logger.info(response)
        return response


def delete_handler(event, response, pathParameters):
    if 'databaseId' not in pathParameters:
        message = "No database ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response
    if 'workflowId' not in pathParameters:
        message = "No workflow ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    logger.info("Validating Parameters")
    (valid, message) = validate({
        'databaseId': {
            'value': pathParameters['databaseId'],
            'validator': 'ID',
            'allowGlobalKeyword': True
        },
        'workflowId': {
            'value': pathParameters['workflowId'],
            'validator': 'ID'
        }
    })

    if not valid:
        logger.error(message)
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    logger.info("Deleting Workflow: "+pathParameters['workflowId'])
    result = delete_workflow(pathParameters['databaseId'], pathParameters['workflowId'])
    response['body'] = json.dumps({"message": result['message']})
    response['statusCode'] = result['statusCode']
    logger.info(response)
    return response

def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)
    logger.info(event)
    pathParameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})
    showDeleted = False
    if 'showDeleted' in queryParameters:
        showDeleted = queryParameters['showDeleted']

    validate_pagination_info(queryParameters) 

    try:
        httpMethod = event['requestContext']['http']['method']
        logger.info(httpMethod)

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if httpMethod == 'GET' and method_allowed_on_api:
            return get_handler(event, response, pathParameters, queryParameters, showDeleted)
        if httpMethod == 'DELETE' and method_allowed_on_api:
            return delete_handler(event, response, pathParameters)
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


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
