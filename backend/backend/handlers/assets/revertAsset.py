#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import datetime
import json
import os

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service_name="RevertAsset")

# Set boto environment variable to use regional STS endpoint
# (https://stackoverflow.com/questions/71255594/request-times-out-when-try-to-assume-a-role-with-aws-sts-from-a-private-subnet-u)
# AWS_STS_REGIONAL_ENDPOINTS='regional'
os.environ["AWS_STS_REGIONAL_ENDPOINTS"] = 'regional'

dynamodb = boto3.resource('dynamodb')
s3c = boto3.client('s3')
main_rest_response = STANDARD_JSON_RESPONSE
newObject = {
    "databaseId": "",
    "assetId": "",
    "description": "No Description",
    "assetType": "We will put the extension here",
    "assetLocation": {
        "Key": "key"
    },
    "previewLocation": {
        "Key": "key"
    },
    "authEdit": [],
    "isDistributable": False,
    "currentVersion": {
        "Comment": "",
        "Version": "",
        "S3Version": "",
        "DateModified": "",
        "FileSize": ""
    },
    "versions": [
    ],
    "objectFamily": {
        "Parent": {
        },
        "Children": [
        ]
    },
    "specifiedPipelines": []
}

unitTest = {
    "body": {
        "databaseId": "Unit_Test",
        "assetId": "Unit_Test",  # Editable
        "key": "",
        "assetType": "",
        "description": "Testing as Usual",  # Editable
        "specifiedPipelines": [],  # will develop a query to list pipelines that can act as tags.
        "isDistributable": False,  # Editable
        "Comment": "Unit Test",  # Editable
        "previewLocation": {
            "Key": ""
        }
    }
}
unitTest['body'] = json.dumps(unitTest['body'])


asset_Database = None
db_Database = None
bucket_name = None

try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def _deserialize(raw_data):
    result = {}
    if not raw_data:
        return result

    deserializer = TypeDeserializer()

    for key, val in raw_data.items():
        result[key] = deserializer.deserialize(val)

    return result


def get_account_id():
    client = boto3.client('sts')
    return client.get_caller_identity()["Account"]

# Note
# (from https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.copy_object)
# You can store individual objects of up to 5 TB in Amazon S3.
# You create a copy of your object up to 5 GB in size in a single
# atomic action using this API. However, to copy an object greater
# than 5 GB, you must use the multipart upload. Upload Part -
# Copy (UploadPartCopy) API. For more information, see Copy Object
# Using the REST Multipart Upload API.


def copy_object_and_return_new_version(key, asset):
    # VersionId and ContentLength (bytes)
    copy_source = {
        'Bucket': bucket_name,
        'Key': key,
        'VersionId': asset['currentVersion']['S3Version']
    }
    account_id = get_account_id()
    resp = s3c.copy_object(
        Bucket=bucket_name,
        CopySource=copy_source,
        Key=key,
        ExpectedBucketOwner=account_id,
        ExpectedSourceBucketOwner=account_id,
    )
    asset['currentVersion']['S3Version'] = resp['VersionId']
    return asset


def assetReversion(item, version):
    asset = item
    logger.info("Asset: ")
    logger.info(asset)
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    prevVersions = asset['versions']
    cV = asset['currentVersion']
    cV['previewLocation'] = {
        "Key": asset['previewLocation']['Key']
    }
    prevVersions.append(cV)
    asset['versions'] = prevVersions
    for i in prevVersions:
        if i["Version"] == version:
            asset['currentVersion'] = i
            break

    key = asset['assetLocation']['Key']
    asset = copy_object_and_return_new_version(key, asset)
    return asset


def revert_Asset(databaseId, assetId, version):
    table = dynamodb.Table(asset_Database)
    try:
        db_response = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                databaseId) & Key('assetId').eq(assetId),
            ScanIndexForward=False,
        )

        items = db_response.get('Items')
        asset = items[0] if items else None
        allowed = False

        if asset:
            # Add Casbin Enforcer to check if the current user has permissions to DELETE (revert) the asset:
            asset.update({
                "object__type": "asset"
            })
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset, "DELETE"):
                    allowed = True
            if allowed:
                up = assetReversion(asset, version)
                table.put_item(Item=up)
                logger.info('Revert Asset ' + json.dumps(up))
                return json.dumps({"message": "Succeeded"})
            else:
                return json.dumps({"message": "Asset doesn't exist"})
        else:
            return json.dumps({"message": "Asset doesn't exist"})
    except Exception as e:
        logger.exception(e)
        return json.dumps({"message": "Internal Server Error"})


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)
    logger.info(event)

    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    pathParams = event.get('pathParameters', {})
    logger.info(pathParams)
    if 'databaseId' not in pathParams:
        message = "No database ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response
    databaseId = pathParams['databaseId']

    if 'assetId' not in pathParams:
        message = "No assetId ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response
    assetId = pathParams['assetId']

    if 'version' not in event['body']:
        message = "No version in API Call"
        response['body'] = json.dumps({'message': message})
        response['statusCode'] = 400
        logger.error(message)
        return response
    version = event['body']['version']

    try:
        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParams['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': pathParams['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response


        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            logger.info("Trying to get Data")
            response['body'] = revert_Asset(databaseId, assetId, version)
            logger.info(response)
            return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
