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

    assetId = event["assetId"]
    databaseId = event["databaseId"]

    response = STANDARD_JSON_RESPONSE
    try:
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
            topic_name = asset_obj.get("snsTopic").get("S")
            asset_name = asset_obj.get("assetName").get("S")
            currentVersionId = asset_obj.get("currentVersionId").get("S")

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
                    Subject=f'[{asset_name}] - File or Asset Changed ({currentVersionId})'
                )
                response['statusCode'] = 200
                response['body'] = json.dumps({"message": 'Email sent successfully'})
            except Exception as e:
                logger.exception(e)
                response['statusCode'] = 500
                response['body'] = json.dumps({"message": 'Internal Server Error'})
        else:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": f"Asset - {event['assetId']} with database ID - {event['databaseId']} doesn't exist."})
        return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": 'Internal Server Error'})
