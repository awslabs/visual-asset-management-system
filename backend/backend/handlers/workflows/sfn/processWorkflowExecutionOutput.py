#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from botocore.config import Config
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key
from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    UPLOAD_ID_METADATA_KEY,
)
from common.s3PathPatterns import ALLOWED_PREVIEW_FILE_EXTENSIONS
from common.apiRoutes import API_UPLOAD_COMPLETE_EXTERNAL
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import success, validation_error, VAMSGeneralErrorResponse
from common.s3 import validateUnallowedFileExtensionAndContentType, list_all_objects
from models.assetsV3 import AssetUploadTableModel
from common.workflows import executionRecords as er
from common.workflows import executionOutputs as eo
from common.workflows import outputPathExtension as ope

logger = safeLogger(service_name="ProcessWorkflowExecutionOutput")

# Constants
UPLOAD_EXPIRATION_DAYS = 1  # TTL for upload records for pipeline output
# Cap on PipelineExecutionOutputMetadata rows recorded for one execution (one row per applied
# key). Bounds the sequential DynamoDB writes a metadata-heavy run performs within the lambda
# timeout; rows past the cap are dropped from the provenance record.
MAX_RECORDED_OUTPUT_METADATA_ROWS = 2000
# Generic write-back failure summary recorded on the execution when a metadata/attribute file
# cannot be read, parsed, or applied (specifics go to the log).
METADATA_WRITE_BACK_FAILURE = "The asset metadata write-back failed."
# Bound on the threads used to stamp staged output objects with their asset/upload provenance.
# An output block can hold thousands of objects and each stamp is a full-object copy, so they run
# in parallel to fit the lambda timeout; the S3 connection pool below is sized to match.
MAX_PARALLEL_S3_WORKERS = 16

try:
    s3_asset_buckets_table = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    metadata_service_function = os.environ['METADATA_SERVICE_LAMBDA_FUNCTION_NAME']
    file_upload_function = os.environ['FILE_UPLOAD_LAMBDA_FUNCTION_NAME']
    asset_Database = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    asset_upload_table_name = get_table_name(ResourceKeys.ASSET_UPLOADS_STORAGE_TABLE)
    workflow_execution_database_v2 = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
    pipeline_execution_output_files_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE)
    pipeline_execution_output_metadata_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE)
    pipeline_execution_output_results_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE)
    pipeline_execution_logs_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_LOGS_STORAGE_TABLE)
    # Shared workflow SFN log group (same group for every workflow). Read from env, not
    # the ASL event, so it applies to executions of workflows that were not redeployed
    # with a newer ASL. Optional: empty string disables CloudWatch log retrieval.
    workflow_execution_log_group_arn = os.environ.get("WORKFLOW_EXECUTION_LOG_GROUP_ARN", "")
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
# Match the connection pool to the worker count so the parallel provenance stamps don't queue
# on a too-small pool (botocore defaults to 10).
s3_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                   max_pool_connections=MAX_PARALLEL_S3_WORKERS)

s3c = boto3.client('s3', config=s3_config)
s3r = boto3.resource('s3', config=s3_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)
client = boto3.client('lambda', config=retry_config)
logs_client = boto3.client('logs', config=retry_config)
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

def _head_listed_objects(bucketName: str, objects):
    """HEAD every listed object once, annotating each with its 'ContentType', 'VersionId' and
    'Metadata'.

    One HEAD per object serves the executable-type check below, the recorded output descriptors and
    the provenance stamp, so an output block costs a single HEAD per object however many consumers
    read it. The heads run in a bounded pool because a listing can hold thousands of objects."""
    def _head_one(obj):
        head = s3c.head_object(Bucket=bucketName, Key=obj['Key'])
        obj['ContentType'] = head.get('ContentType', '') or ''
        obj['VersionId'] = head.get('VersionId', '') or ''
        obj['Metadata'] = head.get('Metadata', {}) or {}

    if not objects:
        return
    max_workers = min(MAX_PARALLEL_S3_WORKERS, len(objects))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # list() drains the iterator so a head failure surfaces here rather than being dropped.
        list(executor.map(_head_one, objects))


def verify_get_path_objects(bucketName: str, pathPrefix: str):

    # Page the listing to exhaustion: an output block can hold more objects than a single
    # list_objects_v2 page returns. The result mirrors the list_objects_v2 response shape
    # ('Contents' present only when objects exist) that the callers read.
    objects = list_all_objects(bucketName, pathPrefix, client=s3c)

    #Do MIME check on whatever is uploaded to S3 at this point for this asset, before we do DynamoDB insertion, to validate it's not malicious
    _head_listed_objects(bucketName, objects)
    for obj in objects:
        if not validateUnallowedFileExtensionAndContentType(obj['Key'], obj.get('ContentType', '')):
            raise Exception("Pipeline uploaded objects contains a potentially malicious executable type object. Unable to process asset upload.")

    all_outputs = {'Contents': objects} if objects else {}
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

