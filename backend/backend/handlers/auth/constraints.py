#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
from handlers.authn import request_to_claims
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

logger = safeLogger(service="constraints")

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
dynamodb_client = boto3.client('dynamodb')

constraintsTableName = os.environ['TABLE_NAME']

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
        constraint = json.loads(event['body'])

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
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    if ('criteriaOr' not in event['body'] and 'criteriaAnd' not in event['body']):
        raise ValidationError(404, "Constraint must include criteriaOr or criteriaAnd statements")
    
    totalCriteria = 0
    if 'criteriaOr' in event['body']:
        totalCriteria += len(event['body']['criteriaOr'])

    if 'criteriaAnd' in event['body']:
        totalCriteria += len(event['body']['criteriaAnd'])

    if (totalCriteria == 0):
        raise ValidationError(404, "Constraint must include criteriaOr or criteriaAnd statements")
    
    if 'criteriaAnd' in event['body']:
        for criteriaAnd in event['body']['criteriaAnd']:
            (valid, message) = validate({
                'criteriaAnd': {
                    'value': criteriaAnd['value'],
                    'validator': 'REGEX'
                }
            })

            if not valid:
                raise ValidationError(400, message)
        
    if 'criteriaOr' in event['body']:
        for criteriaOr in event['body']['criteriaOr']:
            (valid, message) = validate({
                'criteriaOrValue': {
                    'value': criteriaOr['value'],
                    'validator': 'REGEX'
                }
            })

            if not valid:
                raise ValidationError(400, message)
            
    if 'groupPermissions' in event['body']:
        for groupPermission in event['body']['groupPermissions']:
            (valid, message) = validate({
                'roleName': {
                    'value': groupPermission['groupId'],
                    'validator': 'OBJECT_NAME'
                }
            })

            if not valid:
                raise ValidationError(400, message)
            
    if 'userPermissions' in event['body']:
        for userPermission in event['body']['userPermissions']:
            (valid, message) = validate({
                'userId': {
                    'value': userPermission['userId'],
                    'validator': 'EMAIL'
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

    response['body']['constraint'] = constraint


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


        elif 'identifier' in event.get('body', {}):
            constraintId = event.get('body').get('identifier')

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
        http_method = event['requestContext']['http']['method']
        method_allowed_on_api = False
        request_object = {
            "object__type": "api",
            "route__path": event['requestContext']['http']['path']
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", request_object, http_method):
                method_allowed_on_api = True
                break

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
        response['statusCode'] = ex.code
        response['body']['error'] = ex.resp
        response['body'] = json.dumps(response['body'])
        return response

    except Exception as e:
        logger.error(event)
        response['statusCode'] = 500
        response['body']['error'] = "Internal Server Error"
        response['body'] = json.dumps(response['body'])
        return response
