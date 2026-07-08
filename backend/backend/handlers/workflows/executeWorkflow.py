#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
from boto3.dynamodb.conditions import Key
import json
import uuid
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate
from common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from urllib.parse import unquote_plus
from common.workflows import executionRecords as er
from models.common import (
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse
)
from models.workflows import ExecuteWorkflowRequestModel

logger = safeLogger(service="ExecuteWorkflow")

# Claims/roles for the current request (set per-invocation in lambda_handler).
claims_and_roles = {}


def _clean_validation_message(v):
    """Extract the human-readable message a request model's @root_validator raised,
    so client-facing validation errors stay identical to the prior handler text
    (the validate() dispatcher message), rather than Pydantic's verbose wrapper."""
    try:
        errors = v.errors()
        if errors and errors[0].get('msg'):
            return errors[0]['msg']
    except Exception:
        pass
    return str(v)

try:
    client = boto3.client('lambda')
    s3c = boto3.client('s3')
    sfn_client = boto3.client('stepfunctions')
    dynamodb = boto3.resource('dynamodb')
except Exception as e:
    logger.exception("Failed Loading Error Functions")

try:
    s3_asset_buckets_table = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    asset_Database = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    pipeline_Database = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE)
    workflow_database = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE)
    bucket_name_assetAuxiliary = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    metadata_service_function = os.environ['METADATA_SERVICE_LAMBDA_FUNCTION_NAME']
    workflow_execution_database_v2 = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
    pipeline_execution_input_files_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE)
    pipeline_execution_input_metadata_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE)
    pipeline_execution_input_configuration_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE)
    workflow_execution_inputs_table = get_table_name(ResourceKeys.WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE)
    workflow_execution_configuration_table = get_table_name(ResourceKeys.WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE)
    # Real shared workflow SFN log group ARN (same group for every workflow). Recorded
    # on the execution row so per-execution logs can later be pulled (group ARN + the
    # row's workflow_execution_arn). Optional: empty string if logging is not configured.
    workflow_execution_log_group_arn = os.environ.get("WORKFLOW_EXECUTION_LOG_GROUP_ARN", "")
    # Orchestration bus ARN + event source prefix, written into each pipeline's
    # manifest.systemConfig for optional sub-process registration. Optional: empty if unset.
    orchestration_bus_arn = os.environ.get("ORCHESTRATION_BUS_ARN", "")
    orchestration_event_source_prefix = os.environ.get("ORCHESTRATION_EVENT_SOURCE_PREFIX", "")
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

# Upper bound on candidate input rows inspected by the concurrency guard so a
# launch never fans out into an unbounded number of describe_execution calls.
MAX_CONCURRENCY_CANDIDATES_INSPECTED = 200

buckets_table = dynamodb.Table(s3_asset_buckets_table)

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
            raise Exception(f"Error getting database default bucket details.")

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

def get_pipelines(databaseId, pipelineId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('pipelineId').eq(pipelineId),
        ScanIndexForward=False,
    )
    return response['Items']


def _metadata_service_lambda(payload):
    """Invoke metadata service lambda"""
    return client.invoke(
        FunctionName=metadata_service_function,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload).encode('utf-8')
    )


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


def get_asset_metadata(databaseId, assetId, event):
    """Get asset metadata using new metadata service"""
    try:
        # Build Lambda event for metadata service GET endpoint
        l_payload = {
            'requestContext': {
                'http': {
                    'path': f'/database/{databaseId}/assets/{assetId}/metadata',
                    'method': 'GET'
                },
                'authorizer': event['requestContext']['authorizer']
            },
            'pathParameters': {
                'databaseId': databaseId,
                'assetId': assetId
            },
            'queryStringParameters': {}
        }

        logger.info("Fetching asset metadata from metadata service")
        logger.info(l_payload)

        metadata_response = _metadata_service_lambda(l_payload)
        logger.info("Asset metadata response received")

        stream = metadata_response.get('Payload', "")
        response_body = {}
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info(f"Asset metadata payload status: {json_response.get('statusCode')}")
            if "body" in json_response and json_response.get('statusCode') == 200:
                response_body = json.loads(json_response['body'])

        return response_body
    except Exception as e:
        logger.exception(f"Failed fetching asset metadata: {e}")
        return {}


def get_file_metadata(databaseId, assetId, filePath, event):
    """Get file metadata using new metadata service"""
    try:
        # Build Lambda event for metadata service GET endpoint
        l_payload = {
            'requestContext': {
                'http': {
                    'path': f'/database/{databaseId}/assets/{assetId}/metadata/file',
                    'method': 'GET'
                },
                'authorizer': event['requestContext']['authorizer']
            },
            'pathParameters': {
                'databaseId': databaseId,
                'assetId': assetId
            },
            'queryStringParameters': {
                'filePath': filePath,
                'type': 'metadata'
            }
        }

        logger.info(f"Fetching file metadata from metadata service for file: {filePath}")
        logger.info(l_payload)

        metadata_response = _metadata_service_lambda(l_payload)
        logger.info("File metadata response received")

        stream = metadata_response.get('Payload', "")
        response_body = {}
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info(f"File metadata payload status: {json_response.get('statusCode')}")
            if "body" in json_response and json_response.get('statusCode') == 200:
                response_body = json.loads(json_response['body'])

        return response_body
    except Exception as e:
        logger.exception(f"Failed fetching file metadata: {e}")
        return {}


