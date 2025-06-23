#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
from customLogging.logger import safeLogger
from common.dynamodb import to_update_expr
from boto3.dynamodb.conditions import Key

logger = safeLogger(service="PreTokenGen")

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
authEntTable = dynamodb.Table(os.environ['AUTH_TABLE_NAME'])
userRoleTable = dynamodb.Table(os.environ['USER_ROLES_TABLE_NAME'])


def get_vams_roles(event):

    roles = []

    try:
        #Get UserID of what we are creating our PreGen token
        userName = event['userName']

        #Get list of roles from DynamoDB for the particular user 
        response = userRoleTable.query(
            KeyConditionExpression=Key('userId').eq(userName)
            )
        
        if 'Items' not in response or len(response['Items']) == 0:
            logger.warning("No VAMS Role Groups for user")
            return roles
        
        items = response['Items']

        for item in items:
            roles.append(item['roleName'])

        return roles

    except Exception as e:
        logger.exception("VAMS Role Groups were not assigned to user. Error:")
        return roles

def remember_observed_claims(claims: set):
    """add claims to the claims record in
       dynamodb using ADD in the update expression"""
    values = {
        'claims': claims,
    }
    keys_map, values_map, expr = to_update_expr(values, op="ADD")
    # logger.info(
    #     "updating observed claims with expression, {expr}, values_map, {values_map}, keys_map, {keys_map}".format(
    #         expr=expr, values_map=values_map, keys_map=keys_map))

    authEntTable.update_item(
        Key={
            'entityType': 'claims',
            'sk': 'observed_claims',
        },
        # TODO this only works b/c there's a single field in the value
        UpdateExpression="ADD #f0 :v0",
        ExpressionAttributeNames=keys_map,
        ExpressionAttributeValues=values_map,
        ReturnValues="UPDATED_NEW"
    )


def lambda_handler(event, context):

    logger.info(event)
    claims_to_save = set()

    #get VAMS roles for user
    roles = get_vams_roles(event)
    claims_to_save = set(roles)
    
    logger.info(event['userName'] + " assigned to user roles")
    logger.info(roles)

    #Save roles as token claims if we have any
    if len(roles) > 0:
        remember_observed_claims(claims_to_save)

    try:
        email = event['request']['userAttributes']['email']
    except Exception as e:
        logger.warning("Email not found in userAttributes")
        email = ""

    result = {}
    result.update(event)
    result.update({
        "response": {
            "claimsAndScopeOverrideDetails": {
                "idTokenGeneration": {
                    "claimsToAddOrOverride": {
                        "vams:externalAttributes": json.dumps([]), #TODO: Future use to add external system user attributes to claims that can be incorporated into ABAC system constraints
                        "vams:roles": json.dumps(roles),
                        "vams:tokens": (
                            json.dumps([event['userName']])
                        ),
                        "email": email
                    }
                },
                "accessTokenGeneration": {
                    "claimsToAddOrOverride": {
                        "vams:externalAttributes": json.dumps([]), #TODO: Future use to add external system user attributes to claims that can be incorporated into ABAC system constraints
                        "vams:roles": json.dumps(roles),
                        "vams:tokens": (
                            json.dumps([event['userName']])
                        ),
                        "email": email
                    }
                }
            },
        }
    })

    logger.info(result)
    return result