def update_s3_object_metadata(key, asset_id, database_id, upload_id, bucket_name,
                              content_type=None, existing_metadata=None):
    """Update S3 object metadata with asset and upload information.

    content_type/existing_metadata carry the object's already-read HEAD so a caller that listed the
    object does not re-read it; either being unset falls back to a head_object here."""
    try:
        # Get current object metadata
        if content_type is None or existing_metadata is None:
            head_response = s3c.head_object(Bucket=bucket_name, Key=key)
            content_type = head_response.get('ContentType', 'application/octet-stream')
            existing_metadata = head_response.get('Metadata', {})

        # Merge existing metadata with new metadata
        metadata = {**existing_metadata, DATABASE_ID_METADATA_KEY: database_id, ASSET_ID_METADATA_KEY: asset_id, UPLOAD_ID_METADATA_KEY: upload_id}

        # Use boto3 resource copy() which automatically handles multipart for large files
        copy_source = {
            'Bucket': bucket_name,
            'Key': key
        }
        s3r.Object(bucket_name, key).copy(
            copy_source,
            ExtraArgs={
                'ContentType': content_type,
                'Metadata': metadata,
                'MetadataDirective': 'REPLACE',
                # Grant the bucket owner full control so a version written into a
                # cross-account asset bucket is owned/readable by that account.
                'ACL': 'bucket-owner-full-control'
            }
        )

        return True
    except Exception as e:
        logger.exception(f"Error updating S3 object metadata: {e}")
        return False


def _stamp_output_objects(objects, asset_id, database_id, upload_id, bucket_name):
    """Stamp every staged output object with its asset/upload provenance metadata, in a bounded pool
    so a thousand-file output completes within the lambda timeout.

    Each object carries the content type and user metadata read when it was listed, so no additional
    head_object is issued. Returns True only when every stamp succeeded; ingesting an unstamped
    object would land a file in the asset with no provenance."""
    if not objects:
        return True

    def _stamp_one(obj):
        return update_s3_object_metadata(
            obj['Key'], asset_id, database_id, upload_id, bucket_name,
            content_type=obj.get('ContentType') or None,
            existing_metadata=obj.get('Metadata'))

    max_workers = min(MAX_PARALLEL_S3_WORKERS, len(objects))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_stamp_one, objects))
    return all(results)


def _ingestion_outcome(result):
    """Read a file-ingestion response body as (all_succeeded, ingested_relative_keys).

    The ingestion API answers 200 whenever at least ONE file succeeded, reporting the all-succeeded
    flag in overallSuccess and per-file outcomes in fileResults, so a partially failed write-back is
    only visible in the body. ingested_relative_keys is None when the body carries no per-file
    results (nothing to narrow the recorded outputs by)."""
    if not result:
        return False, None
    all_succeeded = bool(result.get('overallSuccess', True))
    file_results = result.get('fileResults')
    if not isinstance(file_results, list):
        return all_succeeded, None
    ingested = {entry.get('relativeKey', '') for entry in file_results
                if isinstance(entry, dict) and entry.get('success')}
    return all_succeeded, ingested


def process_external_upload(upload_id, asset_id, database_id, upload_type, files, baseFileKeyPrefix, request_context, workflow_id=None, execution_id=None, change_user_id=None, file_base_execution_path_extension="/", source_bucket=None, mfa_enabled=None):
    """Process an external upload using the fileIngestion Lambda.

    file_base_execution_path_extension is inserted into each output file's relative path immediately
    before the final filename (see common.workflows.outputPathExtension), so each pipeline's own
    output folder structure is preserved and the extension names the leaf folder. It defaults to '/'
    (no extra path segment). It applies to asset FILE outputs, whose key is path-structured; preview
    outputs are basename-only and are unaffected.

    mfa_enabled is the launching end user's MFA state, propagated on the cross-call so the
    delegated write is authorized with the same MFA-gated roles the launch was. None leaves it
    unset, which the cross-call treats as an MFA-satisfied system call."""
    extension = file_base_execution_path_extension
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

                # Insert the output base-execution path extension before the file's own filename,
                # keeping the pipeline's output folder structure ahead of it.
                file_name = ope.apply_output_path_extension(file_name, extension)
            else:
                # For other upload types (like assetPreview), just use the filename
                file_name = os.path.basename(file_key)

            # Add to file list
            file_list.append({
                "relativeKey": file_name,
                "tempKey": file_key
            })

        # Create the request body. sourceBucket tells fileIngestion to READ the tempKey files from
        # the run bucket (where the pipelines staged them) while still writing to the asset bucket.
        body = {
            "assetId": asset_id,
            "databaseId": database_id,
            "uploadType": upload_type,
            "files": file_list,
            "workflowId": workflow_id,
            "workflowExecutionId": execution_id,
            "changeUserId": change_user_id
        }
        if source_bucket:
            body["sourceBucket"] = source_bucket
        
        # Create the Lambda payload to simulate an API Gateway request. The write-back is a
        # system action attributed to the executing user (SYSTEM_USER for auto-triggers), so
        # identity travels as a lambdaCrossCall rather than relying on the stored execution
        # request context, which carries no authorizer claims for trigger-launched executions.
        # A cross-call on behalf of an end user carries that user's MFA state so MFA-gated roles
        # are not activated for a non-MFA session.
        cross_call = {"userName": change_user_id or "SYSTEM_USER"}
        if mfa_enabled is not None:
            cross_call["mfaEnabled"] = bool(mfa_enabled)
        lambda_payload = {
            "requestContext": request_context,
            "pathParameters": {
                "uploadId": upload_id
            },
            "body": json.dumps(body),
            "lambdaCrossCall": cross_call,
        }
        # Synthetic internal route -- must match API_UPLOAD_COMPLETE_EXTERNAL in
        # common/apiRoutes.py, which the uploadFile dispatcher matches against.
        lambda_payload["requestContext"]["http"]["path"] = API_UPLOAD_COMPLETE_EXTERNAL.path.replace(
            "{uploadId}", upload_id
        )
        lambda_payload["requestContext"]["http"]["method"] = "POST"
        
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


