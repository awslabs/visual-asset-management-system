# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from botocore.config import Config
from datetime import datetime
from handlers.metadata import to_update_expr
from common.constants import STANDARD_JSON_RESPONSE
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success, 
    validation_error, general_error, authorization_error, 
    VAMSGeneralErrorResponse
)
from models.assetsV3 import (
    IngestAssetInitializeRequestModel, IngestAssetInitializeResponseModel,
    IngestAssetCompleteRequestModel, IngestAssetCompleteResponseModel,
    InitializeUploadRequestModel, CompleteUploadRequestModel,
    CreateAssetRequestModel, UploadFileModel
)

# Configure AWS clients
region = os.environ['AWS_REGION']
s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'})
s3 = boto3.client('s3', region_name=region, config=s3_config)

lambda_client = boto3.client('lambda')
dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
logger = safeLogger(service_name="IngestAsset")

# Load environment variables
try:
    db_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    metadata_table_name = os.environ["METADATA_STORAGE_TABLE_NAME"]
    # Lambda functions for cross-calls
    create_asset_lambda = os.environ["CREATE_ASSET_LAMBDA_FUNCTION_NAME"]
    file_upload_lambda = os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]
except Exception as e:
    logger.exception(f"Failed loading environment variables: {e}")
    raise e

#######################
# Utility Functions
#######################

def verify_database_exists(database_id):
    """Check if a database exists"""
    table = dynamodb.Table(db_table_name)
    try:
        response = table.get_item(Key={'databaseId': database_id})
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse(f"Database with ID {database_id} does not exist")
        return True
    except Exception as e:
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        logger.exception(f"Error verifying database: {e}")
        raise VAMSGeneralErrorResponse(f"Error verifying database: {str(e)}")

def verify_asset_exists(database_id, asset_id):
    """Check if an asset exists in the database"""
    table = dynamodb.Table(asset_table_name)
    try:
        response = table.get_item(Key={
            'databaseId': database_id,
            'assetId': asset_id
        })
        return 'Item' in response
    except Exception as e:
        logger.exception(f"Error verifying asset: {e}")
        raise VAMSGeneralErrorResponse(f"Error verifying asset: {str(e)}")

def invoke_lambda(function_name, payload, invocation_type="RequestResponse"):
    """Invoke a lambda function with the given payload"""
    try:
        logger.info(f"Invoking {function_name} lambda...")
        lambda_response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=json.dumps(payload).encode('utf-8')
        )
        
        if invocation_type == "RequestResponse":
            stream = lambda_response['Payload']
            response_payload = json.loads(stream.read().decode("utf-8"))
            logger.info(f"Lambda response: {response_payload}")
            return response_payload
        return None
    except Exception as e:
        logger.exception(f"Error invoking lambda function {function_name}: {e}")
        raise VAMSGeneralErrorResponse(f"Error invoking lambda function {function_name}: {str(e)}")

def update_metadata(database_id, asset_id):
    """Update the metadata timestamp for an asset"""
    try:
        table = dynamodb.Table(metadata_table_name)
        metadata = {'_metadata_last_updated': datetime.now().isoformat()}
        keys_map, values_map, expr = to_update_expr(metadata)
        table.update_item(
            Key={
                "databaseId": database_id,
                "assetId": asset_id,
            },
            ExpressionAttributeNames=keys_map,
            ExpressionAttributeValues=values_map,
            UpdateExpression=expr,
        )
        logger.info("Updated metadata successfully")
        return True
    except Exception as e:
        logger.warning(f"Error updating metadata: {e}")
        # Continue even if metadata update fails
        return False

#######################
# API Implementations
#######################

def initialize_ingest(request_model: IngestAssetInitializeRequestModel, claims_and_roles):
    """Initialize an asset ingest operation"""
    database_id = request_model.databaseId
    asset_id = request_model.assetId
    
    # Verify database exists
    verify_database_exists(database_id)
    
    # Prepare payload for fileUpload lambda (initialize upload)
    initialize_upload_payload = {
        "body": {
            "assetId": asset_id,
            "databaseId": database_id,
            "uploadType": "assetFile",
            "files": [file.dict() for file in request_model.files]
        }
    }
    
    # Invoke fileUpload lambda to initialize the upload
    file_upload_response = invoke_lambda(file_upload_lambda, initialize_upload_payload)
    
    # Check response
    if file_upload_response['statusCode'] != 200:
        response_body = json.loads(file_upload_response.get("body", "{}"))
        error_message = response_body.get('message', "Unknown error initializing upload")
        logger.error(f"Error initializing upload: {error_message}")
        raise VAMSGeneralErrorResponse(f"Error initializing upload: {error_message}")
    
    # Parse response
    response_body = json.loads(file_upload_response.get("body", "{}"))
    
    # Return response
    return IngestAssetInitializeResponseModel(
        message="Upload initialized successfully",
        uploadId=response_body.get("uploadId"),
        files=response_body.get("files", [])
    )

