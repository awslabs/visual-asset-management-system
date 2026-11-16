# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
from common.s3 import region_routing_s3_client, validateUnallowedFileExtensionAndContentType
import json
from botocore.config import Config
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import (
    AUDIT_INTERNAL_CALL_KEY, audited_handler, internal_call_marker, log_action, log_file_upload
)
from common.apiRoutes import API_ASSETS, API_UPLOADS, API_UPLOAD_COMPLETE
from common.assetUploads import UPLOAD_RECORD_NOT_FOUND, completion_refusal, validate_file_extension_against_database
from common.s3PathPatterns import ALLOWED_PREVIEW_FILE_EXTENSIONS, PREVIEW_FILE_PATTERN
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from models.common import (
    validation_error_message,
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

# Configure AWS clients (the S3 client is the Region-routing client from common.s3)
region = os.environ['AWS_REGION']
s3 = region_routing_s3_client()

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
lambda_client = boto3.client('lambda', config=retry_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="IngestAsset")

# Load environment variables
try:
    from common.resourceNames import ResourceKeys, get_table_name
    db_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    asset_upload_table_name = get_table_name(ResourceKeys.ASSET_UPLOADS_STORAGE_TABLE)
    # Lambda functions for cross-calls
    create_asset_lambda = os.environ["CREATE_ASSET_LAMBDA_FUNCTION_NAME"]
    file_upload_lambda = os.environ["FILE_UPLOAD_LAMBDA_FUNCTION_NAME"]
except Exception as e:
    logger.exception(f"Failed loading environment variables or resolving resource names: {e}")
    raise e

#######################
# Utility Functions
#######################

# uploadFile statuses returned to the ingest-asset caller as they are (status, headers such as
# Retry-After, and body); every other non-200 from uploadFile is reported as a 400
PASS_THROUGH_UPLOAD_STATUSES = (403, 404, 429)

# The upload type every ingest-asset upload is initialized and completed with
INGEST_UPLOAD_TYPE = "assetFile"

class UploadResponsePassThrough(Exception):
    """An uploadFile response that lambda_handler returns to the caller unchanged."""

    def __init__(self, response):
        super().__init__(f"uploadFile returned status {response.get('statusCode')}")
        self.response = response

def verify_database_exists(database_id):
    """Check if a database exists; returns its stored record"""
    table = dynamodb.Table(db_table_name)
    try:
        response = table.get_item(Key={'databaseId': database_id})
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Database does not exist")
        return response['Item']
    except Exception as e:
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        logger.exception(f"Error verifying database: {e}")
        raise VAMSGeneralErrorResponse(f"Error verifying database.")

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
        raise VAMSGeneralErrorResponse(f"Error verifying asset.")


def verify_upload_can_complete(event, request_model: IngestAssetCompleteRequestModel):
    """Refuse a completion whose upload record cannot be completed, before anything is written.

    Asks the questions the upload completion asks of the record (common/assetUploads.py): it exists
    for this asset, belongs to this database, was initialized for an asset-file upload, has not
    expired and is not a workflow-output record. A refusal answers with the message the upload
    completion would give, and a refusal the completion audits per file is audited the same way.
    """
    database_id = request_model.databaseId
    asset_id = request_model.assetId
    upload_id = request_model.uploadId
    table = dynamodb.Table(asset_upload_table_name)
    try:
        response = table.get_item(Key={'uploadId': upload_id, 'assetId': asset_id})
    except Exception as e:
        logger.exception(f"Error reading upload record: {e}")
        raise VAMSGeneralErrorResponse("Error verifying upload.")

    upload_record = response.get('Item')
    if upload_record is None:
        reason, audited = UPLOAD_RECORD_NOT_FOUND, False
    else:
        refusal = completion_refusal(upload_record, database_id, asset_id, INGEST_UPLOAD_TYPE)
        if refusal is None:
            return
        reason, audited = refusal.reason, refusal.audited

    logger.info(f"Upload {upload_id} for asset {asset_id} in database {database_id} cannot be completed: {reason}")
    if audited:
        for file in request_model.files:
            log_file_upload(event, database_id, asset_id, file.relativeKey, True, reason,
                            {"uploadId": upload_id, "uploadType": INGEST_UPLOAD_TYPE})
    raise VAMSGeneralErrorResponse(f"Error completing upload: {reason}")


def verify_files_can_initialize(database, files):
    """Refuse, before an asset is created, a file the upload initialization would refuse for its name.

    Runs the rules of `uploadFile.initialize_upload` that read only the file names and the database
    (the database's allowed extensions, the blocked extension list, the allowed extensions of a
    `.previewFile.` file), in the same order and with the same messages, so an initialize that the
    upload step would refuse creates no asset. The rules that need the asset's bucket (key length,
    response size) and the per-user rate limit stay with the upload step.
    """
    allowed_extensions = (database or {}).get('restrictFileUploadsToExtensions', '')
    if allowed_extensions and allowed_extensions.strip() != "":
        for file in files:
            if PREVIEW_FILE_PATTERN not in file.relativeKey:
                is_valid, error_message = validate_file_extension_against_database(
                    file.relativeKey, allowed_extensions)
                if not is_valid:
                    raise VAMSGeneralErrorResponse(f"Error initializing upload: {error_message}")

    for file in files:
        if not validateUnallowedFileExtensionAndContentType(file.relativeKey, ""):
            raise VAMSGeneralErrorResponse("Error initializing upload: Files contain an unsupported file extension")
        if PREVIEW_FILE_PATTERN in file.relativeKey:
            extension = '.' + file.relativeKey.split(PREVIEW_FILE_PATTERN)[1].lower()
            if extension not in ALLOWED_PREVIEW_FILE_EXTENSIONS:
                raise VAMSGeneralErrorResponse(
                    "Error initializing upload: Preview files must have one of the allowed extensions: "
                    ".png, .jpg, .jpeg, .svg, .gif")


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
        raise VAMSGeneralErrorResponse(f"Error invoking lambda function.")


#######################
# API Implementations
#######################

def initialize_ingest(request_model: IngestAssetInitializeRequestModel, claims_and_roles, event_auth):
    """Initialize an asset ingest operation"""
    database_id = request_model.databaseId
    asset_id = request_model.assetId
    
    # Verify database exists
    database = verify_database_exists(database_id)

    # Check if asset exists
    asset_exists = verify_asset_exists(database_id, asset_id)
    
    # If asset doesn't exist, create it first, but only for files the upload step would accept by
    # name: a refused initialize must not leave a new, empty asset behind.
    if not asset_exists:
        verify_files_can_initialize(database, request_model.files)

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
            },
            "requestContext": {
                "http": {
                    "path": API_ASSETS.path,
                    "method": "POST"
                },
                "authorizer": event_auth
            },
            AUDIT_INTERNAL_CALL_KEY: internal_call_marker()
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
    
    # Prepare payload for fileUpload lambda (initialize upload)
    initialize_upload_payload = {
        "body": {
            "assetId": asset_id,
            "databaseId": database_id,
            "uploadType": INGEST_UPLOAD_TYPE,
            "files": [file.dict() for file in request_model.files]
        },
        "requestContext": {
            "http": {
                "path": API_UPLOADS.path,
                "method": "POST"
            },
            "authorizer": event_auth
        },
        AUDIT_INTERNAL_CALL_KEY: internal_call_marker()
    }
    
    # Invoke fileUpload lambda to initialize the upload
    file_upload_response = invoke_lambda(file_upload_lambda, initialize_upload_payload)
    
    # Check response
    if file_upload_response['statusCode'] in PASS_THROUGH_UPLOAD_STATUSES:
        raise UploadResponsePassThrough(file_upload_response)
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

