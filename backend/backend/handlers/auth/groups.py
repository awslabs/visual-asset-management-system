#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
import logging
import os
import traceback
from backend.logging.logger import safeLogger

logger = safeLogger(child=True)

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
table = dynamodb.Table(os.environ['TABLE_NAME'])


def lambda_handler(event, context):

    # read the key from the dynamodb table and return the set of values in a json object
    try:
        response = table.get_item(
            Key={
                'entityType': 'claims',
                'sk': 'observed_claims',
            },
        )
        username_as_group = event['requestContext']['authorizer']['jwt']['claims']['cognito:username']
        addl_groups = [username_as_group, "vams:all_users"]
        if 'Item' not in response or 'claims' not in response['Item']:
            claims = {"claims": addl_groups}
        else:
            claims = {"claims": list(response['Item']['claims']) + addl_groups}

        claims['claims'] = sorted(list(set(claims['claims'])))

        return {
            'statusCode': 200,
            'body': json.dumps(claims)
        }
    except Exception as e:
        logger.error(traceback.format_exc(), event)
        return {
            'statusCode': 500,
            'body': json.dumps(e)
        }
