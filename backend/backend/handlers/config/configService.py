#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from boto3.dynamodb.types import TypeDeserializer
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger

logger = safeLogger(service="ConfigService")
dynamo_client = boto3.client('dynamodb')
deserializer = TypeDeserializer()


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    try:
        logger.info("Looking up the requested resource")
        assetS3Bucket = os.getenv("ASSET_STORAGE_BUCKET", None)
        appFeatureEnabledDynamoDBTable = os.getenv("APPFEATUREENABLED_STORAGE_TABLE_NAME", None)

        # Specify the column name you want to aggregate
        appFeatureEnableDynamoDB_feature_column_name = 'featureName'

        # Initialize an empty list to store column values
        appFeatureEnableDynamoDB_column_values = []

        logger.info("Scanning and paginating the table")
        paginator = dynamo_client.get_paginator('scan')
        pageIterator = paginator.paginate(
            TableName=appFeatureEnabledDynamoDBTable,
            PaginationConfig={
                'MaxItems': 500,
                'PageSize': 500,
                'StartingToken': None
            }
        ).build_full_result()

        pageIteratorItems = []
        pageIteratorItems.extend(pageIterator['Items'])

        while 'NextToken' in pageIterator:
            nextToken = pageIterator['NextToken']
            pageIterator = paginator.paginate(
                TableName=appFeatureEnabledDynamoDBTable,
                PaginationConfig={
                    'MaxItems': 500,
                    'PageSize': 500,
                    'StartingToken': nextToken
                }
            ).build_full_result()
            pageIteratorItems.extend(pageIterator['Items'])
        
        logger.info("Constructing results")
        result = {}
        items = []
        for item in pageIteratorItems:
            deserialized_document = {
                k: deserializer.deserialize(v) for k, v in item.items()}
            items.append(deserialized_document)
        result['Items'] = items

        for item in items:
            appFeatureEnableDynamoDB_column_values.append(
                item[appFeatureEnableDynamoDB_feature_column_name])

        logger.info(appFeatureEnableDynamoDB_column_values)

        # Create a concatenated string from the column values
        appFeatureEnabledconcatenated_string = ','.join(
            appFeatureEnableDynamoDB_column_values)

        response = {
            "bucket": assetS3Bucket,
            "featuresEnabled": appFeatureEnabledconcatenated_string,
        }
        logger.info("Success")
        return {
            "statusCode": "200",
            "body": json.dumps(response),
            "headers": {
                "Content-Type": "application/json",
            },
        }
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
