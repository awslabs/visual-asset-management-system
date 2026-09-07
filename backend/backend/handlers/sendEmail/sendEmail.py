#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
from botocore.config import Config
import json
import unicodedata
from common.constants import STANDARD_JSON_RESPONSE
from common.resourceNames import get_table_name, ResourceKeys
from customLogging.logger import safeLogger

logger = safeLogger(service="SendEmail")
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb_client = boto3.client('dynamodb', config=retry_config)
sns_client = boto3.client('sns', config=retry_config)

# SNS rejects a Subject carrying a line break or control character, or one that is
# 100 characters or longer, so the line is folded and trimmed before publish.
SNS_SUBJECT_MAX_LENGTH = 99

main_rest_response = copy.deepcopy(STANDARD_JSON_RESPONSE)

try:
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def sanitize_sns_subject(subject):
    """Fold line breaks and control characters to spaces and trim to the SNS Subject bound."""
    folded = ''.join(
        ' ' if unicodedata.category(character) in ('Cc', 'Cf', 'Zl', 'Zp') else character
        for character in subject)
    return folded[:SNS_SUBJECT_MAX_LENGTH].rstrip()


def lambda_handler(event, context):

    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    try:
        assetId = event["assetId"]
        databaseId = event["databaseId"]

        resp = dynamodb_client.query(
            TableName=asset_table_name,
            ProjectionExpression='assetId, assetName, snsTopic, description, currentVersionId',
            KeyConditionExpression='assetId = :asset_id AND databaseId = :database_id',
            ExpressionAttributeValues={
                ':asset_id': {'S': assetId},
                ':database_id': {'S': databaseId}
            },
        )

        items = resp.get('Items', [])
        if items:
            asset_obj = items[0]
            topic_name = asset_obj.get("snsTopic", {}).get("S")
            asset_name = asset_obj.get("assetName", {}).get("S", "")
            currentVersionId = asset_obj.get("currentVersionId", {}).get("S", "")

            # The topic attribute is removed when the asset's subscription is deleted, so
            # there is no topic left to publish to.
            if not topic_name:
                response['statusCode'] = 200
                response['body'] = json.dumps({"message": 'No subscribers to notify'})
                return response

            try:
                message = f'''
    Dear Subscriber,

    We are excited to inform you that a change in a file or asset version of {asset_name} has occured. 

    Current Version Number: {currentVersionId}

    Thank you for staying updated!

    Best Regards,
    VAMS Automated System
    '''
                sns_client.publish(
                    TopicArn=topic_name,
                    Message=message,
                    Subject=sanitize_sns_subject(
                        f'[{asset_name}] - File or Asset Changed ({currentVersionId})')
                )
                response['statusCode'] = 200
                response['body'] = json.dumps({"message": 'Email sent successfully'})
            except Exception as e:
                logger.exception(e)
                response['statusCode'] = 500
                response['body'] = json.dumps({"message": 'Internal Server Error'})
        else:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Asset doesn't exist."})
        return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": 'Internal Server Error'})
        return response
