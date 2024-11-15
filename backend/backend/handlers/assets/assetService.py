#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import botocore.exceptions
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.assets.assetCount import update_asset_count
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info

claims_and_roles = {}

logger = safeLogger(service_name="AssetService")
main_rest_response = STANDARD_JSON_RESPONSE
dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
asset_database = None
db_database = None
s3_assetVisualizer_bucket = None
bucket_name = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
    s3_assetVisualizer_bucket = os.environ["S3_ASSET_VISUALIZER_BUCKET"]

except:
    logger.exception("Failed Loading Environment Variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def get_all_assets(event, query_params, show_deleted=False):
    deserializer = TypeDeserializer()

    paginator = dynamodb_client.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if show_deleted:
        operator = "CONTAINS"
    filter = {
        "databaseId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}"
        }
    }

    page_iterator = paginator.paginate(
        TableName=asset_database,
        ScanFilter=filter,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    logger.info("Fetching results")
    result = {}
    items = []
    for item in page_iterator['Items']:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}

        # Add Casbin Enforcer to check if the current user has permissions to GET the asset:
        deserialized_document.update({
            "object__type": "asset"
        })
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", deserialized_document, "GET"):
                items.append(deserialized_document)
                break

    result['Items'] = items

    if 'NextToken' in page_iterator:
        result['NextToken'] = page_iterator['NextToken']
    return result


def get_assets(databaseId, query_params, showDeleted=False):
    paginator = dynamodb.meta.client.get_paginator('query')

    if showDeleted:
        databaseId = databaseId + "#deleted"

    page_iterator = paginator.paginate(
        TableName=asset_database,
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    logger.info("Fetching results")
    result = {}
    items = []
    for item in page_iterator['Items']:

        # Add Casbin Enforcer to check if the current user has permissions to GET the asset:
        item.update({
            "object__type": "asset"
        })
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", item, "GET"):
                items.append(item)
                break

    result["Items"] = items

    if "NextToken" in page_iterator:
        result["NextToken"] = page_iterator["NextToken"]
    return result


def get_asset(databaseId, assetId, showDeleted=False):
    #Get single asset - no pagination needed
    table = dynamodb.Table(asset_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.get_item(Key={'databaseId': databaseId, 'assetId': assetId})
    asset = response.get('Item', {})
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

    return asset if allowed else {}


def delete_asset(databaseId, assetId, queryParameters):
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    }
    table = dynamodb.Table(asset_database)
    if "#deleted" in databaseId:
        return response

    db_response = table.get_item(Key={'databaseId': databaseId, 'assetId': assetId})
    item = db_response.get('Item', {})

    if item:
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the asset:
        item.update({
            "object__type": "asset"
        })
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", item, "DELETE"):
                allowed = True
                break

        if allowed:
            logger.info("Deleting asset: ")
            logger.info(item)
            if "assetLocation" in item:
                if item['isMultiFile']:
                    archive_multi_file(item['assetLocation'], databaseId, assetId)
                    delete_assetVisualizer_files(item['assetLocation'])
                else:
                    archive_file(item['assetLocation'], databaseId, assetId)
                    delete_assetVisualizer_files(item['assetLocation'])
            if "previewLocation" in item:
                archive_file(item['previewLocation'], databaseId, assetId)
            item['databaseId'] = databaseId + "#deleted"
            table.put_item(
                Item=item
            )
            result = table.delete_item(Key={'databaseId': databaseId, 'assetId': assetId})
            # update assetCount after successful deletion of an asset
            update_asset_count(db_database, asset_database, queryParameters, databaseId)
            logger.info(result)
            response['statusCode'] = 200
            response['message'] = "Asset deleted"
        else:
            response['statusCode'] = 403
            response['message'] = "Action not allowed"
    else:
        response['statusCode'] = 404
        response['message'] = "Record not found"
    return response


def archive_multi_file(location, databaseId, assetId):
    s3 = boto3.client('s3')
    prefix = ""
    if "Key" in location:
        prefix = location['Key']
    if len(prefix) == 0:
        return
    logger.info('Archiving folder with multiple files')

    paginator = s3.get_paginator('list_objects_v2')
    files = []
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get('Contents', []):
            files.append(obj['Key'])

    for key in files:
        try:
            response = move_to_glacier_and_mark_deleted(key, databaseId, assetId)
            logger.info("S3 response: ")
            logger.info(response)

        except s3.exceptions.InvalidObjectState as ios:
            logger.exception("S3 object already archived: " + key)
            logger.exception(ios)

        except botocore.exceptions.ClientError as e:
            # TODO: Most likely an error when the key doesnt exist
            logger.exception(e)

    return


def archive_file(location, databaseId, assetId):
    s3 = boto3.client('s3')
    key = ""
    if "Key" in location:
        key = location['Key']

    if len(key) == 0:
        return
    logger.info("Archiving item: " + bucket_name +":" + key)

    try:
        response = move_to_glacier_and_mark_deleted(key, databaseId, assetId)
        logger.info("S3 response: ")
        logger.info(response)

    except s3.exceptions.InvalidObjectState as ios:
        logger.exception("S3 object already archived: "+ key)
        logger.exception(ios)

    except botocore.exceptions.ClientError as e:
        # TODO: Most likely an error when the key doesnt exist
        logger.exception(e)
    return


def move_to_glacier_and_mark_deleted(key, assetId, databaseId):
    s3 = boto3.client('s3')
    return s3.copy_object(
        CopySource={
            "Bucket": bucket_name,
            "Key": key,
        },
        Bucket=bucket_name,
        Key=key,
        MetadataDirective='REPLACE',
        Metadata={
            "assetid": assetId,
            "databaseid": databaseId,
            "vams-status": "deleted",
        },
        StorageClass='GLACIER',
    )


def get_handler(event, response, path_parameters, query_parameters):
    show_deleted = False

    if 'showDeleted' in query_parameters:
        show_deleted = query_parameters['showDeleted']

    if 'assetId' not in path_parameters:
        if 'databaseId' in path_parameters:
            logger.info("Validating parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': path_parameters['databaseId'],
                    'validator': 'ID'
                }
            })
            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            logger.info("Listing Assets for Database: " + path_parameters['databaseId'])
            response['body'] = json.dumps({"message": get_assets(path_parameters['databaseId'], query_parameters, show_deleted)})
            logger.info(response)
            return response
        else:
            logger.info("Listing All Assets")
            response['body'] = json.dumps({"message": get_all_assets(event, query_parameters, show_deleted)})
            logger.info(response)
            return response
    else:
        if 'databaseId' not in path_parameters:
            message = "No database ID in API Call"
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response

        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': path_parameters['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_parameters['assetId'],
                'validator': 'ID'
            },
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        logger.info("Getting Asset: " + path_parameters['assetId'])
        response['body'] = json.dumps({"message": get_asset(
            path_parameters['databaseId'], path_parameters['assetId'], show_deleted)})
        logger.info(response)
        return response