def get_file_attributes(databaseId, assetId, filePath, event):
    """Get file attributes using new metadata service"""
    try:
        # Build Lambda event for metadata service GET endpoint
        l_payload = {
            'requestContext': {
                'http': {
                    'path': f'/database/{databaseId}/assets/{assetId}/metadata/file',
                    'method': 'GET'
                },
                'authorizer': event['requestContext']['authorizer']
            },
            'pathParameters': {
                'databaseId': databaseId,
                'assetId': assetId
            },
            'queryStringParameters': {
                'filePath': filePath,
                'type': 'attribute'
            }
        }

        logger.info(f"Fetching file attributes from metadata service for file: {filePath}")
        logger.info(l_payload)

        metadata_response = _metadata_service_lambda(l_payload)
        logger.info("File attributes response received")

        stream = metadata_response.get('Payload', "")
        response_body = {}
        if stream:
            json_response = json.loads(stream.read().decode("utf-8"))
            logger.info(f"File attributes payload status: {json_response.get('statusCode')}")
            if "body" in json_response and json_response.get('statusCode') == 200:
                response_body = json.loads(json_response['body'])

        return response_body
    except Exception as e:
        logger.exception(f"Failed fetching file attributes: {e}")
        return {}


def simplify_metadata_array(metadata_array):
    """
    Convert verbose metadata array to simple key-value dictionary.
    Removes all schema fields and nested structure to reduce size for pipeline input.

    Args:
        metadata_array: List of metadata objects with full schema info

    Returns:
        Dictionary with metadataKey as key and metadataValue as value
    """
    simplified = {}
    for item in metadata_array:
        key = item.get('metadataKey', '')
        value = item.get('metadataValue', '')
        if key:  # Only add if key exists
            simplified[key] = value
    return simplified


def get_separate_metadata(databaseId, assetId, filePath, event):
    """Get asset metadata, file metadata, and file attributes separately using new metadata service"""
    try:
        # Always get asset metadata
        asset_metadata_response = get_asset_metadata(databaseId, assetId, event)

        # Extract metadata list from response (new format)
        asset_metadata = asset_metadata_response.get("metadata", [])

        # If a file path is provided, also get file metadata and attributes
        file_metadata = []
        file_attributes = []
        if filePath:
            file_metadata_response = get_file_metadata(databaseId, assetId, filePath, event)
            file_metadata = file_metadata_response.get("metadata", [])

            file_attributes_response = get_file_attributes(databaseId, assetId, filePath, event)
            file_attributes = file_attributes_response.get("metadata", [])

        logger.info(f"Retrieved metadata - Asset: {len(asset_metadata)} items, File: {len(file_metadata)} items, Attributes: {len(file_attributes)} items")

        return {
            "assetMetadata": {"metadata": asset_metadata},
            "fileMetadata": {"metadata": file_metadata},
            "fileAttributes": {"metadata": file_attributes}
        }
    except Exception as e:
        logger.exception(f"Failed fetching separate metadata: {e}")
        return {
            "assetMetadata": {"metadata": []},
            "fileMetadata": {"metadata": []},
            "fileAttributes": {"metadata": []}
        }


def _parse_pipeline_resource(pipeline):
    """Extract the resourceId (ARN/URL/bus) and resourceType from a pipeline's
    userProvidedResource JSON string. Mirrors stepfunctions_builder parsing."""
    raw = pipeline.get('userProvidedResource', '{}') or '{}'
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    return parsed.get('resourceId', ''), parsed.get('resourceType', 'Lambda')


def _pipeline_input_configuration(pipeline):
    """The pipeline definition's inputParameters JSON string, or empty string when absent or
    not valid JSON."""
    raw = pipeline.get('inputParameters', '') or ''
    if raw and raw != '':
        try:
            json.loads(raw)
            return raw
        except (json.JSONDecodeError, TypeError):
            logger.warning("Pipeline inputParameters is not valid JSON; storing empty input configuration")
            return ''
    return ''


