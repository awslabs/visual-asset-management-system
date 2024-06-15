#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger

logger = safeLogger(service="SendEmail")
dynamodb_client = boto3.client('dynamodb')
sns_client = boto3.client('sns')

main_rest_response = STANDARD_JSON_RESPONSE

try:
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def lambda_handler(event, context):

    response = STANDARD_JSON_RESPONSE
    try:
        resp = dynamodb_client.scan(
            TableName=asset_table_name,
            ProjectionExpression='assetId, assetName, snsTopic, description, currentVersion',
            FilterExpression='assetId = :asset_id',
            ExpressionAttributeValues={':asset_id': {'S': event["asset_id"]}},
        )

        items = resp.get('Items', [])
        if items:
            asset_obj = items[0]
            topic_name = asset_obj.get("snsTopic").get("S")
            asset_name = asset_obj.get("assetName").get("S")
            version = asset_obj.get("currentVersion").get("M").get("Version").get("S")
            release_date = asset_obj.get("currentVersion").get("M").get("DateModified").get("S")

            try:
                message = f'''
    Dear Subscriber,

    We are excited to inform you that a new version of {asset_name} is now available. Below are some details about the latest version:

    Version Number: {version}
    Release Date: {release_date}

    Thank you for staying updated!

    Best Regards,
    VAMS Automated System
    '''
                sns_client.publish(
                    TopicArn=topic_name,
                    Message=message,
                    Subject=f'[{asset_name}] - New Version {version} Available'
                )
                response['statusCode'] = 200
                response['body'] = json.dumps({"message": 'Email sent successfully'})
            except Exception as e:
                logger.exception(e)
                response['statusCode'] = 500
                response['body'] = json.dumps({"message": 'Internal Server Error'})
        else:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": f"Asset - {event['asset_id']} doesn't exits."})
        return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": 'Internal Server Error'})
