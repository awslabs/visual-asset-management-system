#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import base64
import sys
from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType

# Standardized retry configuration merged with existing S3 config
s3_config = Config(
    signature_version='s3v4', 
    s3={'addressing_style': 'path'},
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

s3_client = boto3.client('s3', config=s3_config)
dynamodb = boto3.resource('dynamodb', config=s3_config)
logger = safeLogger(service_name="StreamAsset")

try:
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    auth_table_name = os.environ["AUTH_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
    roles_table_name = os.environ["ROLES_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
buckets_table = dynamodb.Table(s3_asset_buckets_table_name)
asset_table = dynamodb.Table(asset_storage_table_name)
auth_table = dynamodb.Table(auth_table_name)
user_roles_table = dynamodb.Table(user_roles_table_name)
roles_table = dynamodb.Table(roles_table_name)

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
            raise Exception(f"Error getting database default bucket details")
        
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

def get_asset_details(databaseId, assetId):
    """Get asset details from DynamoDB"""
    try:
        response = asset_table.query(
            KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId),
            ScanIndexForward=False
        )
        
        if not response.get('Items'):
            return None
            
        # Return the first (most recent) item
        return response['Items'][0]
    except Exception as e:
        logger.exception(f"Error getting asset details: {e}")
        raise Exception(f"Error retrieving asset: {str(e)}")

def resolve_asset_file_path(asset_base_key: str, file_path: str) -> str:
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

def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    #logger.info(str(event))

    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    # Get the request headers from the API Gateway event
    try:
        request_headers = event['headers']
    except:
        request_headers = ""

    # Get the "Range" header from the request headers
    try:
        range_header = request_headers.get('range')
    except:
        range_header = ""

    path_parameters = event.get('pathParameters', {})

    # Get the object key which comes after the base path of the API Call
    assetId = path_parameters.get('assetId', "") 
    databaseId = path_parameters.get('databaseId', "") 
    object_key = path_parameters.get('proxy', "")  

    # Error if no object key in path
    if not object_key or object_key == None or object_key == "":
        message = "No Asset File Object Key Provided in Path"
        error_response = {
            'statusCode': 400,
            'body': json.dumps({"message": message}),
            'headers': {
                'Access-Control-Allow-Headers': 'Range',
            }
        }
        logger.error(error_response)
        return error_response

    #If object_key doesn't start with a /, add it
    if not object_key.startswith('/'):
        object_key = '/' + object_key

    logger.info("Validating parameters")
    (valid, message) = validate({
        'databaseId': {
            'value': databaseId,
            'validator': 'ID'
        },
        'assetId': {
            'value': assetId,
            'validator': 'ASSET_ID'
        },
        'assetFilePathKey': {
            'value': object_key,
            'validator': 'RELATIVE_FILE_PATH'
        },
    })
    if not valid:
        logger.error(message)
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    http_method = "GET"
    operation_allowed_on_asset = False

    # Get asset details and check if it exists
    asset_object = get_asset_details(databaseId, assetId)
    if not asset_object:
        message = f"Asset {assetId} not found in database {databaseId}"
        error_response = {
            'statusCode': 404,
            'body': json.dumps({"message": message}),
            'headers': {
                'Access-Control-Allow-Headers': 'Range',
            }
        }
        logger.error(error_response)
        return error_response

    # Check if asset is distributable (same as downloadAsset.py)
    if not asset_object.get('isDistributable', False):
        message = "Asset not distributable"
        error_response = {
            'statusCode': 403,
            'body': json.dumps({"message": message}),
            'headers': {
                'Access-Control-Allow-Headers': 'Range',
            }
        }
        logger.error(error_response)
        return error_response

    asset_object.update({"object__type": "asset"})

    logger.info(asset_object)

    # Check API authorization
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforceAPI(event, http_method):
            # Check object-level authorization
            if casbin_enforcer.enforce(asset_object, http_method):
                operation_allowed_on_asset = True

    if operation_allowed_on_asset:
        try:
            # Get asset location
            asset_location = asset_object.get('assetLocation')
            if not asset_location:
                message = "Asset location not found"
                error_response = {
                    'statusCode': 404,
                    'body': json.dumps({"message": message}),
                    'headers': {
                        'Access-Control-Allow-Headers': 'Range',
                    }
                }
                logger.error(error_response)
                return error_response

            # Get bucket details from bucketId (same as downloadAsset.py)
            bucketDetails = get_default_bucket_details(asset_object.get('bucketId'))
            asset_bucket = bucketDetails['bucketName']
            asset_base_key = asset_location.get('Key')

            # Resolve the full S3 key
            object_key = resolve_asset_file_path(asset_base_key, object_key)

            # Validate file extension and content type (same as downloadAsset.py)
            if not validateS3AssetExtensionsAndContentType(asset_bucket, object_key):
                message = "Unallowed file extension or content type in asset file"
                error_response = {
                    'statusCode': 400,
                    'body': json.dumps({"message": message}),
                    'headers': {
                        'Access-Control-Allow-Headers': 'Range',
                    }
                }
                logger.error(error_response)
                return error_response

            # Prepare the S3 GetObject request parameters
            s3_params = {
                'Bucket': asset_bucket,
                'Key': object_key
            }

            # Add the "Range" header to the S3 GetObject request if it exists
            if range_header and range_header != None and range_header != "":
                s3_params['Range'] = range_header

            #logger.info(s3_params)

            # Fetch the file from S3
            s3_response = s3_client.get_object(**s3_params)
            logger.info(s3_response)

            # Extract the file data
            file_data = s3_response['Body'].read()

            # Prepare the API Gateway response
            api_gateway_response = {
                'statusCode': 200,
                'body': '',
                'headers': {
                        'Access-Control-Allow-Headers': 'Range',
                        'Accept-Ranges': s3_response['ResponseMetadata']['HTTPHeaders']['accept-ranges'],
                        'Content-Type': s3_response['ResponseMetadata']['HTTPHeaders']['content-type'],
                        'Content-Length': s3_response['ResponseMetadata']['HTTPHeaders']['content-length'],
                }
            }

            # Add the "Range" header if returned
            try:
                response_header_range = s3_response['ResponseMetadata']['HTTPHeaders']['content-range']
            except:
                response_header_range = ""

            if response_header_range != None and response_header_range != "":
                api_gateway_response['headers']['Content-Range'] = response_header_range

            # Add the "ContentEncoding" header if returned
            try:
                response_header_content_encoding = s3_response['ResponseMetadata']['HTTPHeaders']['content-encoding']
            except:
                response_header_content_encoding = ""

            if response_header_content_encoding != None and response_header_content_encoding != "":
                api_gateway_response['headers']['Content-Encoding'] = response_header_content_encoding

            #logger.info(logger.info(str(api_gateway_response)))

            # Get the size of the file data in bytes and return error if larger than ~5.9MB (accounting for header space)
            file_size = sys.getsizeof(file_data)
            if file_size > 5.9 * 1024 * 1024:
                # NOTE: 6MB is a hard limit for Lambda functions. API Gateway has a 10MB hard limit.
                message = "Error: Asset File Size Chunk is Larger than ~5.9MB, a AWS service limit. Use a smaller header file Range for asset streaming retrieval."
                error_response = {
                    'statusCode': 400,
                    'body': json.dumps({"message": message}),
                    'headers': {
                        'Access-Control-Allow-Headers': 'Range',
                    }
                }
                logger.error(error_response)
                return error_response

            # If returned data is binary, return the file contents as a base64 encoded string in the body
            if isinstance(file_data, bytes):
                api_gateway_response['body'] = base64.b64encode(file_data).decode('utf-8')
                api_gateway_response['isBase64Encoded'] = True
                logger.info("Return is Binary so BaseEncode64")
            else:
                # else return as regular string
                api_gateway_response['body'] = file_data.decode('utf-8')

            return api_gateway_response

        except ClientError as e:
            logger.exception(e)
            message = "Error Fetching Asset File from Path Provided"
            error_response = {
                'statusCode': 400,
                'body': json.dumps({"message": message}),
                'headers': {
                    'Access-Control-Allow-Headers': 'Range',
                }
            }
            logger.exception(error_response)
            return error_response
        except Exception as e:
            # If other error occurs, return an error response
            message = "Internal Server Error"
            error_response = {
                'statusCode': 500,
                'body': json.dumps({"message": message}),
                'headers': {
                    'Access-Control-Allow-Headers': 'Range',
                }
            }
            logger.exception(error_response)
            return error_response
    else:
        response['statusCode'] = 403
        response['body'] = json.dumps({"message": "Not Authorized"})
        return response
