# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0


import boto3
import json
import os
from datetime import datetime
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger
from common.validators import validate

# region Logging
logger = safeLogger(service="InitMetadata")
# endregion

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
metadata_table = dynamodb.Table(os.environ['METADATA_STORAGE_TABLE_NAME'])
buckets_table = dynamodb.Table(os.environ['S3_ASSET_BUCKETS_STORAGE_TABLE_NAME'])


def normalize_s3_path(asset_base_key, file_path):
    """
    Intelligently resolve the full S3 key, avoiding duplication if file_path already contains the asset base key.
    
    Args:
        asset_base_key: The base key from assetLocation (e.g., "assetId/" or "custom/path/")
        file_path: The file path from the request (may or may not include the base key)
        
    Returns:
        The properly resolved S3 key without duplication
    """
    # Normalize the asset base key to ensure it ends with '/'
    if asset_base_key and not asset_base_key.endswith('/'):
        asset_base_key = asset_base_key + '/'
    
    # Remove leading slash from file path if present
    if file_path.startswith('/'):
        file_path = file_path[1:]
    
    # Check if file_path already starts with the asset_base_key
    if file_path.startswith(asset_base_key):
        # File path already contains the base key, use as-is
        logger.info(f"File path '{file_path}' already contains base key '{asset_base_key}', using as-is")
        return file_path
    else:
        # File path doesn't contain base key, combine them
        resolved_path = asset_base_key + file_path
        logger.info(f"Combined base key '{asset_base_key}' with file path '{file_path}' to get '{resolved_path}'")
        return resolved_path

def build_response(http_code, body):
    return {
        "headers": {
            # tell cloudfront and api gateway not to cache the response
            "Cache-Control": "no-cache, no-store",
            "Content-Type": "application/json",
        },
        "statusCode": http_code,
        "body": body,
    }

def get_default_bucket_details(bucketId):
    """Get default S3 bucket details from database default bucket DynamoDB"""
    try:

        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucketId),
            Limit=1
        )
        # Use the first item from the query results
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        #Check to make sure we have what we need
        if not bucket_name or not base_assets_prefix:
            raise Exception(f"Error getting database default bucket details: {str(e)}")
        
        #Make sure we end in a slash for the path
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        # Remove leading slash from file path if present
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise Exception(f"Error getting bucket details: {str(e)}")


def to_update_expr(record):
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]

    keys_map = {
        k: key for k, key in zip(keys_attr_names, keys)
    }
    values_map = {
        v1: record[v] for v, v1 in zip(keys, values_attr_names)
    }
    expr = "SET " + ", ".join([
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names)
    ])
    return keys_map, values_map, expr


def create_or_update(databaseId, assetId, metadata):
    metadata['_metadata_last_updated'] = datetime.now().isoformat()
    keys_map, values_map, expr = to_update_expr(metadata)
    return metadata_table.update_item(
        Key={
            "databaseId": databaseId,
            "assetId": assetId,
        },
        ExpressionAttributeNames=keys_map,
        ExpressionAttributeValues=values_map,
        UpdateExpression=expr,
    )


class ValidationError(Exception):
    def __init__(self, code: int, resp: object) -> None:
        self.code = code
        self.resp = resp


def validate_event(event):
    if "pathParameters" not in event \
            or "assetId" not in event['pathParameters']:
        raise ValidationError(404, {"error": "missing asset ID path parameters"})
    if "pathParameters" not in event \
            or "databaseId" not in event['pathParameters']:
        raise ValidationError(404, {"error": "missing database ID path parameters"})
    logger.info("Validating required parameters")
    
    (valid, message) = validate({
        'databaseId': {
            'value': event['pathParameters']['databaseId'],
            'validator': 'ID'
        },
        'assetId': {
            'value': event['pathParameters']['assetId'],
            'validator': 'ASSET_ID'
        },
    })

    if not valid:
        logger.error(message)
        raise ValidationError(400, {"message": message})

    if ('queryStringParameters' in event and 'prefix' in event['queryStringParameters']):
        logger.info("Validating optional parameters")
        (valid, message) = validate({
            'filePathPrefix': {
                'value': event['queryStringParameters']['prefix'],
                'validator': 'RELATIVE_FILE_PATH'
            }
        })

        if not valid:
            logger.error(message)
            raise ValidationError(400, {"message": message})

def validate_body(event):

    if "body" not in event:
        raise ValidationError(400, {"error": "missing request body"})

    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])
    
    body = event['body']

    for req_field in ["metadata", "version"]:
        if req_field not in body:
            raise ValidationError(400, {
                "error": "{f} field is missing".format(f=req_field)
            })

    if body['version'] == "1":
        for k, v in body['metadata'].items():
            if not isinstance(k, str):
                raise ValidationError(400, {
                    "error":
                        "metadata version 1 requires string keys and values"
                })
            if not isinstance(v, str):
                raise ValidationError(400, {
                    "error":
                        "metadata version 1 requires string keys and values"
                })

    return body
