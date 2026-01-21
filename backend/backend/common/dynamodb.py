#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import os
import boto3
from typing import Tuple
from typing import Any
from typing import Dict
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger
from models.common import VAMSGeneralErrorResponse

logger = safeLogger(service_name="DynamoDBCommon")
dynamodb_client = boto3.client('dynamodb')
dynamodb = boto3.resource('dynamodb')

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


def get_asset_object_from_id(databaseId, assetId):
    if not assetId:
        raise VAMSGeneralErrorResponse("Empty assetId or databaseId received")

    try:
        asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    except Exception as e:
        logger.exception("Failed Loading Environment Variables")

    asset_table = dynamodb.Table(asset_table_name)

    if databaseId:
        """Get asset details from DynamoDB"""
        try:
            response = asset_table.query(
                KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId),
                ScanIndexForward=False
            )
            
            if not response.get('Items'):
                return None

            #get first object
            asset_object = response['Items'][0]
            asset_object.update({
                "object__type": "asset"
            })
            return asset_object
        
        except Exception as e:
            logger.exception(f"Error getting asset details: {e}")
            raise VAMSGeneralErrorResponse(f"Error retrieving asset.")
    else:
        #Kept right now for backwards capability until all tables can be updated to use datbaseId/Assetid (comments, subscriptions, asset links)
        filter_expression = f"assetId = :id"
        expression_attribute_values = {f":id": {"S": assetId}}

        items = dynamodb_client.scan(
            TableName=asset_table_name,
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


def validate_pagination_info(queryParameters, defaultMaxItemsOverride=10000, defaultPageSizeOverride=3000):
    """
    Sets the pagination infor from the query parameters
    :param queryParameters: dictionary containing pagination info
    :param defaultMaxItemsOverride: default max items to return, set to 10000 if not set 
    :param defaultPageSizeOverride: default page size to return, set to 3000 if not set 
    """

    if queryParameters is None:
        queryParameters = {}

    if 'pageSize' not in queryParameters:
        queryParameters['pageSize'] = defaultMaxItemsOverride
    else:
        #Check to make sure maxItems is a number, otherwise log and reset to defaultPageSizeOverride
        try:
            int(queryParameters['pageSize'])
        except ValueError:
            queryParameters['pageSize'] = defaultPageSizeOverride
            logger.warn("pageSize parameter is not a number. Re-Setting to defaultPageSizeOverride.")


    if 'maxItems' not in queryParameters:
        #max items should be page count size if not included
        queryParameters['maxItems'] = int(queryParameters['pageSize'])
    else:
        #Check to make sure maxItems is a number, otherwise log and reset to defaultMaxItemsOverride
        try:
            int(queryParameters['maxItems'])
        except ValueError:
            queryParameters['maxItems'] = defaultMaxItemsOverride
            logger.warn("maxItems parameter is not a number. Re-Setting to defaultMaxItemsOverride.")

    #Check min size
    if int(queryParameters['pageSize']) < 1:
        queryParameters['pageSize'] = defaultPageSizeOverride

    if int(queryParameters['maxItems']) < 1:
        queryParameters['maxItems'] = defaultMaxItemsOverride

    #Limit page size to maxitems
    if int(queryParameters['pageSize']) > int(queryParameters['maxItems']):
        queryParameters['pageSize'] = int(queryParameters['maxItems'])
        logger.warn("Data page size exceeds max items, reseting page size to max items. ")

    if 'startingToken' not in queryParameters:
        queryParameters['startingToken'] = None
