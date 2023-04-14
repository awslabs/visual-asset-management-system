#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
import logging
import os
import traceback
from backend.logging.logger import safeLogger
from backend.common.dynamodb import to_update_expr

logger = safeLogger(child=True)


region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
table = dynamodb.Table(os.environ['TABLE_NAME'])


def remember_observed_claims(claims: set):
    """add claims to the claims record in dynamodb using ADD in the update expression"""
    values = {
        'claims': claims,
    }
    keys_map, values_map, expr = to_update_expr(values, op="ADD")
    logger.info("updating observed claims with expression, {expr}, values_map, {values_map}, keys_map, {keys_map}".format(
        expr=expr, values_map=values_map, keys_map=keys_map))

    table.update_item(
        Key={
            'entityType': 'claims',
            'sk': 'observed_claims',
        },
        UpdateExpression="ADD #f0 :v0",  # TODO this only works b/c there's a single field in the value
        ExpressionAttributeNames=keys_map,
        ExpressionAttributeValues=values_map,
        ReturnValues="UPDATED_NEW"
    )


# https://docs.aws.amazon.com/cognito/latest/developerguide/role-based-access-control.html


def lambda_handler(event, context):

    logger.info("event: {}".format(event))
    groups = event['request']['userAttributes']['custom:groups']
    claims_to_save = set(groups.strip("[]").split(", "))

    remember_observed_claims(claims_to_save)

    result = {}
    result.update(event)
    result.update({
        "response": {
            "claimsOverrideDetails": {
                "claimsToAddOrOverride": {
                    "vams:roles": "super-admin,pipelines,workflows,assets",
                    "vams:groups": json.dumps(list(claims_to_save))
                }
            }
        }
    })
    return result
