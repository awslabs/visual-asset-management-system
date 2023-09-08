#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import traceback
from backend.logging.logger import safeLogger
from backend.common.dynamodb import to_update_expr

logger = safeLogger(child=True)


region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
table = dynamodb.Table(os.environ['TABLE_NAME'])


def get_groups(event):
    return event.get(
        "request", {}).get(
        "groupConfiguration", {}).get(
        "groupsToOverride", [])


def determine_vams_roles(event):
    """determine the VAMS roles for a user based on their Cognito group list"""

    # Default set of roles
    roles = ["pipelines", "workflows", "assets"]
    try:
        cognito_groups = get_groups(event)
        if "super-admin" in cognito_groups:
            roles.append("super-admin")

        # Example: grant access to pipelines and workflows when the user is in
        # the group "pipelines-workflows".
        #
        # if "pipelines-workflows" in cognito_groups:
        #     roles.append("pipelines")
        #     roles.append("workflows")
        #
        # Note: remove "pipelines" and "workflows" from the initial list above.

        return roles

    except Exception as ex:
        logger.warn("groups were not assigned to user",
                    traceback.format_exc(ex))
        return roles


def remember_observed_claims(claims: set):
    """add claims to the claims record in
       dynamodb using ADD in the update expression"""
    values = {
        'claims': claims,
    }
    keys_map, values_map, expr = to_update_expr(values, op="ADD")
    logger.info(
        "updating observed claims with expression, {expr}, "
        "values_map, {values_map}, keys_map, {keys_map}".format(
            expr=expr, values_map=values_map, keys_map=keys_map))

    table.update_item(
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


# https://docs.aws.amazon.com/cognito/latest/developerguide/role-based-access-control.html

def parse_group_list(group_str: str):
    """parse a group list from a Cognito user"""
    return set(group_str.strip("[]").split(", ") + ["vams:all_users"])


def lambda_handler(event, context):

    logger.info("logger event: {}", event)
    print("event", event)
    claims_to_save = set()
    if 'custom:groups' in event['request']['userAttributes']:
        groups = event['request']['userAttributes']['custom:groups']
        claims_to_save = parse_group_list(groups)
        claims_to_save = claims_to_save | set(get_groups(event))
        remember_observed_claims(claims_to_save)
    else:
        claims_to_save = set(get_groups(event))
        if len(claims_to_save) > 0:
            remember_observed_claims(claims_to_save)

    roles = determine_vams_roles(event)

    result = {}
    result.update(event)
    result.update({
        "response": {
            "claimsOverrideDetails": {
                "claimsToAddOrOverride": {
                    "vams:roles": json.dumps(roles),
                    "vams:tokens": (
                        json.dumps(
                            list(claims_to_save) + [
                                "vams:all_users",
                                event['userName']
                            ])
                    )
                }
            },
        }
    })

    print("result", result)

    return result
