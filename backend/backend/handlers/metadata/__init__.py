# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0


import boto3
import json
import logging
import os
import traceback

# region Logging

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logger = logging.getLogger()

if logger.hasHandlers():
    # The Lambda environment pre-configures a handler logging to stderr. If a handler is already configured,
    # `.basicConfig` does not execute. Thus we set the level directly.
    logger.setLevel(LOG_LEVEL)
else:
    logging.basicConfig(level=LOG_LEVEL)

# endregion

def mask_sensitive_data(event):
    # remove sensitive data from request object before logging
    keys_to_redact = ["authorization"]
    result = {}
    for k, v in event.items():
        if isinstance(v, dict):
            result[k] = mask_sensitive_data(v)
        elif k in keys_to_redact:
            result[k] = "<redacted>"
        else:
            result[k] = v
    return result;

def build_response(http_code, body):
    return {
        "headers": {
            "Cache-Control": "no-cache, no-store", # tell cloudfront and api gateway not to cache the response
            "Content-Type": "application/json",
        },
        "statusCode": http_code,
        "body": body,
    }


region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', region_name=region)
table = dynamodb.Table(os.environ['METADATA_STORAGE_TABLE_NAME'])

def to_update_expr(record):
    
    keys= record.keys()
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
    expr = "SET " + ", ".join([
        "{f} = {v}".format(f=f, v=v) 
            for f,v in zip(keys_attr_names, values_attr_names)
    ])
    return keys_map, values_map, expr



def create_or_update(databaseId, assetId, metadata):
    keys_map, values_map, expr = to_update_expr(metadata)
    return table.update_item(
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
    if "pathParameters" not in event or "assetId" not in event['pathParameters']:
        raise ValidationError(404, { "error": "missing path parameters"})
    if "pathParameters" not in event or "databaseId" not in event['pathParameters']:
        raise ValidationError(404, { "error": "missing path parameters"})


def validate_body(event):

    if "body" not in event:
        raise ValidationError(400, {"error": "missing request body"})

    body = json.loads(event['body'])

    for req_field in ["metadata", "version"]:
        if req_field not in body:
            raise ValidationError(400, {"error": "{f} field is missing".format(f=req_field)})

    if body['version'] == "1":
        for k, v in body['metadata'].items():
            if not isinstance(k, str):
                raise ValidationError(400, {"error": "metadata version 1 requires string keys and values"})
            if not isinstance(v, str):
                raise ValidationError(400, {"error": "metadata version 1 requires string keys and values"})
    
    return body