def write_execution_input_files(execution_id, asset_bucket, pipelines, input_metadata,
                                original_input_manifest):
    """Write an execution's input-definition files to the asset bucket (per-execution input
    folder keyed on execution id): the shared metadata file, one config.json per pipeline, and
    pipeline 1's manifest. Returns
    { metadataFileS3Key, configKeys: [perPipeline...], firstManifestS3Key } as asset-bucket keys."""
    locations = {"metadataFileS3Key": "", "configKeys": [], "firstManifestS3Key": ""}
    if not pipelines:
        return locations

    # Shared input metadata file, wrapped in the metadata envelope (parse a string payload
    # back to JSON first so it stays a structured object).
    metadata_key = er.execution_input_metadata_key(execution_id)
    if isinstance(input_metadata, str):
        try:
            metadata_payload = json.loads(input_metadata) if input_metadata else {}
        except (json.JSONDecodeError, ValueError):
            metadata_payload = input_metadata
    else:
        metadata_payload = input_metadata or {}
    metadata_body = json.dumps(er.build_metadata_envelope(metadata_payload))
    s3c.put_object(Bucket=asset_bucket, Key=metadata_key,
                   Body=metadata_body.encode('utf-8'), ContentType='application/json')
    locations["metadataFileS3Key"] = metadata_key

    # Per-pipeline input configuration files (1-indexed folders pipeline1..N).
    for idx, pipeline in enumerate(pipelines):
        cfg_key = er.pipeline_input_config_key(execution_id, idx + 1)
        cfg_body = _pipeline_input_configuration(pipeline)
        s3c.put_object(Bucket=asset_bucket, Key=cfg_key,
                       Body=(cfg_body or "").encode('utf-8'), ContentType='application/json')
        locations["configKeys"].append(cfg_key)

    # Pipeline 1's resolved input manifest = the original asset input files.
    manifest_key = er.pipeline_input_manifest_key(execution_id, 1)
    s3c.put_object(Bucket=asset_bucket, Key=manifest_key,
                   Body=json.dumps(original_input_manifest).encode('utf-8'),
                   ContentType='application/json')
    locations["firstManifestS3Key"] = manifest_key
    return locations


def persist_execution_records(dynamo, execution_id, workflow_arn, workflow_execution_arn,
                              database_id, asset_id, workflow_database_id, workflow_id,
                              input_asset_file_key, asset_bucket, aux_bucket,
                              triggered_by_user_id, trigger_type, execution_log_group_arn,
                              pipelines, first_job_name, input_metadata, input_configuration,
                              pipeline_execution_ids=None, input_config_keys=None,
                              output_asset_id="", output_database_id="",
                              output_file_base_execution_path_extension="/",
                              input_asset_root_s3_key="",
                              input_metadata_asset_id="", input_metadata_database_id="",
                              input_metadata_file_s3_key=""):
    """Write the V2 main execution row plus workflow-level inputs/config and one
    PipelineExecutions row per pipeline, plus each pipeline's own input configuration row.

    Input asset files are tracked at the workflow-execution level (WorkflowExecutionInputs),
    not per-pipeline.

    Returns a dict with executionId, endStatePipelineExecutionId, and the list of
    generated pipelineExecutionIds (ordered to match `pipelines`).
    """
    start_date = er.iso_now()
    first_pipeline_name = pipelines[0]['name'] if pipelines else ""
    output_prefixes = er.pipeline_output_prefixes(first_pipeline_name, first_job_name, execution_id) \
        if pipelines else {"files": "", "previews": "", "metadata": "", "results": ""}

    # 1) Main V2 row
    main_table = dynamo.Table(workflow_execution_database_v2)
    main_table.put_item(Item=er.build_workflow_execution_record(
        execution_id=execution_id,
        workflow_database_id=workflow_database_id, workflow_id=workflow_id,
        workflow_arn=workflow_arn, workflow_execution_arn=workflow_execution_arn,
        execution_start_date=start_date, execution_status="NEW",
        triggered_by_user_id=triggered_by_user_id, trigger_type=trigger_type,
        execution_log_group_arn=execution_log_group_arn,
    ))

    # 2) Workflow-level input row (asset-scoped GET source of truth). Input asset files are
    #    tracked here at the workflow-execution level (not per-pipeline).
    wf_inputs_table = dynamo.Table(workflow_execution_inputs_table)
    wf_inputs_table.put_item(Item=er.build_workflow_execution_input_record(
        workflow_execution_id=execution_id, database_id=database_id, asset_id=asset_id,
        input_asset_file_key=input_asset_file_key, execution_start_date=start_date,
        workflow_id=workflow_id, workflow_database_id=workflow_database_id,
        s3_bucket=asset_bucket, asset_root_s3_key=input_asset_root_s3_key,
    ))

    # 3) Workflow execution configuration row: pipeline snapshot + input metadata + output
    #    target and input-metadata provenance.
    wf_cfg_table = dynamo.Table(workflow_execution_configuration_table)
    wf_cfg_table.put_item(Item=er.build_workflow_configuration_record(
        workflow_execution_id=execution_id,
        workflow_configuration="",
        input_metadata=json.dumps(input_metadata) if not isinstance(input_metadata, str) else input_metadata,
        specified_pipelines_snapshot=pipelines,
        output_location_type="asset",
        output_asset_id=output_asset_id or asset_id,
        output_database_id=output_database_id or database_id,
        output_file_base_execution_path_extension=output_file_base_execution_path_extension or "/",
        input_metadata_asset_id=input_metadata_asset_id or asset_id,
        input_metadata_database_id=input_metadata_database_id or database_id,
        input_metadata_file_s3_key=input_metadata_file_s3_key,
    ))

    # 4) One PipelineExecutions row per pipeline (chain + end-state on last), plus each
    #    pipeline's own input configuration row.
    pexec_table = dynamo.Table(pipeline_executions_table)
    pin_cfg_table = dynamo.Table(pipeline_execution_input_configuration_table)
    if pipeline_execution_ids is None:
        pipeline_execution_ids = [er.new_guid() for _ in pipelines]
    input_config_keys = input_config_keys or []
    # Per-file aux preview prefix, keyed on the FULL asset file key (asset location key + relative
    # path) so any custom asset base prefix is preserved: {databaseId}/{assetFileKey}/preview.
    aux_preview_prefix = er.aux_preview_file_prefix(database_id, input_asset_file_key)

    prev_id = ""
    for idx, pipeline in enumerate(pipelines):
        pexec_id = pipeline_execution_ids[idx]
        is_end_state = (idx == len(pipelines) - 1)
        resource_arn, _rtype = _parse_pipeline_resource(pipeline)
        # Bucket-relative, execution-scoped temp working prefix (matches the manifest's aux temp
        # prefix layout: pipelines/{pipelineName}/{executionId}/).
        aux_temp_prefix = er.aux_pipeline_prefix(pipeline['name'], execution_id)
        cfg_key = input_config_keys[idx] if idx < len(input_config_keys) else ""
        # Orchestration event prefix (empty when the bus is not configured).
        event_prefix = er.orchestration_event_prefix(
            orchestration_event_source_prefix, execution_id, pexec_id) \
            if orchestration_event_source_prefix else ""
        pexec_table.put_item(Item=er.build_pipeline_execution_record(
            pipeline_execution_id=pexec_id, workflow_execution_id=execution_id,
            pipeline_database_id=pipeline.get('databaseId', ''), pipeline_id=pipeline['name'],
            end_state_pipeline=is_end_state,
            s3_asset_bucket=asset_bucket, s3_aux_bucket=aux_bucket,
            output_prefixes=output_prefixes,
            input_metadata_file_prefix="",
            input_config_file_prefix=cfg_key,
            aux_temp_prefix=aux_temp_prefix, aux_preview_prefix=aux_preview_prefix,
            pipeline_execution_type=pipeline.get('pipelineExecutionType', 'Lambda'),
            wait_for_callback=pipeline.get('waitForCallback', 'Disabled'),
            pipeline_resource_arn=resource_arn, from_pipeline_execution_id=prev_id,
            orchestration_bus_event_prefix=event_prefix,
        ))
        # Per-pipeline input configuration row.
        pin_cfg_table.put_item(Item=er.build_input_configuration_record(
            pipeline_execution_id=pexec_id,
            input_configuration=_pipeline_input_configuration(pipeline),
            input_configuration_file_s3_key=cfg_key,
        ))
        prev_id = pexec_id

    # 5) Workflow-level input metadata row (asset-level '/'), recorded once for the execution.
    if input_metadata:
        md = input_metadata if isinstance(input_metadata, dict) else {}
        first_pexec_id = pipeline_execution_ids[0] if pipeline_execution_ids else None
        if first_pexec_id:
            pin_md_table = dynamo.Table(pipeline_execution_input_metadata_table)
            pin_md_table.put_item(Item=er.build_input_metadata_record(
                pipeline_execution_id=first_pexec_id, database_id=database_id, asset_id=asset_id,
                file_path="/", metadata=md,
                source_input_metadata_file_s3_key=input_metadata_file_s3_key,
            ))

    return {
        "workflowExecutionId": execution_id,
        "endStatePipelineExecutionId": pipeline_execution_ids[-1] if pipeline_execution_ids else "",
        "pipelineExecutionIds": pipeline_execution_ids,
    }