def delete_handler(response, pathParameters, queryParameters):
    if 'databaseId' not in pathParameters:
        message = "No database ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response
    if 'assetId' not in pathParameters:
        message = "No asset ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    logger.info("Validating parameters")
    (valid, message) = validate({
        'databaseId': {
            'value': pathParameters['databaseId'],
            'validator': 'ID'
        },
        'assetId': {
            'value': pathParameters['assetId'],
            'validator': 'ID'
        },
    })
    if not valid:
        logger.error(message)
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    logger.info("Deleting Asset: " + pathParameters['assetId'])
    result = delete_asset(pathParameters['databaseId'], pathParameters['assetId'], queryParameters)
    response['body'] = json.dumps({"message": result['message']})
    response['statusCode'] = result['statusCode']
    logger.info(response)
    return response


def delete_assetVisualizer_files(assetLocation):
    s3 = boto3.client('s3')

    key = ""
    if "Key" in assetLocation:
        key = assetLocation['Key']

    if len(key) == 0:
        return

    # Add the folder deliminiator to the end of the key
    key = key + '/'

    logger.info("Deleting Temporary Asset Visualizer Files Under Folder: " + s3_assetVisualizer_bucket + ":"+ key)

    try:
        # Get all assets in assetVisualizer bucket (unversioned, temporary files for the web visualizers) for deletion
        # Use assetLocation key as root folder key for assetVisualizerFiles
        assetVisualizerBucketFilesDeleted = []
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=s3_assetVisualizer_bucket, Prefix=key):
            for item in page['Contents']:
                assetVisualizerBucketFilesDeleted.append(item['Key'])
                logger.info("Deleting visualizer asset file: " + item['Key'])
                s3.delete_object(Bucket=s3_assetVisualizer_bucket, Key=item['Key'])

    except Exception as e:
        logger.exception(e)

    return


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)
    pathParameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})

    validate_pagination_info(queryParameters)

    try:
        httpMethod = event['requestContext']['http']['method']
        logger.info(httpMethod)

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        method_allowed_on_api = False
        request_object = {
            "object__type": "api",
            "route__path": event['requestContext']['http']['path']
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", request_object, httpMethod):
                method_allowed_on_api = True
                break

        if httpMethod == 'GET' and method_allowed_on_api:
            return get_handler(event, response, pathParameters, queryParameters)
        elif httpMethod == 'DELETE' and method_allowed_on_api:
            return delete_handler(response, pathParameters, queryParameters)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
