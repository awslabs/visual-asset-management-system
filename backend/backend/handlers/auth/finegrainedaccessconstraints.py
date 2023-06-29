#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
from backend.handlers.authn import request_to_claims
import boto3
import logging
import os
import traceback
from backend.logging.logger import safeLogger
from backend.common.dynamodb import to_update_expr
from boto3.dynamodb.conditions import Key, Attr

logger = safeLogger(child=True, service="finegrainedpolicies", level="INFO")

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
table = dynamodb.Table(os.environ['TABLE_NAME'])

attrs = "name,groupPermissions,constraintId,description,criteria".split(",")
keys_attrs = { "#{f}".format(f=f): f for f in attrs }

class ValidationError(Exception):
    def __init__(self, code: int, resp: object) -> None:
        self.code = code
        self.resp = resp

def get_constraint(event, response):

    # db = boto3.client('dynamodb')
    key, constraint = get_constraint_from_event(event)

    response['body'] = table.get_item(
        Key=key,
        ExpressionAttributeNames=keys_attrs,
        ProjectionExpression=",".join(keys_attrs.keys()),
    )
    response['body']['constraint'] = response['body']['Item']

def get_constraints(event, response):
    result = table.query(
        ExpressionAttributeNames=keys_attrs,
        ProjectionExpression=",".join(keys_attrs.keys()),
        KeyConditionExpression=Key('entityType').eq('constraint') & Key('sk').begins_with('constraint#'),
    )
    logger.info(
        msg="ddb response", 
        response=result
    )
    response['body']['constraints'] = result['Items']


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
    response['body'] = { "message": "Constraint deleted." }


def lambda_handler(event, context):

    response = {
        'statusCode': 200,
        'body': {
            "requestid": event['requestContext']['requestId'],
        },
    }

    try:
        claims_and_roles = request_to_claims(event)

        if "super-admin" not in claims_and_roles['roles']:
            raise ValidationError(403, "Not Authorized")

        method = event['requestContext']['http']['method']
        pathParameters = event.get('pathParameters', {})

        # For GET requests, retrieve the constraints from the table and return them as a json object
        if method == 'GET' and 'constraintId' in pathParameters:
            get_constraint(event, response)

        if method == 'GET' and 'constraintId' not in pathParameters:
            get_constraints(event, response)
    
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

    except Exception as ex:
        logger.error(traceback.format_exc(), event)
        response['statusCode'] = 500
        response['body']['error'] = traceback.format_exc()
        response['body'] = json.dumps(response['body'])
        return response
