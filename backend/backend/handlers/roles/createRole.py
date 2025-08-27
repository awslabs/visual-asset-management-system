import os
import boto3
import json
import uuid
import datetime
from botocore.exceptions import ClientError

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
    roles_db_table_name = os.environ["ROLES_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body']['message'] = "Failed Loading Environment Variables"


def create_role(body):
    response = STANDARD_JSON_RESPONSE
    role_table = dynamodb.Table(roles_db_table_name)

    try:
        item = {
            "id": str(uuid.uuid4()),
            'roleName': body["roleName"],
            'description': body["description"],
            'createdOn': str(datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
            'source': body.get("source"),
            'sourceIdentifier': body.get("sourceIdentifier"),
            'mfaRequired': body.get("mfaRequired", False)
        }
        role_table.put_item(Item=item, ConditionExpression='attribute_not_exists(roleName)')

        response['statusCode'] = 200
        response['body'] = json.dumps({"message": "success"})
        return response
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Role with name '" + body["roleName"] + "' already exists."})
        elif error_code == 'ValidationException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Invalid request parameters."})
        else:
            logger.exception(f"DynamoDB ClientError: {error_code}")
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
    except Exception as e:
        logger.exception(f"Unexpected error in create_role: {e}")
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response


def update_role(body):
    response = STANDARD_JSON_RESPONSE
    role_table = dynamodb.Table(roles_db_table_name)

    try:
        role_table.update_item(
            Key={
                'roleName': body["roleName"]
            },
            UpdateExpression='SET description = :desc, #source = :source, sourceIdentifier = :sourceIdentifier, mfaRequired = :mfaRequired',
            ExpressionAttributeNames={
                '#source': 'source'
            },
            ExpressionAttributeValues={
                ':desc': body["description"],
                ':source': body.get("source"),
                ':sourceIdentifier': body.get("sourceIdentifier"),
                ':mfaRequired': body.get("mfaRequired", False)
            },
            ConditionExpression='attribute_exists(roleName)'
        )
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "RoleName " + body["roleName"] + " doesn't exist."})
        elif error_code == 'ValidationException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Invalid request parameters."})
        else:
            logger.exception(f"DynamoDB ClientError: {error_code}")
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
    except Exception as e:
        logger.exception(f"Unexpected error in update_role: {e}")
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE

    # Parse request body
    if not event.get('body'):
        message = 'Request body is required'
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    if isinstance(event['body'], str):
        try:
            event['body'] = json.loads(event['body'])
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Invalid JSON in request body"})
            return response

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
            },
            'source': {
                'value': event['body'].get('source'),
                'validator': 'STRING_256',
                'optional': True
            },
            'sourceIdentifier': {
                'value': event['body'].get('sourceIdentifier'),
                'validator': 'STRING_256',
                'optional': True
            },
            'mfaRequired': {
                'value': str(event['body'].get("mfaRequired", "False")),
                'validator': 'BOOL'
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
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(role_object, httpMethod) and casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

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
