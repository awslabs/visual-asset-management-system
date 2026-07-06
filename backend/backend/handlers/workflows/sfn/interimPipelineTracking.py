#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Interim pipeline-tracking lambda.

Inserted in the workflow state machine between two adjacent pipeline steps:

    P(N) -> interim(N -> N+1) -> P(N+1)

On invocation it:
  1. Logs the just-finished pipeline N's OUTPUTS by diffing the shared execution output
     FILES folder against the versionId snapshot reconstructed from the prior pipelines'
     already-recorded output rows (a key is N's output if it is new since that baseline OR
     its latest S3 versionId changed). Records PipelineExecutionOutputFiles rows (with
     s3VersionId) for N, and sets N's stop date + SUCCEEDED status.
  2. Prepares pipeline N+1 by writing its resolved input manifest (originals overlaid by any
     output-files versions that shadow the same relative path) to the asset bucket, and
     returns the N+1 config + manifest S3 locations as this state's SFN result so the next
     pipeline state reads them.

This lambda is reused for every interim gap; the SFN payload carries the gap-specific
from/to pipeline indices + ids. It does NOT modify use-case pipeline containers.
"""

import os
import json
import boto3
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger
from common.workflows import executionRecords as er
from common.workflows import executionOutputs as eo

logger = safeLogger(service="InterimPipelineTracking")

s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

try:
    workflow_execution_database_v2 = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME"]
    pipeline_executions_table = os.environ["PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME"]
    pipeline_execution_output_files_table = os.environ["PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME"]
    workflow_execution_inputs_table = os.environ["WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME"]
    if not all([workflow_execution_database_v2, pipeline_executions_table,
                pipeline_execution_output_files_table, workflow_execution_inputs_table]):
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


def _base_key_from_root(asset_files_s3_root, bucket):
    """The asset-bucket-relative base key from a stored assetFilesS3Root (s3://bucket/baseKey/).
    Returns "" when the root is empty or only the bucket root."""
    root = (asset_files_s3_root or "")
    if not root.startswith("s3://"):
        return ""
    without_scheme = root[len("s3://"):]
    _bkt, _, base = without_scheme.partition("/")
    return base


def _relative_to_asset(full_file_key, base_key):
    """Strip the asset's base location key from a stored (full, asset-ID-prefixed) input file
    key to get the asset-relative path used for shadow matching. Both keys may carry a leading
    slash (the stored inputAssetFileKey is normalize_file_key'd); compare without leading slashes.
    Returns the asset-relative path with a single leading slash."""
    fk = (full_file_key or "").lstrip('/')
    base = (base_key or "").lstrip('/')
    if base and fk.startswith(base):
        fk = fk[len(base):]
    return er.normalize_file_key(fk)


def _get_original_input_entries(workflow_execution_id):
    """Reconstruct the execution's original input asset files as manifest source entries
    ({relativePath, bucket, key, versionId, assetFilesS3Root, ...}) from the WorkflowExecutionInputs
    rows.

    Each input file is self-locating: its own s3Bucket + assetFilesS3Root are stored per row, so
    inputs that span different assets/buckets each resolve against their own root. The stored
    inputAssetFileKey is the FULL asset-bucket key; relativePath must be ASSET-RELATIVE (that
    file's own base key stripped) so it matches the asset-relative keys build_resolved_manifest
    derives from the output FILES folder. key stays the full S3 key (the actual object location)."""
    table = dynamodb.Table(workflow_execution_inputs_table)
    entries = []
    kwargs = {'KeyConditionExpression': Key('workflowExecutionId').eq(workflow_execution_id)}
    resp = table.query(**kwargs)
    while True:
        for row in resp.get('Items', []):
            file_key = row.get('inputAssetFileKey', '')
            file_bucket = row.get('s3Bucket', '')
            asset_files_root = row.get('assetFilesS3Root', '')
            base_key = _base_key_from_root(asset_files_root, file_bucket)
            entries.append({
                "relativePath": _relative_to_asset(file_key, base_key),
                "databaseId": row.get('databaseId', ''),
                "assetId": row.get('assetId', ''),
                "assetFilesS3Root": asset_files_root,
                "bucket": file_bucket,
                "key": file_key.lstrip('/'),
                "versionId": "",
            })
        if 'LastEvaluatedKey' not in resp:
            break
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = table.query(**kwargs)
    return entries


def record_previous_pipeline_outputs(body):
    """Diff + record pipeline N's outputs and mark N complete. Returns N's output-files
    listing (used by the manifest build) and the produced files."""
    from_pipeline_execution_id = body.get('fromPipelineExecutionId', '')
    prior_pipeline_execution_ids = body.get('priorPipelineExecutionIds', []) or []
    workflow_execution_id = body.get('workflowExecutionId', '')
    # The shared pipeline output folder lives in the workflow-execution I/O bucket.
    wf_exec_bucket = body.get('workflowExecutionS3InputOutputBucket', '')
    output_files_prefix = body.get('outputFilesPrefix', '')

    # Baseline = output versions recorded by the pipelines BEFORE N (excludes N itself).
    baseline = eo.recorded_output_versions(
        dynamodb, pipeline_execution_output_files_table,
        [pid for pid in prior_pipeline_execution_ids if pid and pid != from_pipeline_execution_id])

    current_files = eo.list_current_output_files(s3c, wf_exec_bucket, output_files_prefix)
    produced = eo.attribute_pipeline_outputs(current_files, baseline)

    if from_pipeline_execution_id:
        eo.record_pipeline_output_files(
            dynamodb, pipeline_execution_output_files_table,
            from_pipeline_execution_id, wf_exec_bucket, produced)
        eo.set_pipeline_status(
            dynamodb, pipeline_executions_table,
            from_pipeline_execution_id, workflow_execution_id,
            "SUCCEEDED", stop_date=er.iso_now())

    return current_files


def prepare_next_pipeline(body):
    """Write pipeline N+1's resolved input manifest envelope to the asset bucket and return the
    N+1 config + manifest S3 locations for the SFN result.

    The envelope context (output/aux locations, metadata location, system config) is threaded
    from the ASL into the interim payload; the input FILES are resolved here (shadowing) from
    the execution's original inputs overlaid by the shared output FILES folder."""
    # The workflow-execution I/O bucket holds the shared pipeline output folder + the per-pipeline
    # manifest/config files (NOT the input asset files, whose own buckets come from the input rows).
    wf_exec_bucket = body.get('workflowExecutionS3InputOutputBucket', '')
    output_files_prefix = body.get('outputFilesPrefix', '')
    workflow_execution_id = body.get('workflowExecutionId', '')
    next_manifest_key = body.get('nextPipelineManifestS3Key', '')
    next_config_key = body.get('nextPipelineConfigS3Key', '')

    # Envelope context for the next pipeline (output/aux/metadata locations + system config),
    # threaded from the ASL. The next pipeline's orchestration event prefix is built here from
    # the source prefix + execution id + the next pipeline-execution id.
    aux_bucket = body.get('bucketAssetAuxiliary', '')
    next_aux_prefix = body.get('nextPipelineAuxPrefix', '')
    next_event_prefix = ""
    src_prefix = body.get('orchestrationEventSourcePrefix', '')
    next_pexec_id = body.get('nextPipelineExecutionId', '')
    if src_prefix and next_pexec_id:
        next_event_prefix = er.orchestration_event_prefix(src_prefix, workflow_execution_id, next_pexec_id)
    envelope_context = {
        "inputMetadataS3Location": body.get('inputMetadataS3Location', ''),
        "outputs": {
            "files": body.get('outputFilesUri', ''),
            "previews": body.get('outputPreviewsUri', ''),
            "metadata": body.get('outputMetadataUri', ''),
            "results": body.get('outputResultsUri', ''),
        },
        "outputTarget": er.build_manifest_output_target(
            location_type=body.get('outputLocationType', 'asset'),
            asset_id=body.get('outputAssetId', ''),
            database_id=body.get('outputDatabaseId', ''),
            file_base_execution_path_extension=body.get('outputFileBaseExecutionPathExtension', '/')),
        "auxBucketS3Root": f"s3://{aux_bucket}/" if aux_bucket else "",
        "auxTempPrefix": f"s3://{aux_bucket}/{next_aux_prefix}" if (aux_bucket and next_aux_prefix) else "",
        "auxPreviewPrefix": f"s3://{aux_bucket}/{next_aux_prefix}" if (aux_bucket and next_aux_prefix) else "",
        "systemConfig": er.build_manifest_system_config(
            orchestration_bus_arn=body.get('orchestrationBusArn', ''),
            orchestration_event_prefix=next_event_prefix),
    }

    original_inputs = _get_original_input_entries(workflow_execution_id)
    # Output files are listed/shadowed from the workflow-exec I/O bucket (the shared output folder).
    manifest = eo.build_resolved_manifest(
        s3c, original_inputs, wf_exec_bucket, output_files_prefix, envelope_context=envelope_context)

    if next_manifest_key:
        s3c.put_object(Bucket=wf_exec_bucket, Key=next_manifest_key,
                       Body=json.dumps(manifest).encode('utf-8'), ContentType='application/json')

    return {
        "inputManifestS3Location": f"s3://{wf_exec_bucket}/{next_manifest_key}" if next_manifest_key else "",
        "inputConfigurationS3Location": f"s3://{wf_exec_bucket}/{next_config_key}" if next_config_key else "",
        "nextPipelineManifestS3Key": next_manifest_key,
        "nextPipelineConfigS3Key": next_config_key,
    }


def lambda_handler(event, context):
    """SFN-invoked interim tracking between pipeline N and N+1.

    Returns the next-pipeline input locations as the state result. Never finalizes the
    overall execution (that is the end-state processWorkflowExecutionOutput lambda)."""
    logger.info(event)
    body = event.get('body', event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in interim body: {e}")
            raise

    # 1) Record the just-finished pipeline N's outputs + completion.
    record_previous_pipeline_outputs(body)

    # 2) Prepare pipeline N+1's resolved input manifest + return its input locations.
    next_locations = prepare_next_pipeline(body)

    logger.info(f"Interim tracking complete; next pipeline locations: {next_locations}")
    return next_locations