def complete_ingest(request_model: IngestAssetCompleteRequestModel, claims_and_roles, event_auth, event):
    """Complete an asset ingest operation"""
    database_id = request_model.databaseId
    asset_id = request_model.assetId
    upload_id = request_model.uploadId

    # The upload-completion route with this upload's id filled in, which is what uploadFile
    # dispatches on. An id that does not fill the route as one path segment is refused before
    # anything is written.
    complete_upload_path = API_UPLOAD_COMPLETE.path.replace("{uploadId}", upload_id)
    if not API_UPLOAD_COMPLETE.matches(complete_upload_path):
        raise ValueError("Invalid uploadId")
    
    # Verify database exists
    verify_database_exists(database_id)
    
    # Check if asset exists
    asset_exists = verify_asset_exists(database_id, asset_id)
    
    # If asset doesn't exist, create it first, but only for an upload record the completion can
    # complete: a refused completion must not leave a new, empty asset behind. When the asset
    # exists nothing is written here, and the upload completion checks the record itself.
    if not asset_exists:
        verify_upload_can_complete(event, request_model)

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
            },
            "requestContext": {
                "http": {
                    "path": API_ASSETS.path,
                    "method": "POST"
                },
                "authorizer": event_auth
            },
            AUDIT_INTERNAL_CALL_KEY: internal_call_marker()
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
            "uploadType": INGEST_UPLOAD_TYPE,
            "files": [file.dict() for file in request_model.files]
        },
        "pathParameters": {
            "uploadId": upload_id
        },
        "requestContext": {
            "http": {
                "path": complete_upload_path,
                "method": "POST"
            },
            "authorizer": event_auth
        },
        AUDIT_INTERNAL_CALL_KEY: internal_call_marker()
    }
    
    # Invoke fileUpload lambda to complete the upload
    file_upload_response = invoke_lambda(file_upload_lambda, complete_upload_payload)
    
    # Check response
    if file_upload_response['statusCode'] in PASS_THROUGH_UPLOAD_STATUSES:
        raise UploadResponsePassThrough(file_upload_response)
    if file_upload_response['statusCode'] != 200:
        response_body = json.loads(file_upload_response.get("body", "{}"))
        error_message = response_body.get('message', "Unknown error completing upload")
        logger.error(f"Error completing upload: {error_message}")
        raise VAMSGeneralErrorResponse(f"Error completing upload: {error_message}")
    
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