def complete_ingest(request_model: IngestAssetCompleteRequestModel, claims_and_roles):
    """Complete an asset ingest operation"""
    database_id = request_model.databaseId
    asset_id = request_model.assetId
    upload_id = request_model.uploadId
    
    # Verify database exists
    verify_database_exists(database_id)
    
    # Check if asset exists
    asset_exists = verify_asset_exists(database_id, asset_id)
    
    # If asset doesn't exist, create it first
    if not asset_exists:
        logger.info(f"Asset {asset_id} does not exist. Creating it first.")
        
        # Prepare payload for createAsset lambda
        create_asset_payload = {
            "body": {
                "databaseId": database_id,
                "assetId": asset_id,
                "assetName": request_model.assetName,
                "description": request_model.description,
                "isDistributable": request_model.isDistributable,
                "tags": request_model.tags
            }
        }
        
        # Invoke createAsset lambda
        create_asset_response = invoke_lambda(create_asset_lambda, create_asset_payload)
        
        # Check response
        if create_asset_response['statusCode'] != 200:
            response_body = json.loads(create_asset_response.get("body", "{}"))
            error_message = response_body.get('message', "Unknown error creating asset")
            logger.error(f"Error creating asset: {error_message}")
            raise VAMSGeneralErrorResponse(f"Error creating asset: {error_message}")
        
        logger.info("Asset created successfully")
    
    # Prepare payload for fileUpload lambda (complete upload)
    complete_upload_payload = {
        "body": {
            "assetId": asset_id,
            "databaseId": database_id,
            "uploadType": "assetFile",
            "files": [file.dict() for file in request_model.files]
        },
        "pathParameters": {
            "uploadId": upload_id
        }
    }
    
    # Invoke fileUpload lambda to complete the upload
    file_upload_response = invoke_lambda(file_upload_lambda, complete_upload_payload)
    
    # Check response
    if file_upload_response['statusCode'] != 200:
        response_body = json.loads(file_upload_response.get("body", "{}"))
        error_message = response_body.get('message', "Unknown error completing upload")
        logger.error(f"Error completing upload: {error_message}")
        raise VAMSGeneralErrorResponse(f"Error completing upload: {error_message}")
    
    # Update metadata
    update_metadata(database_id, asset_id)
    
    # Parse response
    response_body = json.loads(file_upload_response.get("body", "{}"))
    
    # Return response
    return IngestAssetCompleteResponseModel(
        message="Multipart upload and asset ingestion completed successfully.",
        uploadId=upload_id,
        assetId=asset_id,
        fileResults=response_body.get("fileResults", []),
        overallSuccess=response_body.get("overallSuccess", True)
    )

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset ingest API"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Determine if this is stage 1 (initialize) or stage 2 (complete)
        is_complete_stage = "uploadId" in body
        
        # Parse request model based on stage
        if is_complete_stage:
            # Stage 2 - Complete upload
            request_model = parse(body, model=IngestAssetCompleteRequestModel)
        else:
            # Stage 1 - Initialize upload
            request_model = parse(body, model=IngestAssetInitializeRequestModel)
        
        # Check authorization
        asset = {
            "object__type": "asset",
            "databaseId": request_model.databaseId,
            "assetId": request_model.assetId,
            "assetName": request_model.assetName,
            "tags": request_model.tags
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not (casbin_enforcer.enforce(asset, "PUT") and casbin_enforcer.enforceAPI(event)):
                return authorization_error()
        
        # Process request based on stage
        if is_complete_stage:
            # Stage 2 - Complete upload
            response = complete_ingest(request_model, claims_and_roles)
        else:
            # Stage 1 - Initialize upload
            response = initialize_ingest(request_model, claims_and_roles)
        
        return success(body=response.dict())
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
