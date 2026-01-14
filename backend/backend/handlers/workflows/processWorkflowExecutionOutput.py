#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
import json
import uuid
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType
from models.assetsV3 import AssetUploadTableModel

asset_Database = None
db_Database = None
workflow_execution_database = None
asset_upload_table_name = None
s3_asset_buckets_table = None
logger = safeLogger(service_name="ProcessWorkflowExecutionOutput")

# Constants
UPLOAD_EXPIRATION_DAYS = 1  # TTL for upload records for pipeline output

try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    metadata_service_function = os.environ['METADATA_SERVICE_LAMBDA_FUNCTION_NAME']
    file_upload_function = os.environ['FILE_UPLOAD_LAMBDA_FUNCTION_NAME']
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_upload_table_name = os.environ["ASSET_UPLOAD_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]

except Exception as e:
    logger.exception("Failed loading environment variables")
    raise

s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
client = boto3.client('lambda')
asset_upload_table = dynamodb.Table(asset_upload_table_name)
buckets_table = dynamodb.Table(s3_asset_buckets_table)


def _lambda_metadata_service(payload):
    """Invoke metadata service lambda"""
    return client.invoke(
        FunctionName=metadata_service_function,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload).encode('utf-8')
    )


def _lambda_file_ingestion(payload):
    """Invoke file upload lambda"""
    return client.invoke(
        FunctionName=file_upload_function,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload).encode('utf-8')
    )


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
            raise Exception(f"Error getting database default bucket details: missing bucket_name or base_assets_prefix")
        
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
        raise Exception(f"Error getting bucket details.")

def verify_get_path_objects(bucketName: str, pathPrefix: str):

    #Do MIME check on whatever is uploaded to S3 at this point for this asset, before we do DynamoDB insertion, to validate it's not malicious
    if(not validateS3AssetExtensionsAndContentType(bucketName, pathPrefix)):
        raise Exception("Pipeline uploaded objects contains a potentially malicious executable type object. Unable to process asset upload.")

    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.list_objects_v2
    all_outputs = s3c.list_objects_v2(Bucket=bucketName, Prefix=pathPrefix)
    if 'IsTruncated' in all_outputs and all_outputs['IsTruncated']:
        logger.warning(
            "WARN: s3 object listing exceeds 1,000 objects,"+
            "this is unexpected for this operation with the bucket and prefix"+
            bucketName+" "+pathPrefix
        )
    logger.info(all_outputs)

    return all_outputs

def lookup_existing_asset(database_id, asset_id):
    asset_table = dynamodb.Table(asset_Database)
    asset = asset_table.get_item(
        Key={'databaseId': database_id, 'assetId': asset_id})
    if 'Item' in asset:
        return asset['Item']
    else:
        return None

def create_external_upload_record(asset_id, database_id, upload_type, baseFileKeyPrefix):
    """Create an external upload record in DynamoDB"""
    try:
        # Generate upload ID
        upload_id = f"y{str(uuid.uuid4())}"
        
        # Calculate expiration time (7 days from now)
        now = datetime.utcnow()
        expires_at = int((now + timedelta(days=UPLOAD_EXPIRATION_DAYS)).timestamp())
        
        # Create upload record
        upload_record = AssetUploadTableModel(
            uploadId=upload_id,
            assetId=asset_id,
            databaseId=database_id,
            uploadType=upload_type,
            createdAt=now.isoformat(),
            expiresAt=expires_at,
            totalFiles=0,  # Will be updated later
            totalParts=0,  # Not relevant for external uploads
            status="initialized",
            isExternalUpload=True,
            temporaryPrefix=baseFileKeyPrefix
        )
        
        # Save to DynamoDB
        asset_upload_table.put_item(Item=upload_record.to_dict())
        
        return upload_id
    except Exception as e:
        logger.exception(f"Error creating external upload record: {e}")
        raise e

def update_s3_object_metadata(key, asset_id, database_id, upload_id, bucket_name):
    """Update S3 object metadata with asset and upload information"""
    try:
        # Get current object metadata
        head_response = s3c.head_object(Bucket=bucket_name, Key=key)
        content_type = head_response.get('ContentType', 'application/octet-stream')
        current_metadata = head_response.get('Metadata', {})
        
        # Merge existing metadata with new metadata
        metadata = {**current_metadata, 'databaseid': database_id, 'assetid': asset_id, 'uploadid': upload_id}
        
        # Use boto3 resource copy() which automatically handles multipart for large files
        s3_resource = boto3.resource('s3')
        copy_source = {
            'Bucket': bucket_name,
            'Key': key
        }
        s3_resource.Object(bucket_name, key).copy(
            copy_source,
            ExtraArgs={
                'ContentType': content_type,
                'Metadata': metadata,
                'MetadataDirective': 'REPLACE'
            }
        )
        
        return True
    except Exception as e:
        logger.exception(f"Error updating S3 object metadata: {e}")
        return False