def launchWorkflow(inputAssetBucket, inputAssetLocationKey, inputAssetFileKey, workflow_arn, database_id, asset_id, workflow_database_id, workflow_id, executingUserName, executingRequestContext, pipelines, inputMetadata = {}, triggerType = "Manual", storedJobNames = None, outputFileBaseExecutionPathExtension = "/"):

    logger.info("Launching workflow with arn: "+workflow_arn)

    # Path segment inserted between the output asset location key and each output file's relative
    # path. Defaults to '/' (no extra path); the execute call may override it for this run.
    output_file_base_execution_path_extension = outputFileBaseExecutionPathExtension or "/"

    #Modify asset key to turn + sympbols into spaces for the final processing entry
    inputAssetFileKey = unquote_plus(inputAssetFileKey)

    # Generate a VAMS-owned execution GUID and use it as the Step Functions
    # execution name so $$.Execution.Name == executionId (all ASL S3 paths and
    # process-output keep working unchanged).
    executionId = uuid.uuid4().hex

    # Pre-generate the end-state pipeline-execution GUID so it can be threaded
    # into the SFN input for the process-output step.
    pipeline_execution_ids = [uuid.uuid4().hex for _ in pipelines]
    end_state_pipeline_execution_id = pipeline_execution_ids[-1] if pipeline_execution_ids else ""

    # First pipeline job name: prefer the ASL-stored jobNames[0] so the manifest's output
    # locations match the ASL's paths; fall back to the ASL convention for legacy workflows.
    first_pipeline_name = pipelines[0]['name'] if pipelines else ""
    if storedJobNames:
        first_job_name = storedJobNames[0]
    else:
        first_job_name = (uuid.uuid1().hex[:5] + "-" + first_pipeline_name)[:80] if first_pipeline_name else ""

    # Build pipeline 1's input file manifest entry. Locations are carried as relative keys plus
    # the file's own bucket (never a pre-built s3:// URI): assetRootS3Key is the asset-root prefix
    # within the bucket (relativePath is the key after that root), and each input file gets its own
    # unique aux preview prefix ({databaseId}/{assetId}/{relativeFileKey}/preview).
    base_key = inputAssetLocationKey or ""
    relative_input = inputAssetFileKey[len(base_key):] if inputAssetFileKey.startswith(base_key) else inputAssetFileKey
    # Aux preview prefix keyed on the FULL asset file key (location key + relative path) so a
    # custom asset base prefix is preserved: {databaseId}/{assetFileKey}/preview.
    first_aux_preview_prefix = er.aux_preview_file_prefix(database_id, inputAssetFileKey)
    first_input_files = [er.build_manifest_entry(
        relative_path=relative_input, bucket=inputAssetBucket, key=inputAssetFileKey,
        version_id="", database_id=database_id, asset_id=asset_id,
        asset_root_s3_key=base_key, aux_preview_prefix=first_aux_preview_prefix)]

    # Build pipeline 1's full manifest envelope (output/aux locations + system config), using
    # the ASL-stored job name so output locations match the ASL's paths. outputs carries the asset
    # bucket + bucket-relative prefixes; the aux temp prefix is bucket-relative and scoped to this
    # execution (pipelines/{pipelineName}/{executionId}/).
    out_prefixes = er.pipeline_output_prefixes(first_pipeline_name, first_job_name, executionId) \
        if pipelines else {"files": "", "previews": "", "metadata": "", "results": ""}
    outputs = er.build_manifest_outputs(bucket=inputAssetBucket, **out_prefixes)
    first_aux_temp_prefix = er.aux_pipeline_prefix(first_pipeline_name, executionId) if pipelines else ""
    metadata_metadata_location = f"s3://{inputAssetBucket}/{er.execution_input_metadata_key(executionId)}"
    first_event_prefix = er.orchestration_event_prefix(
        orchestration_event_source_prefix, executionId, pipeline_execution_ids[0]) \
        if (orchestration_event_source_prefix and pipeline_execution_ids) else ""
    original_input_manifest = er.build_manifest_envelope(
        input_files=first_input_files,
        input_metadata_s3_location=metadata_metadata_location,
        outputs=outputs,
        aux_bucket=bucket_name_assetAuxiliary,
        aux_temp_prefix=first_aux_temp_prefix,
        # Per-pipeline viewer subfolder appended to each input file's aux preview prefix; empty
        # until sourced from the pipeline configuration.
        aux_preview_pipeline_prefix="",
        system_config=er.build_manifest_system_config(
            orchestration_bus_arn=orchestration_bus_arn,
            orchestration_event_prefix=first_event_prefix),
        # Output target == the input asset today (output location type 'asset').
        output_target=er.build_manifest_output_target(
            location_type="asset", asset_id=asset_id, database_id=database_id,
            file_base_execution_path_extension=output_file_base_execution_path_extension),
    )

    # Write the input-definition files to the asset bucket execution input folder.
    input_locations = write_execution_input_files(
        execution_id=executionId, asset_bucket=inputAssetBucket,
        pipelines=pipelines, input_metadata=inputMetadata,
        original_input_manifest=original_input_manifest)

    # The SFN execution input carries only what the ASL ($.X) references: identity, the
    # workflow-execution + auxiliary buckets, the output target, the per-pipeline execution ids,
    # and the executing-user context. Per-input-file locations live in the manifest (addressed by
    # per-pipeline computed S3 keys), so no single triggering file key is threaded here — the SFN
    # layer is input-file-agnostic and multi-file-ready. Orchestration bus config is not threaded
    # through the SFN input either: it belongs in the manifest (for pipelines) and in the interim
    # lambda's environment (for the next-pipeline manifest build), each per its intended purpose.
    response = sfn_client.start_execution(
        stateMachineArn=workflow_arn,
        name=executionId,
        input=json.dumps({
            # Workflow-execution identity
            'workflowExecutionId': executionId,
            'workflowDatabaseId': workflow_database_id,
            'workflowId': workflow_id,
            'endStatePipelineExecutionId': end_state_pipeline_execution_id,
            'pipelineExecutionIds': pipeline_execution_ids,
            # Workflow-execution I/O bucket: where the ASL pulls the manifest/config files from and
            # where the shared output folder lives. The auxiliary bucket is NOT threaded here — it
            # is resolved by the interim lambda and carried in each manifest (manifest.auxBucket).
            'workflowExecutionS3InputOutputBucket': inputAssetBucket,
            # Output target identity (where outputs are written; == the input asset today). Read
            # only by the end-state process-output lambda, which has no manifest of its own.
            'outputLocationType': "asset",
            'outputAssetId': asset_id,
            'outputDatabaseId': database_id,
            'outputFileBaseExecutionPathExtension': output_file_base_execution_path_extension,
            # Executing-user context
            'executingUserName': executingUserName,
            'executingRequestContext': executingRequestContext,
        })
    )
    logger.info("Workflow Response: ")
    logger.info(response)

    # Shared workflow SFN log group ARN recorded on the execution row (best-effort).
    execution_log_group_arn = workflow_execution_log_group_arn

    # Persist the V2 main row + workflow-level inputs/config + per-pipeline rows.
    # Pass the pre-generated pipeline-execution ids so the end-state id matches the
    # value threaded into the SFN input above.
    persist_execution_records(
        dynamo=dynamodb,
        pipeline_execution_ids=pipeline_execution_ids,
        execution_id=executionId,
        workflow_arn=workflow_arn,
        workflow_execution_arn=response['executionArn'],
        database_id=database_id, asset_id=asset_id,
        workflow_database_id=workflow_database_id, workflow_id=workflow_id,
        input_asset_file_key=inputAssetFileKey,
        asset_bucket=inputAssetBucket, aux_bucket=bucket_name_assetAuxiliary,
        triggered_by_user_id=executingUserName, trigger_type=triggerType,
        execution_log_group_arn=execution_log_group_arn,
        pipelines=pipelines, first_job_name=first_job_name,
        input_metadata=inputMetadata, input_configuration="",
        input_config_keys=input_locations['configKeys'],
        output_asset_id=asset_id, output_database_id=database_id,
        output_file_base_execution_path_extension=output_file_base_execution_path_extension,
        input_asset_root_s3_key=base_key,
        input_metadata_asset_id=asset_id, input_metadata_database_id=database_id,
        input_metadata_file_s3_key=input_locations['metadataFileS3Key'],
    )

    return executionId


