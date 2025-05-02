#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info

claims_and_roles = {}
logger = safeLogger(service="DatabaseService")

dynamodb = boto3.resource('dynamodb')
dbClient = boto3.client('dynamodb')
main_rest_response = STANDARD_JSON_RESPONSE
deserializer = TypeDeserializer()

# Initialize database table names
db_database = os.environ.get("DATABASE_STORAGE_TABLE_NAME", "test-database-table")
workflow_database = os.environ.get("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
pipeline_database = os.environ.get("PIPELINE_STORAGE_TABLE_NAME", "test-pipeline-table")
asset_database = os.environ.get("ASSET_STORAGE_TABLE_NAME", "test-asset-table")

if not all([db_database, workflow_database, pipeline_database, asset_database]):
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def check_workflows(database_id):
    table = dynamodb.Table(workflow_database)
    db_response = table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id),
        ScanIndexForward=False,
        Limit=1
    )

    return db_response['Count'] > 0


def check_pipelines(database_id):
    table = dynamodb.Table(pipeline_database)
    db_response = table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id),
        ScanIndexForward=False,
        Limit=1
    )

    return db_response['Count'] > 0


def check_assets(database_id):
    table = dynamodb.Table(asset_database)
    db_response = table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id),
        ScanIndexForward=False,
        Limit=1
    )

    return db_response['Count'] > 0


def get_handler(event, response, path_parameters, query_parameters, show_deleted):
    if 'databaseId' not in path_parameters:
        response['body'] = json.dumps({"message": get_databases(query_parameters, show_deleted)})
        response['statusCode'] = 200
        logger.info(response)
        return response
    else:
        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': path_parameters['databaseId'],
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        response['body'] = json.dumps({"message": get_database(path_parameters['databaseId'], show_deleted)})
        response['statusCode'] = 200
        logger.info(response)
        return response


def get_database(database_id, show_deleted=False):
    table = dynamodb.Table(db_database)
    if show_deleted:
        database_id = database_id + "#deleted"

    db_response = table.get_item(
        Key={
            'databaseId': database_id
        }
    )

    database = db_response.get("Item", {})
    allowed = False

    if database:
        # Add Casbin Enforcer to check if the current user has permissions to GET the database:
        database.update({
            "object__type": "database"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(database, "GET"):
                allowed = True

    return database if allowed else {}


def get_databases(query_params, show_deleted=False):
    
    paginator = dbClient.get_paginator('scan')
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
        TableName=db_database,
        ScanFilter=db_filter,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    result = {}
    items = []
    for item in page_iterator['Items']:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}

        # Add Casbin Enforcer to check if the current user has permissions to GET the database:
        deserialized_document.update({
            "object__type": "database"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(deserialized_document, "GET"):
                items.append(deserialized_document)

    result['Items'] = items

    if 'NextToken' in page_iterator:
        result['NextToken'] = page_iterator['NextToken']
    return result


def delete_handler(event, response, path_parameters, query_parameters):
    if 'databaseId' not in path_parameters:
        message = "No database ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response
    else:
        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': path_parameters['databaseId'],
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        logger.info("Deleting Database")
        result = delete_database(path_parameters['databaseId'])
        response['body'] = json.dumps({"message": result['message']})
        response['statusCode'] = result['statusCode']
        logger.info(response)
        return response


def delete_database(database_id):
    result = {
        'statusCode': 404,
        'message': 'Record not found'
    }

    if "#deleted" in database_id:
        return result

    # Check for active workflows, pipelines, and assets before accessing the table
    if check_workflows(database_id):
        result['statusCode'] = 400
        result['message'] = "Database contains active workflows"
        return result
    if check_pipelines(database_id):
        result['statusCode'] = 400
        result['message'] = "Database contains active pipelines"
        return result
    if check_assets(database_id):
        result['statusCode'] = 400
        result['message'] = "Database contains active assets"
        return result

    # Only create the table reference if we've passed all the checks
    table = dynamodb.Table(db_database)

    db_response = table.get_item(
        Key={
            'databaseId': database_id
        }
    )
    database = db_response.get("Item", {})

    if database:
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the database:
        database.update({
            "object__type": "database"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(database, "DELETE"):
                allowed = True

        if allowed:
            logger.info("Deleting database: ")
            logger.info(database)
            database['databaseId'] = database_id + "#deleted"
            table.put_item(
                Item=database
            )
            delete_result = table.delete_item(Key={'databaseId': database_id})
            logger.info(delete_result)
            result = {
                'statusCode': 200,
                'message': "Database deleted"
            }
        else:
            result['statusCode'] = 403
            result['message'] = "Action not allowed"
    else:
        result['statusCode'] = 404
        result['message'] = "Record not found"

    return result


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

    try:
        path_parameters = event.get('pathParameters', {})
        query_parameters = event.get('queryStringParameters', {})

        validate_pagination_info(query_parameters)

        show_deleted = False
        if 'showDeleted' in query_parameters:
            show_deleted = query_parameters['showDeleted']

        http_method = event['requestContext']['http']['method']
        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if http_method == 'GET' and method_allowed_on_api:
            return get_handler(event, response, path_parameters, query_parameters, show_deleted)
        elif http_method == 'DELETE' and method_allowed_on_api:
            return delete_handler(event, response, path_parameters, query_parameters)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
