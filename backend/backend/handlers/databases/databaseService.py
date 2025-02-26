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
main_rest_response = STANDARD_JSON_RESPONSE
db_database = None
deserializer = TypeDeserializer()

try:
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    pipeline_database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
except Exception as e:
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
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", database, "GET"):
                allowed = True
                break

    return database if allowed else {}


def get_databases(query_params, show_deleted=False):
    db = boto3.client('dynamodb')

    paginator = db.get_paginator('scan')
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
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", deserialized_document, "GET"):
                items.append(deserialized_document)
                break

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

    table = dynamodb.Table(db_database)
    if "#deleted" in database_id:
        return result

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
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", database, "DELETE"):
                allowed = True
                break

        if allowed:
            logger.info("Deleting database: ")
            logger.info(database)
            database['databaseId'] = database_id + "#deleted"
            table.put_item(
                Item=database
            )
            result = table.delete_item(Key={'databaseId': database_id})
            logger.info(result)
            result['statusCode'] = 200
            result['message'] = "Database deleted"
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

    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {})

    validate_pagination_info(query_parameters)

    show_deleted = False
    if 'showDeleted' in query_parameters:
        show_deleted = query_parameters['showDeleted']

    try:
        http_method = event['requestContext']['http']['method']
        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        method_allowed_on_api = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
                break

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