def get_asset(databaseId, assetId):
    table = dynamodb.Table(asset_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId)
    )
    return response['Items']


def get_workflow(workflowDatabaseId, workflowId):
    table = dynamodb.Table(workflow_database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(workflowDatabaseId) & Key('workflowId').eq(workflowId)
    )
    return response['Items']


def validate_pipelines(workflow, claims_and_roles):
    for pipeline in workflow['specifiedPipelines']['functions']:
        pipeline_state = get_pipelines(workflow['databaseId'], pipeline["name"])[0]
        if not pipeline_state['enabled']:
            logger.warning(f"Pipeline {pipeline['name']} is disabled")
            return (False, pipeline["name"])

        allowed = False
        if pipeline_state:
            # Add Casbin Enforcer to check if the current user has permissions to POST the pipeline (Tier 2):
            pipeline_state.update({
                "object__type": "pipeline"
            })

            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(pipeline_state, "POST"):
                    allowed = True

        if not allowed:
            return (False, pipeline["name"])

    return (True, '')

def get_workflow_executions(databaseId, assetId, workflowDatabaseId, workflowId, file_key=None):
    """Detect a currently-RUNNING execution on this asset (optionally filtered by
    workflow + file key) before launching, to prevent duplicate runs.

    Sourced from the V2 inputs GSI + V2 main table. The scan of candidate input
    rows is bounded (newest-first) and, when a file key is given, restricted to
    rows for that exact file before any main-row fetch / describe_execution. The
    first confirmed-running match short-circuits the return."""
    logger.info("Getting current executions (V2)")
    inputs_table = dynamodb.Table(workflow_execution_inputs_table)
    main_table = dynamodb.Table(workflow_execution_database_v2)

    # Compare the file key symmetrically with how the writer stores it.
    normalized_file_key = er.normalize_file_key(unquote_plus(file_key)) if file_key else None

    partitionKey = f"{databaseId}:{assetId}"
    query_kwargs = {
        'IndexName': 'WorkflowExecInputsByAssetGSI',
        'KeyConditionExpression': Key('databaseId:assetId').eq(partitionKey),
        'ScanIndexForward': False,
    }

    # Collect candidate input rows newest-first, applying the file-key filter
    # before accumulating, and stop once the bound is reached (no silent
    # truncation -- warn instead).
    candidate_items = []
    bounded = False
    resp = inputs_table.query(**query_kwargs)
    while True:
        for input_item in resp.get('Items', []):
            if normalized_file_key and input_item.get('inputAssetFileKey', '') != normalized_file_key:
                continue
            candidate_items.append(input_item)
            if len(candidate_items) >= MAX_CONCURRENCY_CANDIDATES_INSPECTED:
                bounded = True
                break
        if bounded or 'LastEvaluatedKey' not in resp:
            break
        query_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = inputs_table.query(**query_kwargs)

    if bounded:
        logger.warning(
            f"Concurrency check bounded at {MAX_CONCURRENCY_CANDIDATES_INSPECTED} candidate input rows; "
            "older executions were not inspected.")

    result = {"Items": []}

    # Dedup execution ids (newest-first); fetch main row + confirm running only
    # for executions on this specific file.
    seen_execution_ids = set()
    for input_item in candidate_items:
        execution_id = input_item.get('workflowExecutionId', '')
        if not execution_id or execution_id in seen_execution_ids:
            continue
        seen_execution_ids.add(execution_id)

        # Fetch the V2 main row to get the workflow composite + arn
        main_resp = main_table.query(
            KeyConditionExpression=Key('workflowExecutionId').eq(execution_id),
            ScanIndexForward=False,
        )
        main_rows = main_resp.get('Items', [])
        if not main_rows:
            continue
        main_item = main_rows[0]

        # Optional workflow filter
        if workflowId:
            if main_item.get('workflowDatabaseId:workflowId', '') != er.workflow_composite_key(workflowDatabaseId, workflowId):
                continue

        # Skip if already has a stop date
        if main_item.get('executionStopDate'):
            continue

        # Confirm running via Step Functions
        try:
            execution = sfn_client.describe_execution(
                executionArn=main_item.get('workflow_execution_arn', '')
            )
            if not execution.get('stopDate'):
                result["Items"].append({
                    'workflowDatabaseId': main_item.get('workflowDatabaseId', ''),
                    'workflowId': main_item.get('workflowId', ''),
                    'workflowExecutionId': execution['name'],
                    'executionStatus': execution['status'],
                    'startDate': main_item.get('executionStartDate', ''),
                })
                # One running match is enough for the caller's concurrency guard.
                logger.info(f"Found running execution on this file: {result}")
                return result
        except Exception as e:
            logger.exception(e)
            logger.info("Continuing with trying to fetch executions...")

    logger.info(f"Returning existing running execution results: {result}")
    return result


