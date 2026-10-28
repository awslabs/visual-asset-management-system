#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger

logger = safeLogger(service_name="AssetCount")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)


def update_asset_count(db_database, asset_database, queryParams, databaseId):
    # Count the database's live assets with Select='COUNT', which reads no item attributes back,
    # following LastEvaluatedKey until the partition is exhausted (each page reads up to 1 MB).
    # queryParams is accepted for existing callers and not used.
    query_kwargs = {
        'TableName': asset_database,
        'KeyConditions': {
            "databaseId": {
                'AttributeValueList': [{"S": databaseId}],
                'ComparisonOperator': 'EQ'
            }
        },
        'Select': 'COUNT',
    }
    count = 0
    while True:
        response = dynamodb_client.query(**query_kwargs)
        count += response.get('Count', 0)
        if 'LastEvaluatedKey' not in response:
            break
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

    # Only assetCount is written, so a database edit committed while counting is not reverted, and
    # only while the record exists, so a deleted database is not recreated as a live record.
    table = dynamodb.Table(db_database)
    try:
        table.update_item(
            Key={'databaseId': databaseId},
            UpdateExpression='SET #assetCount = :assetCount',
            ExpressionAttributeNames={'#assetCount': 'assetCount'},
            ExpressionAttributeValues={':assetCount': str(count)},
            ConditionExpression='attribute_exists(databaseId)'
        )
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            logger.warning(f"Database {databaseId} no longer exists, asset count not updated")
            return
        raise
    logger.info(f"Asset count for database {databaseId} set to {count}")
    return
