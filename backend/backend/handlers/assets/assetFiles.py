import boto3
import json
import os
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import validate_pagination_info

claims_and_roles = {}
main_rest_response = STANDARD_JSON_RESPONSE

# Create a logger object to log the events
logger = safeLogger(service_name="AssetFiles")

dynamodb_client = boto3.client('dynamodb')
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
paginator = s3_client.get_paginator('list_objects_v2')

asset_database = None
bucket_name = None

try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]

except:
    logger.exception("Failed Loading Environment Variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


asset_table = dynamodb.Table(asset_database)

# A method that takes in s3 path and returns all the files in that path using paginator and s3_client


def get_all_files_in_path(path, primaryKeyPath, query_params):
    result = {
        "Items": []
    }

    for page_iterator in paginator.paginate(
        Bucket=bucket_name,
        Prefix=path,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ):
        for obj in page_iterator.get('Contents', []):
            fileName = os.path.basename(obj['Key'])
            primaryFile = False

            #This is the primary asset file, so label it
            if primaryKeyPath == obj['Key']:
                primaryFile = True

            result["Items"].append({
                'fileName': fileName,
                'key': obj['Key'],
                'relativePath': obj['Key'].removeprefix(path),
                "primary": primaryFile
            })

        if 'NextToken' in page_iterator:
            result['NextToken'] = page_iterator['NextToken']

    # Log the length of files with a description
    logger.info("Files in the path: ")
    logger.info(len(result["Items"]))
    return result

# Check if the assetId is present in the database using asset_table resource
# If it exists return the assetLocation key
# If it does not exist return None

def get_asset(database_id, asset_id):
    db_response = asset_table.get_item(
        Key={
            'databaseId': database_id,
            'assetId': asset_id
        })
    asset = db_response.get('Item', {})
    allowed = False

    if asset:
        # Add Casbin Enforcer to check if the current user has permissions to GET the asset:
        asset.update({
            "object__type": "asset"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset, "GET"):
                allowed = True

    return asset if allowed else {}


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)

    queryParameters = event.get('queryStringParameters', {})
    validate_pagination_info(queryParameters)

    try:


        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            pathParams = event.get('pathParameters', {})
            if 'databaseId' not in pathParams:
                message = "No database ID in API Call"
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                logger.error(response)
                return response

            if 'assetId' not in pathParams:
                message = "No asset ID in API Call"
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                logger.error(response)
                return response

            logger.info("Validating parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': event['pathParameters']['databaseId'],
                    'validator': 'ID'
                },
                'assetId': {
                    'value': event['pathParameters']['assetId'],
                    'validator': 'ID'
                },
            })

            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response


            # get assetId, databaseId from event
            asset_id = event['pathParameters']['assetId']
            database_id = event['pathParameters']['databaseId']

            # log the assetId and databaseId
            logger.info("AssetId: " + asset_id + " DatabaseId: " + database_id)

            # check if assetId exists in database
            asset = get_asset(database_id, asset_id)
            asset_location = asset.get('assetLocation')

            # log the asset_location
            logger.info("AssetLocation: " + str(asset_location))

            # if assetId exists in database
            if asset_location:
                # Get Key from assetLocation dictionary (primary asset file or folder)
                primaryFileKey = asset_location['Key']

                #For now grab all files from the top level asset location
                key = asset_id + "/"

                # get all files in assetLocation
                result = get_all_files_in_path(key, primaryFileKey, queryParameters)
                response['body'] = json.dumps({"message": result})
                response['statusCode'] = 200
                logger.info(response)
                return response
            else:
                # log the assetId and databaseId on a single line and include they don't exist
                logger.info("AssetId: " + asset_id + " DatabaseId: " + database_id + " Asset does not exist")

                response['statusCode'] = 404
                response['body'] = json.dumps({"message":"Asset not found"})
                return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
