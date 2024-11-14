#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import boto3
import json
import os
from boto3.dynamodb.conditions import Key
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info

claims_and_roles = {}
s3_client = boto3.client('s3')
dynamo_client = boto3.resource('dynamodb')
logger = safeLogger(service_name="AssetMetadata")

main_rest_response = STANDARD_JSON_RESPONSE

asset_database = None
bucket_name = None

try:
    asset_database = os.environ['ASSET_STORAGE_TABLE_NAME']
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
except:
    logger.exception("Failed loading environment variables")

    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})
    main_rest_response['statusCode'] = 500


def get_asset_path(databaseId, assetId):
    logger.info("Trying to get asset from database")
    table = dynamo_client.Table(asset_database)
    record = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId)
    )
    items = record.get('Items')
    asset = items[0] if items else None
    allowed = False

    if asset:
        # Add Casbin Enforcer to check if the current user has permissions to GET the asset:
        asset.update({
            "object__type": "asset"
        })
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", asset, "GET"):
                allowed = True
                break
        return asset['assetLocation'] if allowed else None
    else:
        return None


def get_headers(key):
    logger.info("Trying to get headers")
    resp = s3_client.select_object_content(
        Bucket=bucket_name,
        Key=key,
        ExpressionType='SQL',
        Expression="SELECT * FROM s3object limit 1",
        InputSerialization={'CSV': {"FileHeaderInfo": "NONE"}},
        OutputSerialization={'CSV': {}},
    )
    records = []
    for event in resp['Payload']:
        if 'Records' in event:
            record = event['Records']['Payload'].decode('utf-8')
            logger.info(record)
            records.append(record)
    return records


def get_records(key):
    logger.info("Trying to get records")
    resp = s3_client.select_object_content(
        Bucket=bucket_name,
        Key=key,
        ExpressionType='SQL',
        Expression="SELECT * FROM s3object limit 100",
        InputSerialization={'CSV': {"FileHeaderInfo": "USE"}},
        OutputSerialization={'CSV': {}},
    )
    records = []
    for event in resp['Payload']:
        if 'Records' in event:
            record = event['Records']['Payload'].decode('utf-8')
            records.append(record)
    return records


def split_records(records):
    result = []
    for record in records:
        rows = record.split("\n")
        for row in rows:
            row = row.split(",")
            result.append(row)
    return result


def get_metadata(databaseId, assetId):
    location = get_asset_path(databaseId, assetId)
    result = {}
    if location:
        headers = get_headers(location['Key'])
        header_records = split_records(headers)
        records = get_records(location['Key'])
        item_records = split_records(records)
        logger.info("Constructing result")
        items = []
        for item in item_records[:-1]:
            items.append(dict(zip(header_records[0], item)))
        result['columns'] = header_records[0]
        result['Items'] = items
    else:
        result['columns'] = ''
        result['Items'] = []
    return result


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)
    pathParams = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})

    validate_pagination_info(queryParameters)

    global claims_and_roles
    claims_and_roles = request_to_claims(event)


    method_allowed_on_api = False
    for user_name in claims_and_roles["tokens"]:
        casbin_enforcer = CasbinEnforcer(user_name)
        if casbin_enforcer.enforceAPI(event):
            method_allowed_on_api = True
            break

    if method_allowed_on_api:
        try:
            if 'assetId' not in pathParams or 'databaseId' not in pathParams:
                logger.error("assetId or databaseId parameter is not present")
                message = "Required parameters not present in the request"
                response['body']['message'] = message
                response['statusCode'] = 400
                return response
            else:
                logger.info("Validating parameters")
                (valid, message) = validate({
                    'databaseId': {
                        'value': pathParams['databaseId'],
                        'validator': 'ID'
                    },
                    'assetId': {
                        'value': pathParams['assetId'],
                        'validator': 'ID'
                    },
                })
                if not valid:
                    logger.error(message)
                    response['body'] = json.dumps({"message": message})
                    response['statusCode'] = 400
                    return response

                logger.info("Fetching metadata")
                result = get_metadata(pathParams['databaseId'], pathParams['assetId'])
                logger.info(result)
                response['body'] = json.dumps({"message": result})
                return response
        except Exception as e:
            response['statusCode'] = 500
            logger.exception(e)
            response['body'] = json.dumps({"message": "Internal Server Error"})

            return response
    else:
        response['statusCode'] = 403
        response['body'] = json.dumps({"message": "Not Authorized"})
        return response
