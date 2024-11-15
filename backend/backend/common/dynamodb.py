#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import os
import boto3
from typing import Tuple
from typing import Any
from typing import Dict
from customLogging.logger import safeLogger
from models.common import VAMSGeneralErrorResponse

logger = safeLogger(service_name="DynamoDBCommon")
dynamodb_client = boto3.client('dynamodb')

def to_update_expr(record, op="SET") -> Tuple[Dict[str, str], Dict[str, Any], str]:
    """
    :param record:
    :param op:
    :return:
    """
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]

    keys_map = {
        k: key
        for k, key in zip(keys_attr_names, keys)
    }
    values_map = {
        v1: record[v]
        for v, v1 in zip(keys, values_attr_names)
    }
    expr = "{op} ".format(op=op) + ", ".join([
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names)
    ])
    return keys_map, values_map, expr


def get_asset_object_from_id(asset_id):
    if not asset_id:
        raise VAMSGeneralErrorResponse("Empty assetId received")

    try:
        asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    except Exception as e:
        logger.exception("Failed Loading Environment Variables")

    filter_expression = f"assetId = :id"
    expression_attribute_values = {f":id": {"S": asset_id}}

    items = dynamodb_client.scan(
        TableName=asset_table_name,
        ProjectionExpression='assetId, assetName, databaseId, assetType, tags',
        FilterExpression=filter_expression,
        ExpressionAttributeValues=expression_attribute_values,
    )
    logger.info("Scanned Asset Item:")
    logger.info(items)
    item = items.get("Items", [])[0] if items.get("Items", []) else None

    asset_object = {
        "object__type": "asset",
        "assetId": item['assetId']['S'] if item else None,
        "assetName": item['assetName']['S'] if item else None,
        "databaseId": item['databaseId']['S'] if item else None,
        "assetType": item['assetType']['S'] if item else None,
        "tags": [tag['S'] for tag in item['tags']['L']] if item else None
    }
    return asset_object


def validate_pagination_info(queryParameters, defaultMaxItemsOverride=1000):
    """
    Sets the pagination infor from the query parameters
    :param queryParameters: dictionary containing pagination info
    :param defaultMaxItemsOverride: default max items to return, set to 1000 if not set 
    """

    if queryParameters is None:
        queryParameters = {}

    if 'maxItems' not in queryParameters:
        queryParameters['maxItems'] = defaultMaxItemsOverride
        queryParameters['pageSize'] = defaultMaxItemsOverride
    else:
        #Check to make sure maxItems is a number, otherwise log and reset to 100
        try:
            float(queryParameters['maxItems'])
        except ValueError:
            queryParameters['maxItems'] = 100
            logger.warn("maxItems parameter is not a number. Re-Setting to 100.")

        #Set pageSize equal to maxItems
        queryParameters['pageSize'] = queryParameters['maxItems']

    if 'startingToken' not in queryParameters:
        queryParameters['startingToken'] = None

    #Limit page size
    if queryParameters['maxItems'] > defaultMaxItemsOverride:
        queryParameters['maxItems'] = defaultMaxItemsOverride
        queryParameters['pageSize'] = defaultMaxItemsOverride
        logger.warn("Data page size requested exceeds "+str(defaultMaxItemsOverride)+" records. Limiting to "+str(defaultMaxItemsOverride)+". ")