def build_pipeline_input_metadata(asset, databaseId, assetId, relative_file_path, event):
    """Assemble the simplified `inputMetadata` payload (asset data + asset/file/
    attribute metadata) passed to the workflow, via the metadata service."""
    metadata_result = get_separate_metadata(databaseId, assetId, relative_file_path, event)

    simplified_asset_metadata = simplify_metadata_array(
        metadata_result.get("assetMetadata", {}).get("metadata", [])
    )
    simplified_file_metadata = simplify_metadata_array(
        metadata_result.get("fileMetadata", {}).get("metadata", [])
    )
    simplified_file_attributes = simplify_metadata_array(
        metadata_result.get("fileAttributes", {}).get("metadata", [])
    )

    logger.info(f"Simplified metadata - Asset: {len(simplified_asset_metadata)} keys, "
                f"File: {len(simplified_file_metadata)} keys, "
                f"Attributes: {len(simplified_file_attributes)} keys")

    return {
        "VAMS": {
            "assetData": {
                "assetName": asset.get("assetName", ""),
                "description": asset.get("description", ""),
                "tags": asset.get("tags", [])
            },
            "assetMetadata": simplified_asset_metadata,
            "fileMetadata": simplified_file_metadata,
            "fileAttributes": simplified_file_attributes
        },
    }


