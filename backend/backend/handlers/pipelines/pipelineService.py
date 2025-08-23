#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from botocore.exceptions import ClientError
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info

claims_and_roles = {}
logger = safeLogger(service="PipelineService")

dynamodb = boto3.resource('dynamodb')
dynamodbClient = boto3.client('dynamodb')
lambda_client = boto3.client('lambda')

main_rest_response = STANDARD_JSON_RESPONSE
pipeline_database = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

try:
    pipeline_database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps({"message": "Failed Loading Environment Variables"})


def get_all_pipelines(queryParams, showDeleted=False):
    deserializer = TypeDeserializer()

    paginator = dynamodbClient.get_paginator('scan')
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
        TableName=pipeline_database,
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

        # Add Casbin Enforcer to check if the current user has permissions to GET the pipeline:
        deserialized_document.update({
            "object__type": "pipeline"
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


def get_pipelines(databaseId, query_params, showDeleted=False):
    paginator = dynamodb.meta.client.get_paginator('query')

    if showDeleted:
        databaseId = databaseId + "#deleted"

    page_iterator = paginator.paginate(
        TableName=pipeline_database,
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
        # Add Casbin Enforcer to check if the current user has permissions to GET the pipeline:
        item.update({
            "object__type": "pipeline"
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


def get_pipeline(databaseId, pipelineId, showDeleted=False):
    table = dynamodb.Table(pipeline_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    db_response = table.get_item(Key={'databaseId': databaseId, 'pipelineId': pipelineId})
    pipeline = db_response.get("Item", {})
    allowed = False

    if pipeline:
        # Add Casbin Enforcer to check if the current user has permissions to GET the pipeline:
        pipeline.update({
            "object__type": "pipeline"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(pipeline, "GET"):
                allowed = True

    return {
        "statusCode": 200 if pipeline and allowed else 404,
        "message": pipeline if allowed else {}
    }


def delete_pipeline(databaseId, pipelineId):
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    }
    table = dynamodb.Table(pipeline_database)
    if "#deleted" in databaseId:
        return response
    
    db_response = table.get_item(Key={'databaseId': databaseId, 'pipelineId': pipelineId})
    pipeline = db_response.get("Item", {})

    if pipeline:
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the pipeline:
        pipeline.update({
            "object__type": "pipeline"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(pipeline, "DELETE"):
                allowed = True

        if allowed:
            logger.info("Deleting pipeline: ")
            logger.info(pipeline)
            userResource = json.loads(pipeline['userProvidedResource'])
            if userResource['isProvided'] == False:
                if pipeline['pipelineExecutionType'] == 'Lambda':
                    delete_lambda(userResource['resourceId'])

            pipeline['databaseId'] = databaseId + "#deleted"

            table.put_item(
                Item=pipeline
            )
            result = table.delete_item(Key={'databaseId': databaseId, 'pipelineId': pipelineId})
            logger.info(result)
            response['statusCode'] = 200
            response['message'] = "Pipeline deleted"
        else:
            response['statusCode'] = 403
            response['message'] = "Action not allowed"
    return response


def delete_lambda(functionName):
    logger.info("Deleting lambda: " + functionName)
    try:
        lambda_client.delete_function(
            FunctionName=functionName,
        )
    except Exception as e:
        logger.exception("Failed to delete lambda")
        logger.exception(e)


def get_handler(event, response, pathParameters, queryParameters, showDeleted):
    if 'pipelineId' not in pathParameters:
        if 'databaseId' in pathParameters:
            logger.info("Validating Parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': pathParameters['databaseId'],
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                },
            })
            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response
            logger.info("Listing Pipelines for Database: " + pathParameters['databaseId'])
            result = get_pipelines(pathParameters['databaseId'], queryParameters, showDeleted)
            response['body'] = json.dumps({"message": result['message']})
            response['statusCode'] = result['statusCode']
            logger.info(response)
            return response
        else:
            logger.info("Listing All Pipelines")
            result = get_all_pipelines(queryParameters, showDeleted)
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
            'pipelineId': {
                'value': pathParameters['pipelineId'],
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        logger.info("Getting Pipeline: "+pathParameters['pipelineId'])
        result = get_pipeline(pathParameters['databaseId'], pathParameters['pipelineId'], showDeleted)
        response['body'] = json.dumps({"message": result['message']})
        response['statusCode'] = result['statusCode']
        logger.info(response)
        return response


def delete_handler(event, response, pathParameters, queryParameters):
    logger.info("Validating Parameters")
    for parameter in ['databaseId', 'pipelineId']:
        if parameter not in pathParameters:
            message = f"No {parameter} in API Call"
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response
        (valid, message) = validate({
            parameter: {
                'value': pathParameters[parameter],
                'validator': 'ID',
                'allowGlobalKeyword': True
            }
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

    logger.info("Deleting Pipeline: "+pathParameters['pipelineId'])
    result = delete_pipeline(pathParameters['databaseId'], pathParameters['pipelineId'])
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

    # Enhanced parameter validation for query parameters
    try:
        # Validate showDeleted parameter if present
        showDeleted = False
        if 'showDeleted' in queryParameters:
            show_deleted_value = queryParameters['showDeleted']
            if isinstance(show_deleted_value, str):
                if show_deleted_value.lower() in ['true', '1', 'yes']:
                    showDeleted = True
                elif show_deleted_value.lower() in ['false', '0', 'no']:
                    showDeleted = False
                else:
                    response['statusCode'] = 400
                    response['body'] = json.dumps({"message": "showDeleted parameter must be a valid boolean value (true/false)"})
                    return response
            else:
                showDeleted = bool(show_deleted_value)

        validate_pagination_info(queryParameters)

    except Exception as e:
        logger.exception(f"Error validating query parameters: {e}")
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Invalid query parameters"})
        return response

    try:
        http_method = event['requestContext']['http']['method']
        logger.info(http_method)

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if http_method == 'GET' and method_allowed_on_api:
            return get_handler(event, response, pathParameters, queryParameters, showDeleted)
        elif http_method == 'DELETE' and method_allowed_on_api:
            return delete_handler(event, response, pathParameters, queryParameters)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