def process_external_upload(upload_id, asset_id, database_id, upload_type, files, baseFileKeyPrefix, request_context):
    """Process an external upload using the fileIngestion Lambda"""
    try:
        # Prepare the request payload
        file_list = []
        for file_key in files:
            # Extract the file name/path based on upload type
            if upload_type == "assetFile":
                # For asset files, preserve the relative path structure
                if file_key.startswith(baseFileKeyPrefix):
                    file_name = file_key[len(baseFileKeyPrefix):]
                else:
                    file_name = file_key
                
                # Remove leading slash if present
                if file_name.startswith('/'):
                    file_name = file_name[1:]
            else:
                # For other upload types (like assetPreview), just use the filename
                file_name = os.path.basename(file_key)
            
            # Add to file list
            file_list.append({
                "relativeKey": file_name,
                "tempKey": file_key
            })
        
        # Create the request body
        body = {
            "assetId": asset_id,
            "databaseId": database_id,
            "uploadType": upload_type,
            "files": file_list
        }
        
        # Create the Lambda payload to simulate an API Gateway request
        lambda_payload = {
            "requestContext": request_context,
            "pathParameters": {
                "uploadId": upload_id
            },
            "body": json.dumps(body),
        }
        lambda_payload["requestContext"]["http"]["path"] = f"/uploads/{upload_id}/complete/external"
        lambda_payload["requestContext"]["http"]["httpMethod"] = f"POST"
        
        # Invoke the Lambda function
        response = _lambda_file_ingestion(lambda_payload)
        
        # Process the response
        if response and 'Payload' in response:
            stream = response['Payload']
            if stream:
                json_response = json.loads(stream.read().decode("utf-8"))
                logger.info("fileIngestion response:")
                logger.info(json_response)
                
                if "statusCode" in json_response and json_response["statusCode"] == 200:
                    if "body" in json_response:
                        return json.loads(json_response["body"])
                    else:
                        logger.error("No body in fileIngestion response")
                        return None
                else:
                    logger.error(f"Error in fileIngestion response: {json_response}")
                    return None
            else:
                logger.error("No payload stream in fileIngestion response")
                return None
        else:
            logger.error("Invalid response from fileIngestion Lambda")
            return None
    except Exception as e:
        logger.exception(f"Error processing external upload: {e}")
        return None


def filter_metadata_files(objects_list):
    """Filter S3 objects to only include files ending with .metadata.json
    
    Excludes directory markers (keys ending with /) and includes files in subdirectories.
    """
    filtered = []
    for obj in objects_list:
        key = obj['Key']
        # Exclude directory markers (keys ending with /)
        if key.endswith('/'):
            continue
        # Include files ending with .metadata.json
        if key.endswith('.metadata.json'):
            filtered.append(obj)
    return filtered


def filter_attribute_files(objects_list):
    """Filter S3 objects to only include files ending with .attribute.json
    
    Excludes directory markers (keys ending with /) and includes files in subdirectories.
    """
    filtered = []
    for obj in objects_list:
        key = obj['Key']
        # Exclude directory markers (keys ending with /)
        if key.endswith('/'):
            continue
        # Include files ending with .attribute.json
        if key.endswith('.attribute.json'):
            filtered.append(obj)
    return filtered


def extract_file_path_from_metadata_filename(s3_key, metadata_path_key):
    """
    Extract the target file path from metadata/attribute filename.
    Example: 'prefix/folder1/folder2/boopy.glb.metadata.json' -> 'folder1/folder2/boopy.glb'
    """
    try:
        # Remove the metadata_path_key prefix
        if s3_key.startswith(metadata_path_key):
            relative_path = s3_key[len(metadata_path_key):]
        else:
            relative_path = s3_key
        
        # Remove leading slash if present
        if relative_path.startswith('/'):
            relative_path = relative_path[1:]
        
        # Remove '.metadata.json' or '.attribute.json' suffix
        if relative_path.endswith('.metadata.json'):
            relative_path = relative_path[:-len('.metadata.json')]
        elif relative_path.endswith('.attribute.json'):
            relative_path = relative_path[:-len('.attribute.json')]
        
        return relative_path
    except Exception as e:
        logger.exception(f"Error parsing file path from metadata filename: {e}")
        return None