def execute_workflow(event, databaseId, assetId, workflowId, request_model):
    """Validate, authorize, and launch a workflow execution on an asset.

    Returns the API response (success with the new execution id, or an error).
    `request_model` is the parsed ExecuteWorkflowRequestModel.
    """
    workflow_database_id = request_model.workflowDatabaseId
    request_file_key = request_model.fileKey

    # Workflow's database must be GLOBAL or match the asset's database.
    if workflow_database_id != 'GLOBAL' and workflow_database_id != databaseId:
        logger.error("Workflow database ID validation failed. Workflow can only be executed "
                     "on assets from the same database or from global workflows.")
        return validation_error(
            body={'message': 'Workflow can only be executed on assets from the same database or from global workflows'},
            event=event
        )

    # Asset must exist + Tier 2 authorization (POST on the asset); this is also the
    # output-asset write-permission gate, since the asset is today the output target.
    assetResponse = get_asset(databaseId, assetId)
    logger.info(assetResponse)
    if not bool(assetResponse):
        return validation_error(status_code=404, body={'message': 'Asset does not exist'}, event=event)

    asset = assetResponse[0]
    asset.update({"object__type": "asset"})

    executingUserName = ''
    executingRequestContext = event['requestContext']
    asset_allowed = False
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(asset, "POST"):
            asset_allowed = True
            executingUserName = claims_and_roles["tokens"][0]

    if not asset_allowed:
        return authorization_error()

    # Workflow must exist + Tier 2 authorization (POST on the workflow).
    workflowResponse = get_workflow(workflow_database_id, workflowId)
    logger.info(workflowResponse)
    if not bool(workflowResponse):
        return validation_error(status_code=404, body={'message': 'Workflow does not exist'}, event=event)

    workflow = workflowResponse[0]
    workflow.update({"object__type": "workflow"})
    workflow_allowed = False
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(workflow, "POST"):
            workflow_allowed = True

    if not workflow_allowed:
        return authorization_error()

    # All pipelines in the workflow must be enabled + Tier 2 accessible.
    (status, pipelineName) = validate_pipelines(workflow, claims_and_roles)
    if not status:
        logger.error("Not all pipelines are enabled/accessible")
        return validation_error(body={'message': 'Pipeline is not enabled/accessible'}, event=event)

    logger.info("All pipelines are enabled. Continuing to run workflow")

    # Resolve the file key (asset base prefix, or a specific requested file).
    asset_file_key = asset['assetLocation']['Key']
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    asset_bucket = bucketDetails['bucketName']

    file_key = asset_file_key
    relative_file_path = None
    if request_file_key:
        file_key = resolve_asset_file_path(file_key, request_file_key)
        relative_file_path = request_file_key
        logger.info(f"Using file key from request: {file_key}, relative path: {relative_file_path}")
    else:
        logger.info(f"Using asset's base prefix key (no particular file): {file_key}")

    # Block duplicate concurrent runs of this workflow on the same file.
    executionResults = get_workflow_executions(databaseId, assetId, workflow_database_id, workflowId, file_key)
    if len(executionResults['Items']) > 0:
        logger.error(f"Workflow has a currently running execution on the file: {file_key}")
        return validation_error(body={'message': 'Workflow has a currently running execution on this file'}, event=event)

    # Build the pipeline input metadata payload.
    inputMetadata = build_pipeline_input_metadata(asset, databaseId, assetId, relative_file_path, event)

    # Per-pipeline inputParameters override (by pipeline name) for this run only; the workflow
    # definition is left untouched. A pipeline absent from the map keeps its stored value.
    pipelines = workflow['specifiedPipelines']['functions']
    overrides = request_model.pipelineInputParameters or {}
    if overrides:
        pipelines = [
            {**pipeline, 'inputParameters': overrides[pipeline['name']]}
            if overrides.get(pipeline['name']) else pipeline
            for pipeline in pipelines
        ]

    # Optional override of the output file base-execution path extension for this run (defaults
    # to "/"). Later this will fall back to a default stored on the workflow when not provided.
    output_file_base_execution_path_extension = request_model.fileBaseExecutionPathExtension or "/"

    logger.info("Launching Workflow:")
    # Trigger type: auto-trigger callers pass triggerSource='auto-trigger-sqs'.
    trigger_type = "File-Upload" if request_model.triggerSource == 'auto-trigger-sqs' else "Manual"
    executionId = launchWorkflow(
        asset_bucket, asset_file_key, file_key, workflow['workflow_arn'], databaseId,
        assetId, workflow_database_id, workflow['workflowId'],
        executingUserName, executingRequestContext, pipelines,
        inputMetadata, trigger_type, storedJobNames=workflow.get('jobNames'),
        outputFileBaseExecutionPathExtension=output_file_base_execution_path_extension)
    return success(body={'message': executionId})


