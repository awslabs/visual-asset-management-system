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
    read_metadata_function = os.environ['READ_METADATA_LAMBDA_FUNCTION_NAME']
    create_metadata_function = os.environ['CREATE_METADATA_LAMBDA_FUNCTION_NAME']
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


def _lambda_read_metadata(payload): return client.invoke(FunctionName=read_metadata_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

def _lambda_create_metadata(payload): return client.invoke(FunctionName=create_metadata_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

def _lambda_file_ingestion(payload): return client.invoke(FunctionName=file_upload_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

# def attach_execution_assets(assets, execution_id, database_id, asset_id, workflow_id):
#     logger.info("Attaching assets to execution")

#     asset_table = dynamodb.Table(asset_Database)
#     all_assets = assets

#     source_assets = asset_table.query(
#         KeyConditionExpression=Key('databaseId').eq(database_id) & Key('assetId').begins_with(
#             asset_id))
#     logger.info("Source assets: ")
#     logger.info(source_assets)
#     if source_assets['Items']:
#         all_assets.append(source_assets['Items'][0])

#     table = dynamodb.Table(workflow_execution_database)
#     pk = f'{asset_id}-{workflow_id}'

#     table.update_item(
#         Key={'pk': pk, 'sk': execution_id},
#         UpdateExpression='SET #attr1 = :val1',
#         ExpressionAttributeNames={'#attr1': 'assets'},
#         ExpressionAttributeValues={':val1': all_assets}
#     )

#     return

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

def create_external_upload_record(asset_id, database_id, upload_type, temporary_prefix):
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
            temporaryPrefix=temporary_prefix
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

def process_external_upload(upload_id, asset_id, database_id, upload_type, files, temporary_prefix, request_context):
    """Process an external upload using the fileIngestion Lambda"""
    try:
        # Prepare the request payload
        file_list = []
        for file_key in files:
            # Extract the file name from the key
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
    """Filter S3 objects to only include files ending with _metadata.json"""
    return [obj for obj in objects_list if obj['Key'].endswith('_metadata.json') and '/' != obj['Key'][-1]]


def parse_file_metadata_path(s3_key, metadata_path_key):
    """
    Extract the target file path from metadata filename.
    Example: 'prefix/folder1/folder2/boopy.glb_metadata.json' -> '/folder1/folder2/boopy.glb'
    """
    try:
        # Remove the metadata_path_key prefix
        if s3_key.startswith(metadata_path_key):
            relative_path = s3_key[len(metadata_path_key):]
        else:
            relative_path = s3_key
        
        # Remove '_metadata.json' suffix
        if relative_path.endswith('_metadata.json'):
            relative_path = relative_path[:-len('_metadata.json')]
        
        # Ensure leading slash
        if not relative_path.startswith('/'):
            relative_path = '/' + relative_path
        
        return relative_path
    except Exception as e:
        logger.exception(f"Error parsing file metadata path: {e}")
        return None


def build_file_prefix(asset_id, relative_path):
    """
    Build full prefix for file-level metadata.
    Example: asset_id='x1c688932-ad0f-49c0-971d-578939126947', relative_path='/folder1/folder2/boopy.glb'
    Returns: '/x1c688932-ad0f-49c0-971d-578939126947/folder1/folder2/boopy.glb'
    """
    # Ensure asset_id has leading slash
    if not asset_id.startswith('/'):
        asset_id = '/' + asset_id
    
    # Remove leading slash from relative_path if present (we'll add it back)
    if relative_path.startswith('/'):
        relative_path = relative_path[1:]
    
    return f"{asset_id}/{relative_path}"


def process_metadata_fields(json_data):
    """
    Process JSON fields according to rules:
    - Strings: add as-is
    - Lists: join with commas if all elements are strings
    - Dicts: convert entire dict to JSON string
    - Skip: assetId, databaseId fields
    Returns: processed metadata dictionary
    """
    processed_metadata = {}
    
    for k, v in json_data.items():
        # Skip assetId and databaseId fields
        if str(k) in ['assetId', 'databaseId']:
            continue
        
        if isinstance(v, dict):
            # Convert entire dictionary to JSON string
            processed_metadata[str(k)] = json.dumps(v)
        elif isinstance(v, list):
            # Check if all elements are strings, if so join with commas
            if v and all(isinstance(item, str) for item in v):
                processed_metadata[str(k)] = ",".join(v)
            else:
                # Convert list to JSON string if not all strings
                processed_metadata[str(k)] = json.dumps(v)
        else:
            # Add string values as-is (skip empty strings)
            if str(v) != "":
                processed_metadata[str(k)] = str(v)
    
    return processed_metadata


def get_or_create_metadata(database_id, asset_id, request_context, prefix=None):
    """
    Query existing metadata or create new entry with defaults.
    Returns: metadata dictionary
    """
    # Build Lambda payload for read_metadata
    l_payload = {
        "requestContext": request_context,
        "pathParameters": {
            "databaseId": database_id,
            "assetId": asset_id
        }
    }
    
    # Add prefix query parameter if provided (for file-level metadata)
    if prefix:
        l_payload["queryStringParameters"] = {
            "prefix": prefix
        }
    
    logger.info(f"Getting metadata for assetId={asset_id}, prefix={prefix}")
    logger.info(l_payload)
    
    try:
        read_metadata_response = _lambda_read_metadata(l_payload)
        stream = read_metadata_response['Payload']
        
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info("readMetadata response:")
            logger.info(json_response)
            
            # Check if metadata exists
            if "statusCode" in json_response and json_response["statusCode"] == 200:
                if "body" in json_response:
                    response_body = json.loads(json_response['body'])
                    if "metadata" in response_body:
                        return response_body['metadata']
            
            # Metadata doesn't exist, create new with defaults
            logger.info("Metadata not found, creating new entry with defaults")
            metadata = {
                "databaseId": database_id,
                "assetId": prefix if prefix else asset_id,
                "_metadata_last_updated": datetime.now().isoformat()
            }
            return metadata
    except Exception as e:
        logger.exception(f"Error getting metadata: {e}")
        # Return new metadata with defaults on error
        return {
            "databaseId": database_id,
            "assetId": prefix if prefix else asset_id,
            "_metadata_last_updated": datetime.now().isoformat()
        }


def save_metadata(database_id, asset_id, metadata, request_context, prefix=None):
    """
    Save metadata via create_metadata Lambda.
    Handles prefix parameter for file-level metadata.
    """
    # Remove assetId and databaseId from metadata (they are primary/sort keys)
    metadata_to_save = {k: v for k, v in metadata.items() if k not in ['assetId', 'databaseId']}
    
    # Make sure we have something to save
    if not metadata_to_save:
        logger.warning("Empty metadata dictionary, skipping save")
        return False
    
    # Build Lambda payload
    l_payload = {
        "requestContext": request_context,
        "pathParameters": {
            "databaseId": database_id,
            "assetId": asset_id
        },
        "body": json.dumps({
            "version": "1",
            "metadata": metadata_to_save
        })
    }
    
    # Add prefix query parameter if provided (for file-level metadata)
    if prefix:
        l_payload["queryStringParameters"] = {
            "prefix": prefix
        }
    
    logger.info(f"Saving metadata for assetId={asset_id}, prefix={prefix}")
    logger.info(l_payload)
    
    try:
        create_metadata_response = _lambda_create_metadata(l_payload)
        stream = create_metadata_response['Payload']
        
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info("createMetadata response:")
            logger.info(json_response)
            
            if "statusCode" in json_response:
                if json_response['statusCode'] == 200:
                    logger.info("Metadata saved successfully")
                    return True
                else:
                    logger.error(f"Error saving metadata, status code: {json_response['statusCode']}")
                    return False
            else:
                logger.error("No status code in createMetadata response")
                return False
    except Exception as e:
        logger.exception(f"Error saving metadata: {e}")
        return False


def process_root_metadata(bucket_name, s3_key, database_id, asset_id, request_context):
    """Process root asset metadata from asset_metadata.json"""
    try:
        logger.info(f"Processing root metadata from: {s3_key}")
        
        # Get existing metadata
        metadata = get_or_create_metadata(database_id, asset_id, request_context)
        
        # Read JSON file from S3
        objectResponse = s3c.get_object(Bucket=bucket_name, Key=s3_key)
        objectData = objectResponse['Body'].read().decode("utf-8")
        
        try:
            data = json.loads(objectData)
            logger.info(f"Root metadata JSON content: {data}")
            
            # Process fields and merge with existing metadata
            processed_fields = process_metadata_fields(data)
            metadata.update(processed_fields)
            
            # Update timestamp
            metadata['_metadata_last_updated'] = datetime.now().isoformat()
            
            # Save metadata
            save_metadata(database_id, asset_id, metadata, request_context)
            
        except json.JSONDecodeError as e:
            logger.error(f"Root metadata file is not valid JSON: {e}")
    except Exception as e:
        logger.exception(f"Error processing root metadata: {e}")


def process_file_level_metadata(bucket_name, s3_key, metadata_path_key, database_id, asset_id, request_context):
    """Process file-level metadata from *_metadata.json files"""
    try:
        logger.info(f"Processing file-level metadata from: {s3_key}")
        
        # Parse the relative path from the metadata filename
        relative_path = parse_file_metadata_path(s3_key, metadata_path_key)
        if not relative_path:
            logger.error(f"Could not parse relative path from: {s3_key}")
            return
        
        # Build full prefix for file-level metadata
        full_prefix = build_file_prefix(asset_id, relative_path)
        logger.info(f"File-level metadata prefix: {full_prefix}")
        
        # Get existing metadata for this file
        metadata = get_or_create_metadata(database_id, asset_id, request_context, prefix=full_prefix)
        
        # Read JSON file from S3
        objectResponse = s3c.get_object(Bucket=bucket_name, Key=s3_key)
        objectData = objectResponse['Body'].read().decode("utf-8")
        
        try:
            data = json.loads(objectData)
            logger.info(f"File-level metadata JSON content: {data}")
            
            # Process fields and merge with existing metadata
            processed_fields = process_metadata_fields(data)
            metadata.update(processed_fields)
            
            # Update timestamp
            metadata['_metadata_last_updated'] = datetime.now().isoformat()
            
            # Save metadata with prefix
            save_metadata(database_id, asset_id, metadata, request_context, prefix=full_prefix)
            
        except json.JSONDecodeError as e:
            logger.error(f"File-level metadata file is not valid JSON: {e}")
    except Exception as e:
        logger.exception(f"Error processing file-level metadata: {e}")


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
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset, "PUT"):
                operation_allowed_on_asset = True
        
        if operation_allowed_on_asset:
            # Get bucket details from asset's bucketId
            bucketDetails = get_default_bucket_details(asset['bucketId'])
            bucket_name = bucketDetails['bucketName']

            #Handle preview outputs
            if ('previewPathKey' in event):
                previewPathKey = event['previewPathKey']

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, previewPathKey)
                except Exception as e:
                    logger.error(e)

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

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, filesPathKey)
                except Exception as e:
                    logger.error(e)

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
                            
                            # if result and "assetType" in result:
                            #     # Create a simplified asset object for attachment
                            #     asset = {
                            #         "databaseId": event['databaseId'],
                            #         "assetId": event['assetId'],
                            #         "assetType": result["assetType"],
                            #         "version": result["version"],
                            #         "pipeline": event.get('pipeline', ''),
                            #         "executionId": event.get('executionId', '')
                            #     }
                            #     assets.append(asset)
                            #     logger.info("Asset file upload completed successfully")
                            # else:
                            #     logger.error("Asset file upload failed or returned incomplete data")
                                
                            # # Attach assets to execution record
                            # attach_execution_assets(assets, event['executionId'], event['databaseId'], event['assetId'], event['workflowId'])
                        except Exception as e:
                            logger.exception(f"Error processing asset file upload: {e}")
                    else:
                        logger.warning("No files found in asset output folder")

            #Handle metadata outputs (needs to happen after S3)
            if('metadataPathKey' in event):
                metadataPathKey = event['metadataPathKey']

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, metadataPathKey)
                except Exception as e:
                    logger.error(e)

                if 'Contents' in objectsFound:
                    # Filter to only metadata JSON files
                    metadata_files = filter_metadata_files(objectsFound['Contents'])
                    logger.info(f"Found {len(metadata_files)} metadata files")
                    
                    # Separate root metadata from file-level metadata
                    root_metadata_file = None
                    file_metadata_files = []
                    
                    for file_obj in metadata_files:
                        filename = os.path.basename(file_obj['Key'])
                        if filename == 'asset_metadata.json':
                            root_metadata_file = file_obj
                            logger.info(f"Found root metadata file: {file_obj['Key']}")
                        else:
                            file_metadata_files.append(file_obj)
                            logger.info(f"Found file-level metadata: {file_obj['Key']}")
                    
                    # Process root metadata (asset_metadata.json)
                    if root_metadata_file:
                        try:
                            process_root_metadata(
                                bucket_name,
                                root_metadata_file['Key'],
                                event['databaseId'],
                                event['assetId'],
                                requestContext
                            )
                        except Exception as e:
                            logger.exception(f"Error processing root metadata: {e}")
                    
                    # Process each file-level metadata
                    for file_obj in file_metadata_files:
                        try:
                            process_file_level_metadata(
                                bucket_name,
                                file_obj['Key'],
                                metadataPathKey,
                                event['databaseId'],
                                event['assetId'],
                                requestContext
                            )
                        except Exception as e:
                            logger.exception(f"Error processing file-level metadata {file_obj['Key']}: {e}")


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