def extract_file_path_from_metadata_filename(s3_key, metadata_path_key,
                                             file_base_execution_path_extension="/"):
    """
    Extract the target file path from metadata/attribute filename.
    Example: 'prefix/folder1/folder2/boopy.glb.metadata.json' -> 'folder1/folder2/boopy.glb'

    The output base-execution path extension is applied to the result, so the derived path is where
    the file output actually landed in the asset. Defaults to '/' (no extra segment).
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

        return ope.apply_output_path_extension(
            relative_path, file_base_execution_path_extension)
    except Exception as e:
        logger.exception(f"Error parsing file path from metadata filename: {e}")
        return None


def _metadata_value_text(value):
    """Render a metadata/attribute value as the string the output-metadata row stores,
    trimmed to the single-field byte budget."""
    if not isinstance(value, str):
        try:
            value = json.dumps(value)
        except (TypeError, ValueError):
            value = str(value)
    text, _truncated = er.truncate_text(value)
    return text


def _applied_metadata_entries(metadata_items):
    """Extract the {metadataKey, metadataValue} pairs a metadata/attribute file applies, skipping
    entries with no key (nothing to record against)."""
    entries = []
    for item in metadata_items:
        if not isinstance(item, dict):
            continue
        key = item.get('metadataKey') or item.get('attributeKey') or ""
        if not key:
            continue
        value = item.get('metadataValue', item.get('attributeValue', ""))
        entries.append({"metadataKey": key, "metadataValue": _metadata_value_text(value)})
    return entries


def process_metadata_file(bucket_name, s3_key, metadata_path_key, database_id, asset_id, file_path, metadata_type, request_context):
    """Process metadata or attribute file from pipeline output.

    Returns the {metadataKey, metadataValue} entries the file applied (empty when the file carried
    no keys) so the caller can record them as output provenance, or None when the file could not be
    read/parsed or the metadata service rejected the write."""
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
                return None

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
                    return _applied_metadata_entries(data['metadata'])
                else:
                    logger.error(f"Error processing {metadata_type}: {json_response}")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {s3_key}: {e}")
    except Exception as e:
        logger.exception(f"Error processing {metadata_type} file {s3_key}: {e}")
    return None


def _record_applied_metadata(collected, target_file_path, source_key, entries):
    """Append one output-metadata descriptor per applied key onto `collected`, capped at
    MAX_RECORDED_OUTPUT_METADATA_ROWS for the whole execution. target_file_path is '/' for
    asset-level metadata and the asset-relative path (leading slash) for file-level."""
    for entry in entries:
        if len(collected) >= MAX_RECORDED_OUTPUT_METADATA_ROWS:
            logger.warning(
                f"Reached the {MAX_RECORDED_OUTPUT_METADATA_ROWS}-row output-metadata recording "
                f"cap; remaining keys from {source_key} are not recorded")
            return
        collected.append({
            "targetFilePath": target_file_path,
            "metadataKey": entry["metadataKey"],
            "metadataValue": entry["metadataValue"],
            "sourceMetadataFileRelativePath": source_key,
        })


def _collect_output_descriptors(objects_found, file_type, prefix, bucket_name,
                                file_base_execution_path_extension="/"):
    """Build OutputFiles descriptors from an S3 listing relative to `prefix`, carrying each object's
    contentType and S3 versionId from the HEAD verify_get_path_objects already performed, so a large
    output block costs one HEAD per object rather than one per consumer. A listing that was not
    annotated falls back to a best-effort per-object head_object (empty on failure).

    The recorded relativeFilePath reflects where the output lands in the asset, so the output
    base-execution path extension (inserted before the final filename) is applied here too, keeping
    the recorded provenance aligned with the actual write location (and with the asset file
    version-history join). Defaults to '/' (no extra segment)."""
    descriptors = []
    for obj in objects_found.get('Contents', []):
        key = obj['Key']
        if key.endswith('/'):
            continue
        relative = key[len(prefix):] if key.startswith(prefix) else key
        relative = ope.apply_output_path_extension(
            relative, file_base_execution_path_extension)
        content_type = obj.get('ContentType', '') or ''
        version_id = obj.get('VersionId', '') or ''
        if 'VersionId' not in obj:
            try:
                head = s3c.head_object(Bucket=bucket_name, Key=key)
                content_type = head.get('ContentType', '') or ''
                version_id = head.get('VersionId', '') or ''
            except Exception as e:
                logger.info(f"Could not read S3 version for {key} (non-critical): {e}")
        descriptors.append({
            "fileType": file_type,
            "relativeFilePath": relative,
            # s3Key/s3VersionId are the LISTED object's locator, so the bucket recorded with them
            # is the bucket they were listed in (the run I/O bucket), which may differ from the
            # output asset's bucket.
            "s3Bucket": bucket_name,
            "s3Key": key,
            "fileSize": obj.get('Size', 0),
            "contentType": content_type,
            "s3VersionId": version_id,
        })
    return descriptors


def _log_group_name_from_arn(log_group_arn):
    """Extract the CloudWatch log group NAME from its ARN.
    ARN shape: arn:partition:logs:region:acct:log-group:NAME(:*). Returns '' if unparseable.
    (Log group names cannot contain ':' so trimming a trailing ':*' is safe.)"""
    if not log_group_arn:
        return ""
    parts = log_group_arn.split(":log-group:")
    if len(parts) < 2:
        return ""
    name = parts[1]
    if name.endswith(":*"):
        name = name[:-2]
    return name


def _fetch_execution_logs(log_group_arn, execution_id, limit_events=50):
    """Best-effort fetch of CloudWatch log events for ONE workflow execution.

    Returns the full recent execution log (not just errors), captured for every
    completed run. All workflow state machines log to a single shared group; Step
    Functions STANDARD logging writes a per-execution log stream named for the execution
    (its name == our executionId) and tags every event with execution_arn. We scope the
    read to this execution by filtering on the execution id, so the shared group does not
    leak other executions' logs into this record. Returns (text, stream_name); ('', '')
    on any failure or when logging is not configured (logs are non-critical)."""
    log_group_name = _log_group_name_from_arn(log_group_arn)
    if not log_group_name or not execution_id:
        return "", ""
    try:
        # filterPattern matches the execution id within events (it appears in the
        # execution_arn / execution name that includeExecutionData emits), scoping the
        # result to just this execution's events within the shared group.
        resp = logs_client.filter_log_events(
            logGroupName=log_group_name,
            filterPattern=f'"{execution_id}"',
            limit=limit_events,
        )
        events = resp.get('events', [])
        text = "\n".join(e.get('message', '') for e in events)
        stream = events[0].get('logStreamName', '') if events else ''
        return text, stream
    except Exception as e:
        logger.info(f"Could not fetch CloudWatch logs (non-critical): {e}")
        return "", ""


def record_execution_outputs(dynamo, workflow_execution_id, end_state_pipeline_execution_id,
                             workflow_database_id, workflow_id, bucket_name,
                             output_files, output_metadata, output_results, result_log, execution_log,
                             log_group_arn, log_stream_name, execution_status, execution_error=""):
    """Write end-state pipeline output/metadata/log rows and set completion status
    on the end-state PipelineExecutions row and the V2 main execution row.

    The end-state lambda runs on normal workflow completion, so it captures the full
    execution log (`execution_log`) onto the main row's executionLog field for every
    completed run (success or failure), not just failures.

    bucket_name is the fallback bucket for output-file rows; a descriptor carrying its own
    's3Bucket' (the bucket its s3Key/s3VersionId were listed in) wins, so the stored
    (bucket, key, versionId) triple always resolves.

    execution_error carries the write-back failure summary for a non-success status; it is
    written to the main row's executionError field so the UI/CLI surface why the run did not
    fully succeed.

    No-op when no execution context is present (non-workflow/direct invocations).
    """
    if not workflow_execution_id or not end_state_pipeline_execution_id:
        logger.info("No workflow execution context; skipping execution-output recording")
        return

    stop_date = er.iso_now()

    # Output files (file + preview)
    if output_files:
        of_table = dynamo.Table(pipeline_execution_output_files_table)
        for f in output_files:
            of_table.put_item(Item=er.build_output_file_record(
                pipeline_execution_id=end_state_pipeline_execution_id,
                file_type=f.get("fileType", "file"),
                relative_file_path=f.get("relativeFilePath", ""),
                s3_bucket=f.get("s3Bucket") or bucket_name, s3_key=f.get("s3Key", ""),
                file_size=f.get("fileSize", 0), content_type=f.get("contentType", ""),
                s3_version_id=f.get("s3VersionId", ""),
            ))

    # Output metadata
    if output_metadata:
        om_table = dynamo.Table(pipeline_execution_output_metadata_table)
        for m in output_metadata:
            om_table.put_item(Item=er.build_output_metadata_record(
                pipeline_execution_id=end_state_pipeline_execution_id,
                target_file_path=m.get("targetFilePath", "/"),
                metadata_key=m.get("metadataKey", ""), metadata_value=m.get("metadataValue", ""),
                source_metadata_file_relative_path=m.get("sourceMetadataFileRelativePath", ""),
            ))

    # Output results (structured pipeline result files)
    if output_results:
        or_table = dynamo.Table(pipeline_execution_output_results_table)
        for r in output_results:
            or_table.put_item(Item=er.build_output_result_record(
                pipeline_execution_id=end_state_pipeline_execution_id,
                relative_file_path=r.get("relativeFilePath", ""),
                results_content=r.get("resultsContent", ""),
                s3_key=r.get("s3Key", ""),
            ))

    # Logs summary row (per-pipeline logs table)
    logs_table = dynamo.Table(pipeline_execution_logs_table)
    logs_table.put_item(Item=er.build_log_record(
        pipeline_execution_id=end_state_pipeline_execution_id, log_type="summary",
        result_log=result_log, error_log=execution_log,
        log_group_arn=log_group_arn, log_stream_name=log_stream_name,
    ))

    # Completion status on the end-state pipeline-execution row, conditioned on the row not being
    # terminal already so an in-flight task cannot regress a row the abort/error path finished.
    eo.set_pipeline_status(dynamo, pipeline_executions_table, end_state_pipeline_execution_id,
                           workflow_execution_id, execution_status, stop_date=stop_date)

    # Completion status + full execution log on the main V2 row, under the same terminal guard. The
    # log is captured on every completed run (success or failure) for later debugging by limited
    # roles.
    main_table = dynamo.Table(workflow_execution_database_v2)
    expression = "SET executionStopDate = :s, executionStatus = :st, executionLog = :lg"
    values = {":s": stop_date, ":st": execution_status, ":lg": execution_log or ""}
    if execution_error:
        expression += ", executionError = :er"
        values[":er"] = execution_error
    terminal_values = {f":term{index}": terminal
                       for index, terminal in enumerate(eo.TERMINAL_STATUSES)}
    values.update(terminal_values)
    condition = ("attribute_not_exists(executionStatus) OR NOT executionStatus IN ("
                 + ", ".join(terminal_values) + ")")
    try:
        main_table.update_item(
            Key={"workflowExecutionId": workflow_execution_id,
                 "workflowDatabaseId:workflowId": er.workflow_composite_key(workflow_database_id, workflow_id)},
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        logger.info("Main execution row already holds a terminal status; completion write skipped")


def _terminal_status(output_failures):
    """Map the write-back failure list to the (executionStatus, executionError) recorded for the
    run. Any failed ingestion or output listing means the execution did not deliver its outputs,
    so it is recorded FAILED with a deduplicated summary rather than SUCCEEDED."""
    if not output_failures:
        return "SUCCEEDED", ""
    unique = list(dict.fromkeys(output_failures))
    return "FAILED", " ".join(unique)


def _process_results_only(event):
    """End-state processing for a results-only execution (outputLocationType 'none'): no output asset,
    no file/metadata ingestion. Reads the pipeline's results text from the run I/O bucket and records
    only results + logs + completion status against the execution transaction. Used by pipelines that
    return a textual response (e.g. LLM-style) rather than asset files."""
    logger.info("Results-only execution (outputLocationType 'none'); recording results + logs, no asset ingestion")
    source_bucket = event.get('workflowExecutionS3InputOutputBucket', '')

    collected_output_results = []
    output_failures = []
    results_path_key = event.get('resultsPathKey', '')
    if results_path_key and source_bucket:
        objects_found = {}
        try:
            objects_found = verify_get_path_objects(source_bucket, results_path_key)
        except Exception as e:
            logger.exception(f"Error listing result objects: {e}")
            output_failures.append("Listing the pipeline results output failed.")
        for obj in objects_found.get('Contents', []):
            result_key = obj['Key']
            if result_key.endswith('/'):
                continue
            try:
                results_content = s3c.get_object(
                    Bucket=source_bucket, Key=result_key)['Body'].read().decode("utf-8")
                relative_file_path = "/" + result_key[len(results_path_key):].lstrip("/")
                collected_output_results.append({
                    "relativeFilePath": relative_file_path,
                    "resultsContent": results_content,
                    "s3Key": result_key,
                })
            except Exception as e:
                logger.exception(f"Error reading result file {result_key}: {e}")
                output_failures.append("Reading a pipeline results file failed.")

    try:
        log_group_arn = workflow_execution_log_group_arn
        workflow_execution_id = event.get('workflowExecutionId', '')
        end_state_pipeline_execution_id = event.get('endStatePipelineExecutionId', '')
        execution_log, stream_name = _fetch_execution_logs(log_group_arn, workflow_execution_id)
        status, error_summary = _terminal_status(output_failures)
        record_execution_outputs(
            dynamo=dynamodb,
            workflow_execution_id=workflow_execution_id,
            end_state_pipeline_execution_id=end_state_pipeline_execution_id,
            workflow_database_id=event.get('workflowDatabaseId', ''),
            workflow_id=event.get('workflowId', ''),
            bucket_name="",
            output_files=[], output_metadata=[],
            output_results=collected_output_results,
            result_log="Workflow Execution Output Processing Complete (results-only)",
            execution_log=execution_log,
            log_group_arn=log_group_arn, log_stream_name=stream_name,
            execution_status=status, execution_error=error_summary,
        )
    except Exception as e:
        logger.exception(f"Error recording results-only execution outputs (non-critical): {e}")

    return success(body={"message": "Workflow Execution Output Processing Complete (results-only)"})


def lambda_handler(event, context):
    logger.info(event)

    try:
        if 'body' in event:
            if isinstance(event['body'], str):
                try:
                    event['body'] = json.loads(event['body'])
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in request body: {e}")
                    return validation_error(body={"message": "Invalid JSON in request body"})
        else:
            message = "No Body in API Call"
            logger.error(message)
            return validation_error(body={"message": message})
        
        #sub in body for event
        event = event["body"]

        # Resolve the OUTPUT TARGET identity: outputs are written to this asset. The output
        # target is threaded explicitly as outputAssetId/outputDatabaseId (it equals the input
        # asset today, but is honored here so a divergent target works without further changes).
        # The rest of this handler keys off event['assetId']/event['databaseId'].
        event['assetId'] = event.get('outputAssetId', '')
        event['databaseId'] = event.get('outputDatabaseId', '')

        # Results-only executions (outputLocationType "none") write no asset files/metadata — only
        # results text + logs recorded against the execution transaction (e.g. an LLM-style pipeline
        # returning a textual response). Handle them before the asset-required validation below.
        if event.get('outputLocationType') == 'none' or (not event['assetId'] and not event['databaseId']):
            return _process_results_only(event)

        #Input validation
        if not event['databaseId']:
            message = "No outputDatabaseId in API Call"
            logger.error(message)
            return validation_error(body={"message": message})

        if not event['assetId']:
            message = "No outputAssetId in API Call"
            logger.error(message)
            return validation_error(body={"message": message})

        if 'executingRequestContext' not in event:
            message = "No executingRequestContext in API Call"
            logger.error(message)
            return validation_error(body={"message": message})

        if 'executingUserName' not in event:
            message = "No executingUserName in API Call"
            logger.error(message)
            return validation_error(body={"message": message})


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
            return validation_error(body={"message": message})

        requestContext = event['executingRequestContext']
        event["requestContext"] = requestContext
        claims_and_roles = request_to_claims(event)

        # Get existing asset
        asset = lookup_existing_asset(event['databaseId'], event['assetId'])
        if not asset:
            logger.error(f"Asset {event['assetId']} not found in database {event['databaseId']}")
            return validation_error(body={"message": "Asset not found in database"})

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
            # Get bucket details from asset's bucketId. bucket_name is the output asset's own bucket
            # (the write-back destination). source_bucket is where the pipelines actually STAGED the
            # output files/metadata/results/preview — the run I/O bucket threaded from the execute
            # handler (workflowExecutionS3InputOutputBucket). It defaults to the asset bucket for the
            # legacy single-bucket path where the two coincide.
            bucketDetails = get_default_bucket_details(asset['bucketId'])
            bucket_name = bucketDetails['bucketName']
            source_bucket = event.get('workflowExecutionS3InputOutputBucket') or bucket_name

            # The ingestion write-back is delegated on the executing end user's behalf, so it
            # carries that user's MFA state from the launch claims. A SYSTEM_USER execution
            # (trigger-launched) leaves it unset, keeping the system cross-call default.
            write_back_mfa_enabled = (
                None if event.get('executingUserName') == 'SYSTEM_USER'
                else bool(claims_and_roles.get("mfaEnabled", False)))

            # Accumulate output descriptors for execution-output recording.
            collected_output_files = []
            collected_output_metadata = []
            collected_output_results = []
            # Generic write-back failure summaries; a non-empty list records the run FAILED.
            output_failures = []

            #Handle preview outputs
            if ('previewPathKey' in event):
                previewPathKey = event['previewPathKey']
                logger.info(f"Processing preview outputs from: {previewPathKey}")

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(source_bucket, previewPathKey)
                    logger.info(f"Found {len(objectsFound.get('Contents', []))} objects in preview path")
                except Exception as e:
                    logger.exception(f"Error listing preview objects: {e}")
                    output_failures.append("Listing the pipeline preview output failed.")

                if 'Contents' in objectsFound:
                    files = [x['Key'] for x in objectsFound['Contents'] if '/' != x['Key'][-1]]

                    if(len(files) > 1):
                        logger.error("Multiple files present in pipeline output preview folder. Limiting to top 1 for now.")

                    # Filter for image files. ALLOWED_PREVIEW_FILE_EXTENSIONS is lowercase, so
                    # match on a lowercased key -- extensions are case-insensitive in S3 keys.
                    image_files = [f for f in files
                                   if f.lower().endswith(ALLOWED_PREVIEW_FILE_EXTENSIONS)]

                    if image_files:
                        # Only process the first image file
                        preview_file = image_files[0]
                        preview_objects = [obj for obj in objectsFound['Contents']
                                           if obj['Key'] == preview_file]

                        # The preview lands on the asset under its basename, so the recorded
                        # descriptor covers just the ingested image at that flattened path.
                        preview_folder = preview_file.rsplit("/", 1)[0] + "/" if "/" in preview_file else ""
                        preview_descriptors = _collect_output_descriptors(
                            {'Contents': preview_objects}, "preview", preview_folder, source_bucket)

                        try:
                            # Create external upload record
                            upload_id = create_external_upload_record(
                                event['assetId'],
                                event['databaseId'],
                                "assetPreview",
                                previewPathKey
                            )

                            # Stamp the staged file (in the source bucket) with its provenance. An
                            # unstamped object would be ingested carrying no asset/upload identity,
                            # so a failed stamp fails the write-back.
                            if not _stamp_output_objects(
                                    preview_objects, event['assetId'], event['databaseId'],
                                    upload_id, source_bucket):
                                raise VAMSGeneralErrorResponse(
                                    "Stamping the staged preview object failed")

                            # Process the external upload
                            result = process_external_upload(
                                upload_id,
                                event['assetId'],
                                event['databaseId'],
                                "assetPreview",
                                [preview_file],
                                previewPathKey,
                                requestContext,
                                workflow_id=event.get('workflowId'),
                                execution_id=event.get('workflowExecutionId'),
                                change_user_id=event.get('executingUserName'),
                                source_bucket=source_bucket,
                                mfa_enabled=write_back_mfa_enabled
                            )

                            # Ingestion answers 200 when only SOME files landed, so read the
                            # per-file outcome and record rows only for what actually landed.
                            all_ingested, ingested_keys = _ingestion_outcome(result)
                            collected_output_files.extend(
                                d for d in preview_descriptors
                                if result and (ingested_keys is None
                                               or d["relativeFilePath"] in ingested_keys))
                            if all_ingested and result:
                                logger.info("Preview upload completed successfully")
                            else:
                                logger.error("Preview upload failed")
                                output_failures.append("The asset preview write-back failed.")
                        except Exception as e:
                            logger.exception(f"Error processing preview upload: {e}")
                            output_failures.append("The asset preview write-back failed.")
                    elif files:
                        logger.error("No image files found in preview folder")
                        output_failures.append(
                            "The pipeline preview output contains no recognized image file.")

            #Handle asset file outputs
            if('filesPathKey' in event):
                filesPathKey = event['filesPathKey']
                logger.info(f"Processing asset file outputs from: {filesPathKey}")

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(source_bucket, filesPathKey)
                    logger.info(f"Found {len(objectsFound.get('Contents', []))} objects in files path")
                except Exception as e:
                    logger.exception(f"Error listing file objects: {e}")
                    output_failures.append("Listing the pipeline file output failed.")

                assets = []
                if 'Contents' in objectsFound:
                    file_objects = [x for x in objectsFound['Contents'] if '/' != x['Key'][-1]]
                    files = [x['Key'] for x in file_objects]
                    logger.info("Files present in pipeline output asset folder:")
                    logger.info(files)

                    file_descriptors = _collect_output_descriptors(
                        objectsFound, "file", filesPathKey, source_bucket,
                        file_base_execution_path_extension=event.get('outputFileBaseExecutionPathExtension', '/'))

                    if files:
                        try:
                            # Create external upload record
                            upload_id = create_external_upload_record(
                                event['assetId'],
                                event['databaseId'],
                                "assetFile",
                                filesPathKey
                            )

                            # Stamp each staged file (in the source bucket) with its provenance. An
                            # unstamped object would be ingested carrying no asset/upload identity,
                            # so a failed stamp fails the write-back.
                            if not _stamp_output_objects(
                                    file_objects, event['assetId'], event['databaseId'],
                                    upload_id, source_bucket):
                                raise VAMSGeneralErrorResponse(
                                    "Stamping the staged output objects failed")

                            # Process the external upload
                            result = process_external_upload(
                                upload_id,
                                event['assetId'],
                                event['databaseId'],
                                "assetFile",
                                files,
                                filesPathKey,
                                requestContext,
                                workflow_id=event.get('workflowId'),
                                execution_id=event.get('workflowExecutionId'),
                                change_user_id=event.get('executingUserName'),
                                file_base_execution_path_extension=event.get('outputFileBaseExecutionPathExtension', '/'),
                                source_bucket=source_bucket,
                                mfa_enabled=write_back_mfa_enabled
                            )

                            # Ingestion answers 200 when only SOME files landed, so read the
                            # per-file outcome and record rows only for what actually landed.
                            all_ingested, ingested_keys = _ingestion_outcome(result)
                            collected_output_files.extend(
                                d for d in file_descriptors
                                if result and (ingested_keys is None
                                               or d["relativeFilePath"] in ingested_keys))
                            if all_ingested and result:
                                logger.info("Asset file upload completed successfully")
                            else:
                                logger.error("Asset file upload failed")
                                output_failures.append("The asset file write-back failed.")

                        except Exception as e:
                            logger.exception(f"Error processing asset file upload: {e}")
                            output_failures.append("The asset file write-back failed.")
                    else:
                        logger.warning("No files found in asset output folder")

            #Handle metadata outputs (needs to happen after S3 file processing)
            if('metadataPathKey' in event):
                metadataPathKey = event['metadataPathKey']
                logger.info(f"Processing metadata outputs from: {metadataPathKey}")

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(source_bucket, metadataPathKey)
                    logger.info(f"Found {len(objectsFound.get('Contents', []))} objects in metadata path")
                except Exception as e:
                    logger.exception(f"Error listing metadata objects: {e}")
                    output_failures.append("Listing the pipeline metadata output failed.")

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
                    
                    # Process asset-level metadata (asset.metadata.json). Asset-level rows are
                    # recorded against the '/' target path.
                    if asset_metadata_file:
                        try:
                            applied = process_metadata_file(
                                source_bucket,
                                asset_metadata_file['Key'],
                                metadataPathKey,
                                event['databaseId'],
                                event['assetId'],
                                None,  # No file path for asset-level metadata
                                'metadata',
                                requestContext
                            )
                            if applied is None:
                                output_failures.append(METADATA_WRITE_BACK_FAILURE)
                            else:
                                _record_applied_metadata(
                                    collected_output_metadata, "/", asset_metadata_file['Key'], applied)
                        except Exception as e:
                            logger.exception(f"Error processing asset metadata: {e}")
                            output_failures.append(METADATA_WRITE_BACK_FAILURE)

                    # File-level metadata/attributes target the file's placement in the asset, which
                    # includes the output base-execution path extension the file write applied.
                    output_extension = event.get('outputFileBaseExecutionPathExtension', '/')

                    # Process each file-level metadata
                    for file_obj in file_metadata_files:
                        try:
                            # Extract the file path from the metadata filename
                            file_path = extract_file_path_from_metadata_filename(
                                file_obj['Key'],
                                metadataPathKey,
                                file_base_execution_path_extension=output_extension
                            )

                            if file_path:
                                logger.info(f"Processing metadata for file: {file_path}")
                                applied = process_metadata_file(
                                    source_bucket,
                                    file_obj['Key'],
                                    metadataPathKey,
                                    event['databaseId'],
                                    event['assetId'],
                                    file_path,
                                    'metadata',
                                    requestContext
                                )
                                if applied is None:
                                    output_failures.append(METADATA_WRITE_BACK_FAILURE)
                                else:
                                    _record_applied_metadata(
                                        collected_output_metadata, "/" + file_path.lstrip("/"),
                                        file_obj['Key'], applied)
                            else:
                                logger.error(f"Could not extract file path from: {file_obj['Key']}")
                                output_failures.append(METADATA_WRITE_BACK_FAILURE)
                        except Exception as e:
                            logger.exception(f"Error processing file metadata {file_obj['Key']}: {e}")
                            output_failures.append(METADATA_WRITE_BACK_FAILURE)

                    # Process each file-level attribute
                    for file_obj in attribute_files:
                        try:
                            # Extract the file path from the attribute filename
                            file_path = extract_file_path_from_metadata_filename(
                                file_obj['Key'],
                                metadataPathKey,
                                file_base_execution_path_extension=output_extension
                            )

                            if file_path:
                                logger.info(f"Processing attributes for file: {file_path}")
                                applied = process_metadata_file(
                                    source_bucket,
                                    file_obj['Key'],
                                    metadataPathKey,
                                    event['databaseId'],
                                    event['assetId'],
                                    file_path,
                                    'attribute',
                                    requestContext
                                )
                                if applied is None:
                                    output_failures.append(METADATA_WRITE_BACK_FAILURE)
                                else:
                                    _record_applied_metadata(
                                        collected_output_metadata, "/" + file_path.lstrip("/"),
                                        file_obj['Key'], applied)
                            else:
                                logger.error(f"Could not extract file path from: {file_obj['Key']}")
                                output_failures.append(METADATA_WRITE_BACK_FAILURE)
                        except Exception as e:
                            logger.exception(f"Error processing file attribute {file_obj['Key']}: {e}")
                            output_failures.append(METADATA_WRITE_BACK_FAILURE)

            # Handle structured result outputs (read content into the results table)
            if('resultsPathKey' in event):
                resultsPathKey = event['resultsPathKey']
                logger.info(f"Processing result outputs from: {resultsPathKey}")

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(source_bucket, resultsPathKey)
                    logger.info(f"Found {len(objectsFound.get('Contents', []))} objects in results path")
                except Exception as e:
                    logger.exception(f"Error listing result objects: {e}")
                    output_failures.append("Listing the pipeline results output failed.")

                if 'Contents' in objectsFound:
                    result_files = [obj['Key'] for obj in objectsFound['Contents'] if obj['Key'][-1] != '/']
                    for result_key in result_files:
                        try:
                            results_content = s3c.get_object(
                                Bucket=source_bucket, Key=result_key)['Body'].read().decode("utf-8")
                            # Path relative to the results folder, asset-relative with a leading slash.
                            relative_file_path = "/" + result_key[len(resultsPathKey):].lstrip("/")
                            collected_output_results.append({
                                "relativeFilePath": relative_file_path,
                                "resultsContent": results_content,
                                "s3Key": result_key,
                            })
                        except Exception as e:
                            logger.exception(f"Error reading result file {result_key}: {e}")
                            output_failures.append("Reading a pipeline results file failed.")


            # Record end-state pipeline outputs + completion status.
            try:
                # The workflow SFN log group is the same for every workflow; read it from
                # env (not the ASL event) so this works even for workflows not redeployed
                # with a newer ASL. Scope the log read to THIS execution within the group.
                log_group_arn = workflow_execution_log_group_arn
                workflow_execution_id = event.get('workflowExecutionId', '')
                end_state_pipeline_execution_id = event.get('endStatePipelineExecutionId', '')
                # Full execution log for this run, captured on completion regardless of
                # success/failure (stored on the main row's executionLog field).
                execution_log, stream_name = _fetch_execution_logs(
                    log_group_arn, workflow_execution_id
                )
                # Attribute to the end-state pipeline only the output files new or version-changed
                # vs the prior pipelines' baseline; preview rows have no baseline, kept as-is.
                prior_ids = [pid for pid in (event.get('priorPipelineExecutionIds') or [])
                             if pid and pid != end_state_pipeline_execution_id]
                baseline = eo.recorded_output_versions(
                    dynamodb, pipeline_execution_output_files_table, prior_ids)
                file_descriptors = [f for f in collected_output_files if f.get("fileType") == "file"]
                preview_descriptors = [f for f in collected_output_files if f.get("fileType") != "file"]
                attributed_files = [
                    f for f in file_descriptors
                    if baseline.get(f.get("s3Key", "")) is None
                    or baseline.get(f.get("s3Key", "")) != f.get("s3VersionId", "")
                ]
                status, error_summary = _terminal_status(output_failures)
                record_execution_outputs(
                    dynamo=dynamodb,
                    workflow_execution_id=workflow_execution_id,
                    end_state_pipeline_execution_id=end_state_pipeline_execution_id,
                    workflow_database_id=event.get('workflowDatabaseId', ''),
                    workflow_id=event.get('workflowId', ''),
                    bucket_name=bucket_name,
                    output_files=attributed_files + preview_descriptors,
                    output_metadata=collected_output_metadata,
                    output_results=collected_output_results,
                    result_log="Workflow Execution Output Processing Complete",
                    execution_log=execution_log,
                    log_group_arn=log_group_arn, log_stream_name=stream_name,
                    execution_status=status, execution_error=error_summary,
                )
            except Exception as e:
                # Output recording is non-critical to the upload pipeline; log and continue.
                logger.exception(f"Error recording execution outputs (non-critical): {e}")

            return success(body={"message": "Workflow Execution Output Processing Complete"})

        else:
            # Step Functions invokes this state as a plain lambda task and does not inspect the
            # returned payload, so a failure must surface as a raised error for the state's Catch
            # to route to the error-handler state (which marks the execution rows terminal).
            raise VAMSGeneralErrorResponse(
                "Write-back to the output asset is not authorized for the executing user")
    except Exception as e:
        logger.exception(e)
        # Propagate so the ASL Catch on this state fires; returning an error payload would let
        # the state machine report the run SUCCEEDED with no outputs recorded.
        raise