def process_metadata_file(bucket_name, s3_key, metadata_path_key, database_id, asset_id, file_path, metadata_type, request_context):
    """Process metadata or attribute file from pipeline output"""
    try:
        logger.info(f"Processing {metadata_type} file: {s3_key}")
        
        # Read JSON file from S3
        objectResponse = s3c.get_object(Bucket=bucket_name, Key=s3_key)
        objectData = objectResponse['Body'].read().decode("utf-8")
        
        try:
            data = json.loads(objectData)
            logger.info(f"{metadata_type.capitalize()} file content loaded")
            
            # Validate and auto-correct type field
            file_type = data.get('type', metadata_type)
            if file_type != metadata_type:
                logger.warning(f"Type mismatch in {s3_key}: expected '{metadata_type}', got '{file_type}'. Auto-correcting.")
                data['type'] = metadata_type
            
            # Extract updateType (defaults to 'update')
            update_type = data.get('updateType', 'update')
            if update_type not in ['update', 'replace_all']:
                logger.warning(f"Invalid updateType '{update_type}' in {s3_key}. Defaulting to 'update'.")
                update_type = 'update'
            
            # Validate metadata array exists
            if 'metadata' not in data or not isinstance(data['metadata'], list):
                logger.error(f"Invalid metadata structure in {s3_key}: missing or invalid 'metadata' array")
                return
            
            # Build request body for metadata service
            request_body = {
                'metadata': data['metadata'],
                'updateType': update_type
            }
            
            # Add filePath and type for file metadata/attributes
            if file_path:
                request_body['filePath'] = file_path
                request_body['type'] = metadata_type
            
            # Build Lambda event for metadata service PUT endpoint
            if file_path:
                # File metadata/attribute endpoint
                path = f"/database/{database_id}/assets/{asset_id}/metadata/file"
            else:
                # Asset metadata endpoint
                path = f"/database/{database_id}/assets/{asset_id}/metadata"
            
            event = {
                'requestContext': {
                    'http': {
                        'path': path,
                        'method': 'PUT'
                    },
                    #'authorizer': request_context['authorizer']
                },
                'pathParameters': {
                    'databaseId': database_id,
                    'assetId': asset_id
                },
                'body': json.dumps(request_body),
                'lambdaCrossCall': {
                    'userName': 'SYSTEM_USER'
                }
            }
            
            # Invoke metadata service
            logger.info(f"Invoking metadata service with updateType={update_type}")
            response = _lambda_metadata_service(event)
            
            # Process response
            if response and 'Payload' in response:
                stream = response['Payload']
                json_response = json.loads(stream.read().decode("utf-8"))
                
                if json_response.get('statusCode') == 200:
                    body = json.loads(json_response['body'])
                    logger.info(f"Successfully processed {metadata_type} with updateType={update_type}: {body.get('message')}")
                else:
                    logger.error(f"Error processing {metadata_type}: {json_response}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {s3_key}: {e}")
    except Exception as e:
        logger.exception(f"Error processing {metadata_type} file {s3_key}: {e}")


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

    try:
        if 'body' in event:
            if isinstance(event['body'], str):
                try:
                    event['body'] = json.loads(event['body'])
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in request body: {e}")
                    response['statusCode'] = 400
                    response['body'] = json.dumps({"message": "Invalid JSON in request body"})
                    return response
        else:
            message = "No Body in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response
        
        #sub in body for event
        event = event["body"]

        #Input validation
        if 'databaseId' not in event:
            message = "No databaseId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'assetId' not in event:
            message = "No assetId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'executingRequestContext' not in event:
            message = "No executingRequestContext in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'executingUserName' not in event:
            message = "No executingUserName in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response


        logger.info("Validating parameters")
        #required fields
        (valid, message) = validate({
            'databaseId': {
                'value': event['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': event['assetId'],
                'validator': 'ASSET_ID'
            },
            'executingUserName': {
                'value': event['executingUserName'],
                'validator': 'USERID'
            },
            'assetFilesPathPipelineKey': {
                'value': event.get("filesPathKey", ""),
                'validator': 'ASSET_PATH_PIPELINE',
                'optional': True
            },
            'assetMetadataPathPipelineKey': {
                'value': event.get('metadataPathKey',""),
                'validator': 'ASSET_PATH_PIPELINE',
                'optional': True
            },
            'assetPreviewPathPipelineKey': {
                'value': event.get('previewPathKey', ""),
                'validator': 'ASSET_PATH_PIPELINE',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        global claims_and_roles
        requestContext = event['executingRequestContext']
        event["requestContext"] = requestContext
        claims_and_roles = request_to_claims(event)

        # Get existing asset
        asset = lookup_existing_asset(event['databaseId'], event['assetId'])
        if not asset:
            logger.error(f"Asset {event['assetId']} not found in database {event['databaseId']}")
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Asset not found in database"})
            return response

        #ABAC Checks for Asset
        #ABAC Implementation Deviation - Not called through API. Username passed through Pipeline Execution Call.
        asset.update({
            "object__type": "asset",
        })
        logger.info(asset)

        operation_allowed_on_asset = False
        
        # Special handling for SYSTEM_USER (pipeline executions)
        if event.get('executingUserName') == 'SYSTEM_USER':
            logger.info("SYSTEM_USER detected - bypassing authorization for pipeline execution")
            operation_allowed_on_asset = True
        elif len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset, "PUT"):
                operation_allowed_on_asset = True
                logger.info("Authorization check passed for user")
            else:
                logger.warning("Authorization check failed for user")
        else:
            logger.warning("No tokens found in claims_and_roles")
        
        if operation_allowed_on_asset:
            # Get bucket details from asset's bucketId
            bucketDetails = get_default_bucket_details(asset['bucketId'])
            bucket_name = bucketDetails['bucketName']

            #Handle preview outputs
            if ('previewPathKey' in event):
                previewPathKey = event['previewPathKey']
                logger.info(f"Processing preview outputs from: {previewPathKey}")

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, previewPathKey)
                    logger.info(f"Found {len(objectsFound.get('Contents', []))} objects in preview path")
                except Exception as e:
                    logger.exception(f"Error listing preview objects: {e}")

                if 'Contents' in objectsFound:
                    files = [x['Key'] for x in objectsFound['Contents'] if '/' != x['Key'][-1]]

                    if(len(files) > 1):
                        logger.error("Multiple files present in pipeline output preview folder. Limiting to top 1 for now.")
                    
                    # Filter for image files
                    image_files = [f for f in files if f.endswith('.jpeg') or f.endswith('.jpg') or f.endswith('.png') or f.endswith('.gif') or f.endswith('.svg')]
                    
                    if image_files:
                        # Only process the first image file
                        preview_file = image_files[0]
                        
                        try:
                            # Create external upload record
                            upload_id = create_external_upload_record(
                                event['assetId'],
                                event['databaseId'],
                                "assetPreview",
                                previewPathKey
                            )
                            
                            # Update S3 object metadata
                            update_s3_object_metadata(
                                preview_file,
                                event['assetId'],
                                event['databaseId'],
                                upload_id,
                                bucket_name
                            )
                            
                            # Process the external upload
                            result = process_external_upload(
                                upload_id,
                                event['assetId'],
                                event['databaseId'],
                                "assetPreview",
                                [preview_file],
                                previewPathKey,
                                requestContext
                            )
                            
                            if result:
                                logger.info("Preview upload completed successfully")
                            else:
                                logger.error("Preview upload failed")
                        except Exception as e:
                            logger.exception(f"Error processing preview upload: {e}")
                    else:
                        logger.error("No image files found in preview folder")

            #Handle asset file outputs
            if('filesPathKey' in event):
                filesPathKey = event['filesPathKey']
                logger.info(f"Processing asset file outputs from: {filesPathKey}")

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, filesPathKey)
                    logger.info(f"Found {len(objectsFound.get('Contents', []))} objects in files path")
                except Exception as e:
                    logger.exception(f"Error listing file objects: {e}")

                assets = []
                if 'Contents' in objectsFound:
                    files = [x['Key'] for x in objectsFound['Contents'] if '/' != x['Key'][-1]]
                    logger.info("Files present in pipeline output asset folder:")
                    logger.info(files)
                    
                    if files:
                        try:
                            # Create external upload record
                            upload_id = create_external_upload_record(
                                event['assetId'],
                                event['databaseId'],
                                "assetFile",
                                filesPathKey
                            )
                            
                            # Update S3 object metadata for each file
                            for file in files:
                                update_s3_object_metadata(
                                    file,
                                    event['assetId'],
                                    event['databaseId'],
                                    upload_id,
                                    bucket_name
                                )
                            
                            # Process the external upload
                            result = process_external_upload(
                                upload_id,
                                event['assetId'],
                                event['databaseId'],
                                "assetFile",
                                files,
                                filesPathKey,
                                requestContext
                            )
                            
                            if result:
                                logger.info("Asset file upload completed successfully")
                            else:
                                logger.error("Asset file upload failed")
                                
                        except Exception as e:
                            logger.exception(f"Error processing asset file upload: {e}")
                    else:
                        logger.warning("No files found in asset output folder")

            #Handle metadata outputs (needs to happen after S3 file processing)
            if('metadataPathKey' in event):
                metadataPathKey = event['metadataPathKey']
                logger.info(f"Processing metadata outputs from: {metadataPathKey}")

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, metadataPathKey)
                    logger.info(f"Found {len(objectsFound.get('Contents', []))} objects in metadata path")
                except Exception as e:
                    logger.exception(f"Error listing metadata objects: {e}")

                if 'Contents' in objectsFound:
                    # Log all objects found for debugging
                    all_keys = [obj['Key'] for obj in objectsFound['Contents']]
                    logger.info(f"All objects in metadata path: {all_keys}")
                    
                    # Filter to metadata and attribute JSON files
                    metadata_files = filter_metadata_files(objectsFound['Contents'])
                    attribute_files = filter_attribute_files(objectsFound['Contents'])
                    
                    logger.info(f"Found {len(metadata_files)} metadata files and {len(attribute_files)} attribute files")
                    
                    # Log filtered files for debugging
                    if metadata_files:
                        metadata_keys = [obj['Key'] for obj in metadata_files]
                        logger.info(f"Metadata files: {metadata_keys}")
                    if attribute_files:
                        attribute_keys = [obj['Key'] for obj in attribute_files]
                        logger.info(f"Attribute files: {attribute_keys}")
                    
                    # Check for asset-level metadata (asset.metadata.json)
                    asset_metadata_file = None
                    file_metadata_files = []
                    
                    for file_obj in metadata_files:
                        filename = os.path.basename(file_obj['Key'])
                        if filename == 'asset.metadata.json':
                            asset_metadata_file = file_obj
                            logger.info(f"Found asset-level metadata file: {file_obj['Key']}")
                        else:
                            file_metadata_files.append(file_obj)
                            logger.info(f"Found file-level metadata: {file_obj['Key']}")
                    
                    # Process asset-level metadata (asset.metadata.json)
                    if asset_metadata_file:
                        try:
                            process_metadata_file(
                                bucket_name,
                                asset_metadata_file['Key'],
                                metadataPathKey,
                                event['databaseId'],
                                event['assetId'],
                                None,  # No file path for asset-level metadata
                                'metadata',
                                requestContext
                            )
                        except Exception as e:
                            logger.exception(f"Error processing asset metadata: {e}")
                    
                    # Process each file-level metadata
                    for file_obj in file_metadata_files:
                        try:
                            # Extract the file path from the metadata filename
                            file_path = extract_file_path_from_metadata_filename(
                                file_obj['Key'],
                                metadataPathKey
                            )
                            
                            if file_path:
                                logger.info(f"Processing metadata for file: {file_path}")
                                process_metadata_file(
                                    bucket_name,
                                    file_obj['Key'],
                                    metadataPathKey,
                                    event['databaseId'],
                                    event['assetId'],
                                    file_path,
                                    'metadata',
                                    requestContext
                                )
                            else:
                                logger.error(f"Could not extract file path from: {file_obj['Key']}")
                        except Exception as e:
                            logger.exception(f"Error processing file metadata {file_obj['Key']}: {e}")
                    
                    # Process each file-level attribute
                    for file_obj in attribute_files:
                        try:
                            # Extract the file path from the attribute filename
                            file_path = extract_file_path_from_metadata_filename(
                                file_obj['Key'],
                                metadataPathKey
                            )
                            
                            if file_path:
                                logger.info(f"Processing attributes for file: {file_path}")
                                process_metadata_file(
                                    bucket_name,
                                    file_obj['Key'],
                                    metadataPathKey,
                                    event['databaseId'],
                                    event['assetId'],
                                    file_path,
                                    'attribute',
                                    requestContext
                                )
                            else:
                                logger.error(f"Could not extract file path from: {file_obj['Key']}")
                        except Exception as e:
                            logger.exception(f"Error processing file attribute {file_obj['Key']}: {e}")


            response['statusCode'] = 200
            response['body'] = json.dumps({"message": "Workflow Execution Output Processing Complete"})
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