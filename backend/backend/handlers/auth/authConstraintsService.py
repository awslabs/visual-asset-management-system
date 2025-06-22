#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
from handlers.auth import request_to_claims
import boto3
import os
from customLogging.logger import safeLogger
from common.dynamodb import to_update_expr
from boto3.dynamodb.conditions import Key
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import validate_pagination_info

claims_and_roles = {}

logger = safeLogger(service="AuthConstraintsService")

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
dynamodb_client = boto3.client('dynamodb')

constraintsTableName = os.environ['AUTH_TABLE_NAME']

table = dynamodb.Table(constraintsTableName)

attrs = "name,groupPermissions,constraintId,description,criteriaAnd,criteriaOr,userPermissions,objectType".split(",")
keys_attrs = {"#{f}".format(f=f): f for f in attrs}


class ValidationError(Exception):
    def __init__(self, code: int, resp: object) -> None:
        self.code = code
        self.resp = resp


def get_constraint(event, response):

    key, constraint = get_constraint_from_event(event)

    response['body'] = table.get_item(
        Key=key,
        ExpressionAttributeNames=keys_attrs,
        ProjectionExpression=",".join(keys_attrs.keys()),
    )
    response['body']['constraint'] = response['body']['Item']


def get_constraints(event, response, query_params):
    paginator = dynamodb.meta.client.get_paginator('query')

    #Change KeyCondition for paginiation due to bug: https://github.com/boto/boto3/issues/2300
    page_iterator = paginator.paginate(
        TableName=constraintsTableName,
        ExpressionAttributeNames=keys_attrs,
        ProjectionExpression=",".join(keys_attrs.keys()),
        KeyConditionExpression=Key('entityType').eq('constraint') & Key('sk').begins_with('constraint#'),
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    result = {}
    result['Items'] = page_iterator["Items"]

    if "NextToken" in page_iterator:
        result["NextToken"] = page_iterator["NextToken"]

    response['body'] = {"message": result}

#
# {
#   "identifier": "constraintId",
#   "name": "user defined name",
#   "description": "description",
#   "groupPermissions": [{ ... }]
#   "created": "utc timestamp",
#   "updated": "utc timestamp",
#   "criteria": [
#     {
#       "field": "fieldname",
#       "operator": "contains", # one of contains, does not contain, is one of, is not one of
#       "value": "value" # or ["value", "value"]
#     }
#   ]
# }
#

def get_constraint_from_event(event):
    constraint = None
    if 'body' in event:
        if isinstance(event['body'], str):
            constraint = json.loads(event['body'])
        else:
            constraint = event['body']

    pathParameters = event.get('pathParameters', {})
    if 'constraintId' in pathParameters:
        constraintId = pathParameters['constraintId']
    else:
        constraintId = constraint['identifier']

    key = {
        'entityType': 'constraint',
        'sk': 'constraint#' + constraintId,
    }
    return key, constraint


def update_constraint(event, response):
    key, constraint = get_constraint_from_event(event)
    keys_map, values_map, expr = to_update_expr(constraint)

    logger.info(msg={
        "keys_map": keys_map,
        "values_map": values_map,
        "expr": expr,
    })

    #Do validation checks on constraint inputs
    body = constraint  # Use the constraint object from get_constraint_from_event

    if ('criteriaOr' not in body and 'criteriaAnd' not in body):
        raise ValidationError(404, "Constraint must include criteriaOr or criteriaAnd statements")

    totalCriteria = 0
    if 'criteriaOr' in body:
        totalCriteria += len(body['criteriaOr'])

    if 'criteriaAnd' in body:
        totalCriteria += len(body['criteriaAnd'])

    if (totalCriteria == 0):
        raise ValidationError(404, "Constraint must include criteriaOr or criteriaAnd statements")

    if 'criteriaAnd' in body:
        for criteriaAnd in body['criteriaAnd']:
            (valid, message) = validate({
                'criteriaAnd': {
                    'value': criteriaAnd['value'],
                    'validator': 'REGEX'
                }
            })

            if not valid:
                raise ValidationError(400, message)

    if 'criteriaOr' in body:
        for criteriaOr in body['criteriaOr']:
            (valid, message) = validate({
                'criteriaOrValue': {
                    'value': criteriaOr['value'],
                    'validator': 'REGEX'
                }
            })

            if not valid:
                raise ValidationError(400, message)

    if 'groupPermissions' in body:
        for groupPermission in body['groupPermissions']:
            (valid, message) = validate({
                'roleName': {
                    'value': groupPermission['groupId'],
                    'validator': 'OBJECT_NAME'
                }
            })

            if not valid:
                raise ValidationError(400, message)

    if 'userPermissions' in body:
        for userPermission in body['userPermissions']:
            (valid, message) = validate({
                'userId': {
                    'value': userPermission['userId'],
                    'validator': 'USERID'
                }
            })

            if not valid:
                raise ValidationError(400, message)

    #Conduct final insert/update
    table.update_item(
        Key=key,
        UpdateExpression=expr,
        ExpressionAttributeNames=keys_map,
        ExpressionAttributeValues=values_map,
        ReturnValues="UPDATED_NEW"
    )

    response['body'] = {"message": "Constraint created/updated."}
    response['body']['constraint'] = json.dumps(constraint)


def delete_constraint(event, response):
    key, constraint = get_constraint_from_event(event)
    table.delete_item(
        Key=key
    )
    response['body'] = {"message": "Constraint deleted."}


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    try:

        queryParameters = event.get('queryStringParameters', {})
        validate_pagination_info(queryParameters)

        if 'constraintId' in event.get('pathParameters', {}):
            constraintId = event.get('pathParameters').get('constraintId')

            (valid, message) = validate({
                'constraintId': {
                    'value': constraintId,
                    'validator': 'OBJECT_NAME'
                }
            })

            if not valid:
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response


        elif 'body' in event and isinstance(event['body'], str):
            body = json.loads(event['body'])
            if 'identifier' in body:
                constraintId = body['identifier']
        elif 'body' in event and isinstance(event['body'], dict) and 'identifier' in event['body']:
            constraintId = event['body']['identifier']

            (valid, message) = validate({
                'constraintId': {
                    'value': constraintId,
                    'validator': 'OBJECT_NAME'
                }
            })

            if not valid:
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response


        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            raise ValidationError(403, "Not Authorized")

        method = event['requestContext']['http']['method']
        pathParameters = event.get('pathParameters', {})

        logger.info(event)

        # For GET requests, retrieve the constraints from the table and return them as a json object
        if method == 'GET' and 'constraintId' in pathParameters:
            get_constraint(event, response)

        if method == 'GET' and 'constraintId' not in pathParameters:
            get_constraints(event, response, queryParameters)

        # For POST requests, add the new constraint to the table and return the new constraint as a json object
        if method == 'POST':
            update_constraint(event, response)

        # For DELETE requests, remove the constraint from the table and return the deleted constraint as a json object
        if method == 'DELETE':
            delete_constraint(event, response)

        response['body'] = json.dumps(response['body'])
        return response

    except ValidationError as ex:
        logger.error(ex)
        response['statusCode'] = ex.code
        if isinstance(response['body'], str):
            response['body'] = {}
        response['body']['error'] = ex.resp
        response['body'] = json.dumps(response['body'])
        return response

    except Exception as e:
        logger.error(event)
        logger.error(e)
        response['statusCode'] = 500
        if isinstance(response['body'], str):
            response['body'] = {}
        response['body']['error'] = "Internal Server Error"
        response['body'] = json.dumps(response['body'])
        return response
