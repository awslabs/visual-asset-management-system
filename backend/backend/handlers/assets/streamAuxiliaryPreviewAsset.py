#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import base64
from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateUnallowedFileExtensionAndContentType
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse

# Set environment variable for S3 client configuration
# 'regional' set to add region descriptor to presigned urls for us-east-1 (ignored for non us-east-1 regions)
os.environ["AWS_S3_US_EAST_1_REGIONAL_ENDPOINT"] = "regional"

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
logger = safeLogger(service_name="StreamAuxiliaryPreviewAsset")

try:
    auxasset_bucket_name = os.environ["ASSET_AUXILIARY_BUCKET_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    token_timeout = os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_table = dynamodb.Table(asset_storage_table_name)

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
        raise VAMSGeneralErrorResponse("Error retrieving asset")

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

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for auxiliary preview asset streaming APIs"""
    global claims_and_roles
    
    try:
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
            message = "No Auxiliary Preview File Object Key Provided in Path"
            logger.error(message)
            # Create custom headers for streaming response
            streaming_headers = {
                'Access-Control-Allow-Headers': 'Range',
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-cache, no-store',
            }
            error_response = validation_error(body={'message': message})
            error_response['headers'].update(streaming_headers)
            return error_response

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
            'auxiliaryPreviewAssetPathKey': {
                'value': object_key,
                'validator': 'ASSET_AUXILIARYPREVIEW_PATH'
            },
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})

        http_method = "GET"
        operation_allowed_on_asset = False

        # Get asset details and check if it exists
        asset_object = get_asset_details(databaseId, assetId)
        if not asset_object:
            message = f"Asset not found in database"
            logger.error(message)
            # Create custom headers for streaming response
            streaming_headers = {
                'Access-Control-Allow-Headers': 'Range',
                'Access-Control-Allow-Origin': '*',
            }
            error_response = general_error(body={"message": message}, status_code=404)
            error_response['headers'].update(streaming_headers)
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
                # Get the location of the base asset key and normalize the object_key we are passing in
                # (also ensures security of not fetching asset key files outside of provided asset ID)
                assetLocationKey = asset_object.get('assetLocation', {}).get("Key")
                if not assetLocationKey:
                    message = "Asset location not found"
                    logger.error(message)
                    # Create custom headers for streaming response
                    streaming_headers = {
                        'Access-Control-Allow-Headers': 'Range',
                        'Access-Control-Allow-Origin': '*',
                    }
                    error_response = general_error(body={"message": message}, status_code=404)
                    error_response['headers'].update(streaming_headers)
                    return error_response

                object_key = resolve_asset_file_path(assetLocationKey, object_key)

                # Prepare the S3 GetObject request parameters
                s3_params = {
                    'Bucket': auxasset_bucket_name,
                    'Key': object_key
                }

                # Add the "Range" header to the S3 GetObject request if it exists
                if range_header and range_header != None and range_header != "":
                    s3_params['Range'] = range_header

                # Fetch the file metadata from S3 first
                s3_response = s3_client.get_object(**s3_params)
                logger.info(s3_response)

                # Validate file extension and content type using the ContentType from S3 response
                content_type = s3_response.get('ContentType', 'application/octet-stream')
                if not validateUnallowedFileExtensionAndContentType(object_key, content_type):
                    message = "Unallowed file extension or content type in auxiliary preview file"
                    logger.error(message)
                    # Create custom headers for streaming response
                    streaming_headers = {
                        'Access-Control-Allow-Headers': 'Range',
                        'Access-Control-Allow-Origin': '*',
                    }
                    error_response = validation_error(body={"message": message})
                    error_response['headers'].update(streaming_headers)
                    return error_response

                # Get the content length from S3 metadata
                content_length = s3_response.get('ContentLength', 0)
                
                # Set conservative limit for streaming (4.4MB raw = ~5.87MB base64 encoded, safely under 6MB Lambda limit)
                MAX_STREAMING_SIZE = int(4.4 * 1024 * 1024)  # 4.4MB
                
                # If file is larger than 4.4MB, generate presigned URL and redirect
                if content_length > MAX_STREAMING_SIZE:
                    logger.info(f"File size ({content_length / (1024*1024):.2f}MB) exceeds streaming limit. Generating presigned URL.")
                    
                    # Generate presigned URL
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params=s3_params,
                        ExpiresIn=int(token_timeout)
                    )
                    
                    # Return 307 redirect to presigned URL
                    return {
                        'statusCode': 307,
                        'headers': {
                            'Location': presigned_url,
                            'Access-Control-Allow-Headers': 'Range',
                            'Access-Control-Allow-Origin': '*',
                            'Cache-Control': 'no-cache, no-store',
                        },
                        'body': ''
                    }

                # For files 4MB and under, stream with base64 encoding
                # Extract the file data
                file_data = s3_response['Body'].read()

                # Prepare the API Gateway response
                api_gateway_response = {
                    'statusCode': 200,
                    'body': '',
                    'headers': {
                            'Access-Control-Allow-Headers': 'Range',
                            'Access-Control-Allow-Origin': '*',
                            'Cache-Control': 'no-cache, no-store',
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
                logger.exception(f"S3 ClientError: {e}")
                message = "Error Fetching Auxiliary Preview File from Path Provided"
                # Create custom headers for streaming response
                streaming_headers = {
                    'Access-Control-Allow-Headers': 'Range',
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache, no-store',
                }
                error_response = general_error(body={"message": message})
                error_response['headers'].update(streaming_headers)
                return error_response
        else:
            return authorization_error()
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