def handle_post_request(event):
    """Validate path params + request body, then execute the workflow."""
    pathParams = event.get('pathParameters', {}) or {}
    logger.info(pathParams)

    # Parse the request body to JSON first (a missing body is treated as {} so the
    # model's validator later emits the same "workflowDatabaseId is a required field."
    # message the prior handler returned). Malformed JSON is reported before path-param
    # validation, matching the original handler's error precedence.
    body = {}
    if event.get('body'):
        body = event['body']
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': 'Invalid JSON in request body'}, event=event)
    logger.info(f"Request body: {body}")

    # Required path parameters.
    required_field_names = ['databaseId', 'workflowId', 'assetId']
    missing_field_names = list(set(required_field_names).difference(pathParams))
    if missing_field_names:
        message = 'Missing path parameter(s) (%s) in API call' % (', '.join(missing_field_names))
        return validation_error(body={'message': message}, event=event)

    logger.info("Validating path parameters")
    (valid, message) = validate({
        'databaseId': {'value': pathParams.get('databaseId', ''), 'validator': 'ID'},
        'workflowId': {'value': pathParams.get('workflowId', ''), 'validator': 'ID'},
        'assetId': {'value': pathParams.get('assetId', ''), 'validator': 'ASSET_ID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    # Validate the request body fields (workflowDatabaseId / fileKey) via the model.
    request_model = parse(body, model=ExecuteWorkflowRequestModel)

    return execute_workflow(
        event, pathParams['databaseId'], pathParams['assetId'], pathParams['workflowId'], request_model)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for the execute-workflow API (POST)."""
    global claims_and_roles
    logger.info(event)
    claims_and_roles = request_to_claims(event)

    try:
        method = event['requestContext']['http']['method']

        # API-level authorization (Tier 1).
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if method == 'POST':
            return handle_post_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': _clean_validation_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] in ('LimitExceededException', 'ThrottlingException'):
            logger.exception("Throttling Error")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ThrottlingException: Too many requests within a given period.'},
                event=event
            )
        elif err.response['Error']['Code'] == 'ExecutionLimitExceeded':
            logger.exception("ExecutionLimitExceeded")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ExecutionLimitExceeded: Reached the maximum state machine execution limit of 1,000,000'},
                event=event
            )
        else:
            logger.exception(err)
            return internal_error(event=event)
    except Exception as e:
        logger.exception(e)
        return internal_error(event=event)
