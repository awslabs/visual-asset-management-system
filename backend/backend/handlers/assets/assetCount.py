#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import boto3
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger

logger = safeLogger(service_name="AssetCount")

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')


def update_asset_count(db_database, asset_database, queryParams, databaseId):
    table = dynamodb.Table(db_database)
    resp = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
    )

    paginator = dynamodb_client.get_paginator('query')
    condition = {
        "databaseId": {
            'AttributeValueList': [ {"S": databaseId} ],
            'ComparisonOperator': 'EQ'
        }
    }
    pageIterator = paginator.paginate(
        TableName=asset_database,
        KeyConditions=condition,
        ScanIndexForward=False,
        PaginationConfig={
            'MaxItems': int(queryParams['maxItems']),
            'PageSize': int(queryParams['pageSize']), 
        #    'StartingToken': queryParams['startingToken']
        }
    ).build_full_result()

    count = pageIterator['Count']
    while 'NextToken' in pageIterator:
        nextToken = pageIterator['NextToken']
        pageIterator = paginator.paginate(
            TableName=asset_database,
            KeyConditions=condition,
            ScanIndexForward=False,
            PaginationConfig={
                'MaxItems': int(queryParams['maxItems']),
                'PageSize': int(queryParams['pageSize']), 
                'StartingToken': nextToken
            }
        ).build_full_result()
        count += pageIterator['Count']

    item = resp['Items'][0]
    item['assetCount'] = str(count)
    logger.info(item)
    table.put_item(Item=item)
    return