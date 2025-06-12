#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key, Attr
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from botocore.exceptions import ClientError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType

claims_and_roles = {}
logger = safeLogger(service_name="DownloadAsset")
dynamodb = boto3.resource('dynamodb')

main_rest_response = STANDARD_JSON_RESPONSE
asset_Database = None
asset_bucket_name_default = None
timeout = 1800
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_bucket_name_default = os.environ["S3_ASSET_STORAGE_BUCKET"]
    region = os.environ['AWS_REGION']
    timeout = int(os.environ['CRED_TOKEN_TIMEOUT_SECONDS'])
    #s3Endpoint = os.environ['S3_ENDPOINT']
except:
    logger.exception("Failed Loading Environment Variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})

#s3_config = Config(signature_version='s3v4')
#s3_client = boto3.client('s3', region_name=region, endpoint_url=s3Endpoint, config=s3_config)
s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'})
s3_client = boto3.client('s3', region_name=region, config=s3_config)


def get_Assets(databaseId, assetId):
    #deserializer = TypeDeserializer()
    table = dynamodb.Table(asset_Database)
    db_response = table.query(
        KeyConditionExpression=Key('databaseId').eq(
            databaseId) & Key('assetId').eq(assetId),
        ScanIndexForward=False
    )
    items = []
    for item in db_response['Items']:
        #USE COMMENTED OUT CODE WHEN USING: dynamodb client w/ paginator, not resource w/ table
        # deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}

        # # Add Casbin Enforcer to check if the current user has permissions to GET the asset:
        # deserialized_document.update({
        #     "object__type": "asset"
        # })
        # if len(claims_and_roles["tokens"]) > 0:
        #     casbin_enforcer = CasbinEnforcer(claims_and_roles)
        #     if casbin_enforcer.enforce(deserialized_document, "GET"):
        #         items.append(deserialized_document)

        # Add Casbin Enforcer to check if the current user has permissions to GET the asset:
        item.update({
            "object__type": "asset"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(item, "GET"):
                items.append(item)

    return items


def get_File(databaseId, assetId, key, version):
    items = get_Assets(databaseId, assetId)
    if not items and len(items) == 0:
        return "Error: Asset not found or not authorized to view the assets"
    item = items[0]

    #Override key with data from tables instead if not provided (base asset file)
    if(key is None or key is ""):
        key = item['assetLocation']['Key']

    asset_bucket = item.get('assetLocation', {}).get('Bucket', asset_bucket_name_default)

    isDistributable = item['isDistributable']
    if isinstance(isDistributable, bool):
        if not isDistributable:
            return "Error: Asset not distributable"
    else:
        # invalid type of isDistributable is treated as asset not distributable
        logger.error("isDistributable invalid type")
        return "Error: Asset not distributable"

    #Validate for malicious content type
    if not validateS3AssetExtensionsAndContentType(asset_bucket_name_default, key):
        return "Error: Unallowed file extention or content type in asset file. Unable to download file."

    if version is None or version == "" or (version != "" and (version == "Latest" or version == item['currentVersion']['Version'])):
        return s3_client.generate_presigned_url('get_object', Params={
            'Bucket': asset_bucket,
            'Key': key
        }, ExpiresIn=timeout)
    else:
        # versions = item['versions']
        # for i in versions:
        #     if i['Version'] == version:
        return s3_client.generate_presigned_url('get_object', Params={
            'Bucket': asset_bucket,
            'Key': key,
            'VersionId': version
        }, ExpiresIn=timeout)
        #return "Error: Asset not found or not authorized to view the assets"


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)
    logger.info(event)

    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    pathParameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})

    if 'assetId' not in pathParameters or 'databaseId' not in pathParameters:
        message = "DatabaseId or assetId not in API Call Path"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response
    try:
        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParameters['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': pathParameters['assetId'],
                'validator': 'ASSET_ID'
            },
            'assetPathKey': {
                'value': event['body']['key'],
                'validator': 'ASSET_PATH'
            }
        })
        if not valid:
            logger.exception(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response


        #Split object key by path and return the first value (the asset ID)
        asset_idFromKeyPath = event['body']['key'].split("/")[0]

        #If we are downloading a preview file, go down the path chain by 1
        if(asset_idFromKeyPath == "previews"):
            asset_idFromKeyPath = event['body']['key'].split("/")[1]

        #Check if the asset ID is the same as the asset ID from the path
        if asset_idFromKeyPath != pathParameters['assetId']:
            response['body'] = json.dumps({"message": "Asset ID from path does not match the asset ID"})
            response['statusCode'] = 400
            return response

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            logger.info("Getting Assets")

            key = event['body']['key']

            version = ""
            if 'version' in event['body'] and event['body']['version'] is not None:
                version = event['body']['version']
                logger.info("Version Provided: " + version)

            url = get_File(pathParameters['databaseId'], pathParameters['assetId'], key, version)
            response['statusCode'] = 200

            if url == "Error: Asset not found or not authorized to view the assets":
                response['statusCode'] = 404
            elif url == "Error: Asset not distributable":
                response['statusCode'] = 401
            elif url == "Error: Unallowed file extention or content type in asset file. Unable to download file.":
                response['statusCode'] = 401
            response['body'] = json.dumps({"message": url})

            logger.info(response)
            return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except (ClientError, Exception) as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})

        return response


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