@audited_handler
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset ingest API"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
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

        event_auth = event['requestContext']['authorizer']

        # Fail closed: with no authenticated identity no authorization can be
        # evaluated, so deny rather than fall through to the mutation.
        if len(claims_and_roles["tokens"]) == 0:
            return authorization_error()

        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not (casbin_enforcer.enforce(asset, "PUT") and casbin_enforcer.enforceAPI(event)):
            return authorization_error()

        # Process request based on stage
        if is_complete_stage:
            # Stage 2 - Complete upload
            response = complete_ingest(request_model, claims_and_roles, event_auth, event)
        else:
            # Stage 1 - Initialize upload
            response = initialize_ingest(request_model, claims_and_roles, event_auth)

        files = request_model.files or []
        details = {"stage": "complete" if is_complete_stage else "initialize",
                   "files": [file.relativeKey for file in files]}
        if is_complete_stage:
            # Completion counts the files that landed; no byte count is recorded, since the
            # declared sizes of the initialize stage are not what was stored.
            file_count = sum(1 for result in getattr(response, "fileResults", None) or [] if result.success)
        else:
            file_count = len(files)
            declared = [getattr(file, "file_size", None) for file in files]
            if declared and all(size is not None for size in declared):
                details["declaredBytes"] = sum(declared)
        log_action(event, "assetIngest", database_id=request_model.databaseId, asset_id=request_model.assetId,
                   file_count=file_count,
                   changes={field: value for field, value in request_model.dict(exclude_none=True).items()
                            if field not in ("files", "uploadId")},
                   details=details, uploadId=getattr(request_model, "uploadId", None))
        return success(body=response.dict())
            
    except UploadResponsePassThrough as p:
        logger.warning(f"Returning the upload response unchanged: {p}")
        return p.response
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
