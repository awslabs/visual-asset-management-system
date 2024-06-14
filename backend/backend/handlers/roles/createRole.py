import os
import boto3
import json
import uuid
import datetime

from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}

dynamodb = boto3.resource('dynamodb')
logger = safeLogger(service="CreateRole")

main_rest_response = STANDARD_JSON_RESPONSE

try:
    roles_db_table_name = os.environ["ROLES_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body']['message'] = "Failed Loading Environment Variables"


def create_role(body):
    response = STANDARD_JSON_RESPONSE
    role_table = dynamodb.Table(roles_db_table_name)
    item = {
        "id": str(uuid.uuid4()),
        'roleName': body["roleName"],
        'description': body["description"],
        'createdOn': str(datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
        'source': body.get("source"),
        'sourceIdentifier': body.get("sourceIdentifier")
    }
    role_table.put_item(Item=item, ConditionExpression='attribute_not_exists(roleName)')

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def update_role(body):
    response = STANDARD_JSON_RESPONSE
    role_table = dynamodb.Table(roles_db_table_name)
    try:
        role_table.update_item(
            Key={
                'roleName': body["roleName"]
            },
            UpdateExpression='SET description = :desc, sourceIdentifier = :sourceIdentifier',
            ExpressionAttributeValues={
                ':desc': body["description"],
                ':sourceIdentifier': body.get("sourceIdentifier")
            },
            ConditionExpression='attribute_exists(roleName)'
        )
    except Exception as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException' or e.response['Error']['Code'] == 'TransactionCanceledException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "RoleName "+ body["roleName"] +" doesn't exists."})
        else:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    try:
        if 'roleName' not in event['body'] or 'description' not in event['body']:
            message = "roleName and description are required."
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        (valid, message) = validate({
            'roleName': {
                'value': event['body']['roleName'],
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': event['body']['description'],
                'validator': 'STRING_256'
            }
        })

        if not valid:
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        httpMethod = event['requestContext']['http']['method']
        method_allowed_on_api = False

        # Add Casbin Enforcer to check if the current user has permissions to POST/PUT the Tag
        role_object = {
            "object__type": "role",
            "roleName": event['body']['roleName']
        }
        request_object = {
            "object__type": "api",
            "route__path": event['requestContext']['http']['path']
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", role_object, httpMethod) and casbin_enforcer.enforce(f"user::{user_name}", request_object, httpMethod):
                method_allowed_on_api = True
                break

        if httpMethod == 'POST' and method_allowed_on_api:
            return create_role(event['body'])
        elif httpMethod == 'PUT' and method_allowed_on_api:
            return update_role(event['body'])
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        logger.exception("Error")
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
