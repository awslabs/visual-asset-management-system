import os
import boto3
import json

from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

claims_and_roles = {}
logger = safeLogger(service="RoleService")

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')

main_rest_response = STANDARD_JSON_RESPONSE

try:
    roles_db_table_name = os.environ["ROLES_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body']['message'] = "Failed Loading Environment Variables"


def delete_handler(response, pathParameters, queryParameters):
    role_table = dynamodb.Table(roles_db_table_name)
    role_name = pathParameters.get("roleId")

    if role_name is None or len(role_name) == 0:
        message = "Role Name is required as query parameter."
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response

    #Check param validation
    (valid, message) = validate({
        'roleName': {
            'value': role_name,
            'validator': 'OBJECT_NAME'
        }
    })

    if not valid:
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    if role_name:
        role_object = {'roleName': role_name}
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the Role
        role_object.update({
            "object__type": "role"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(role_object, "DELETE"):
                allowed = True
        if allowed:
            role_table.delete_item(
                Key={
                        'roleName': role_name
                    },
                    ConditionExpression='attribute_exists(roleName)'
                )
            response['statusCode'] = 200
            response['body'] = json.dumps({"message": "success"})
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Action not allowed"})
    return response


def get_roles(query_params):
    deserializer = TypeDeserializer()
    paginator = dynamodb_client.get_paginator('scan')
    page_iterator = paginator.paginate(
        TableName=roles_db_table_name,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    authorized_roles = []
    result = {}
    #logger.info(page_iterator)
    for role in page_iterator['Items']:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in role.items()}

        # Add Casbin Enforcer to check if the current user has permissions to GET the Role
        deserialized_document.update({
            "object__type": "role"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(deserialized_document, "GET"):
                authorized_roles.append(deserialized_document)

    result["Items"] = authorized_roles

    if "NextToken" in page_iterator:
        result["NextToken"] = page_iterator["NextToken"]

    return result


def get_handler(response, pathParameters, queryParameters):
    response['statusCode'] = 200
    response['body'] = json.dumps({"message": get_roles(queryParameters)})
    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    pathParameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})

    try:
        httpMethod = event['requestContext']['http']['method']

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        validate_pagination_info(queryParameters)

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if httpMethod == 'GET' and method_allowed_on_api:
            return get_handler(response, pathParameters, queryParameters)
        elif httpMethod == 'DELETE' and method_allowed_on_api:
            return delete_handler(response, pathParameters, queryParameters)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        logger.exception(e)

        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Provided role doesn't exists in the system."})
            return response

        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
