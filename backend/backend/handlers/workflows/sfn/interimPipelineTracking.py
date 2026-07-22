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
from common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from common.workflows import executionRecords as er
from common.workflows import executionOutputs as eo
from common.workflows import templateRender as tr

logger = safeLogger(service="InterimPipelineTracking")

s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

try:
    workflow_execution_database_v2 = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
    pipeline_execution_output_files_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE)
    workflow_execution_inputs_table = get_table_name(ResourceKeys.WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE)
    # Auxiliary bucket name, written into each next pipeline's manifest.auxBucket. Resolved here
    # (not threaded through the SFN input) so the aux bucket lives in one place per its purpose.
    bucket_name_assetAuxiliary = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    # Orchestration bus ARN + event source prefix, written into each next pipeline's
    # manifest.systemConfig. Sourced from the lambda environment (not the SFN input) so the
    # config lives in one place per its intended purpose. Optional: empty if unset.
    orchestration_bus_arn = os.environ.get("ORCHESTRATION_BUS_ARN", "")
    orchestration_event_source_prefix = os.environ.get("ORCHESTRATION_EVENT_SOURCE_PREFIX", "")
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e


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
    ({relativePath, bucket, key, versionId, assetRootS3Key, auxPreviewPrefix, ...}) from the
    WorkflowExecutionInputs rows.

    Each input file is self-locating: its own s3Bucket + assetRootS3Key (a bucket-relative asset
    root prefix, no s3:// URI) are stored per row, so inputs that span different assets/buckets
    each resolve against their own root. The stored inputAssetFileKey is the FULL asset-bucket key;
    relativePath must be ASSET-RELATIVE (that file's own asset-root key stripped) so it matches the
    asset-relative keys build_resolved_manifest derives from the output FILES folder. key stays the
    full S3 key (the actual object location). Each file's aux preview prefix is rebuilt from its
    database/asset identity + asset-relative path so it stays per-file and unique."""
    table = dynamodb.Table(workflow_execution_inputs_table)
    entries = []
    kwargs = {'KeyConditionExpression': Key('workflowExecutionId').eq(workflow_execution_id)}
    resp = table.query(**kwargs)
    while True:
        for row in resp.get('Items', []):
            file_key = row.get('inputAssetFileKey', '')
            file_bucket = row.get('s3Bucket', '')
            asset_root_s3_key = row.get('assetRootS3Key', '')
            database_id = row.get('databaseId', '')
            asset_id = row.get('assetId', '')
            relative_path = _relative_to_asset(file_key, asset_root_s3_key)
            entries.append({
                "relativePath": relative_path,
                "databaseId": database_id,
                "assetId": asset_id,
                "assetRootS3Key": asset_root_s3_key,
                # Keyed on the FULL asset file key (location key + relative path) so a custom asset
                # base prefix is preserved: {databaseId}/{assetFileKey}/preview.
                "auxPreviewPrefix": er.aux_preview_file_prefix(database_id, file_key.lstrip('/')),
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

    # Envelope context for the next pipeline (output/aux/metadata locations + system config). The
    # output prefixes are asset-bucket-RELATIVE (threaded from the ASL) and pair with the output
    # bucket (the workflow-execution I/O bucket); the aux temp prefix is bucket-relative and
    # execution-scoped. The next pipeline's orchestration event prefix is built here from the
    # env-sourced source prefix + execution id + the next pipeline-execution id (the bus config
    # is not threaded through the SFN input).
    aux_bucket = bucket_name_assetAuxiliary
    next_aux_temp_prefix = body.get('nextPipelineAuxTempPrefix', '')
    next_event_prefix = ""
    next_pexec_id = body.get('nextPipelineExecutionId', '')
    if orchestration_event_source_prefix and next_pexec_id:
        next_event_prefix = er.orchestration_event_prefix(
            orchestration_event_source_prefix, workflow_execution_id, next_pexec_id)
    envelope_context = {
        "inputMetadataS3Location": body.get('inputMetadataS3Location', ''),
        "outputs": er.build_manifest_outputs(
            bucket=wf_exec_bucket,
            files=body.get('outputFilesPrefixRelative', ''),
            previews=body.get('outputPreviewsPrefixRelative', ''),
            metadata=body.get('outputMetadataPrefixRelative', ''),
            results=body.get('outputResultsPrefixRelative', '')),
        "outputTarget": er.build_manifest_output_target(
            location_type=body.get('outputLocationType', 'asset'),
            asset_id=body.get('outputAssetId', ''),
            database_id=body.get('outputDatabaseId', ''),
            file_base_execution_path_extension=body.get('outputFileBaseExecutionPathExtension', '/')),
        "auxBucket": aux_bucket,
        "auxTempPrefix": next_aux_temp_prefix,
        # Per-pipeline viewer subfolder; empty until sourced from pipeline configuration.
        "auxPreviewPipelineSuffix": "",
        "systemConfig": er.build_manifest_system_config(
            orchestration_bus_arn=orchestration_bus_arn,
            orchestration_event_prefix=next_event_prefix),
    }

    original_inputs = _get_original_input_entries(workflow_execution_id)
    # Output files are listed/shadowed from the workflow-exec I/O bucket (the shared output folder).
    manifest = eo.build_resolved_manifest(
        s3c, original_inputs, wf_exec_bucket, output_files_prefix, envelope_context=envelope_context)

    if next_manifest_key:
        s3c.put_object(Bucket=wf_exec_bucket, Key=next_manifest_key,
                       Body=json.dumps(manifest).encode('utf-8'), ContentType='application/json')

    # Render the NEXT pipeline's input configuration template tags against ITS manifest + execution
    # context, then re-write it in place. The raw (unrendered) config was written at launch by
    # executeWorkflow; this renders it per task (so tags reflect this task's shadowed inputs). The
    # metadata payload is read lazily (only when a metadata-content tag is present).
    _render_next_pipeline_config(body, manifest, wf_exec_bucket, next_config_key)

    return {
        "inputManifestS3Location": f"s3://{wf_exec_bucket}/{next_manifest_key}" if next_manifest_key else "",
        "inputConfigurationS3Location": f"s3://{wf_exec_bucket}/{next_config_key}" if next_config_key else "",
        "nextPipelineManifestS3Key": next_manifest_key,
        "nextPipelineConfigS3Key": next_config_key,
    }


def _render_next_pipeline_config(body, manifest, wf_exec_bucket, next_config_key):
    """Read the next pipeline's raw input configuration from S3, substitute its template tags
    against the next-pipeline manifest + execution context, and re-write it in place. No-op when
    there is no config key or the config has no tags. Never raises on a missing config object (an
    absent config is simply left as-is); a bad/unknown tag DOES raise (strict) so the failure is
    caught by the interim state's Catch and reconciled as a workflow failure."""
    if not next_config_key:
        return
    try:
        resp = s3c.get_object(Bucket=wf_exec_bucket, Key=next_config_key)
        raw_cfg = resp["Body"].read().decode("utf-8")
    except Exception as e:  # nosec B110 - a missing/empty config file is valid; nothing to render
        logger.info(f"No input configuration to render for next pipeline (non-critical): {e}")
        return
    if not tr.uses_template_tags(raw_cfg):
        return

    workflow_execution_id = body.get('workflowExecutionId', '')
    next_config_location = f"s3://{wf_exec_bucket}/{next_config_key}"
    exec_context = {
        "executionId": workflow_execution_id,
        "workflowId": body.get('workflowId', ''),
        "workflowDatabaseId": body.get('workflowDatabaseId', ''),
        "pipelineExecutionId": body.get('nextPipelineExecutionId', ''),
        "pipelineId": body.get('nextPipelineId', ''),
        "pipelineDatabaseId": body.get('nextPipelineDatabaseId', ''),
        "jobName": body.get('nextPipelineJobName', ''),
        "executingUserName": body.get('executingUserName', ''),
        "inputConfigurationS3Location": next_config_location,
    }

    def _metadata_payload():
        # The shared metadata file is loaded from its manifest location only if a metadata-content
        # tag is actually used. It is written as one of two envelopes: the v1 {schemaVersion, metadata}
        # wrapper (unwrap to the inner VAMS payload) or the v2 grouped-by-asset envelope
        # ({schemaVersion: 2, assets: [...]}), which is projected to the legacy {"VAMS": {...}} view the
        # renderer's metadata-content tags read, for this run's primary input file.
        location = manifest.get("inputMetadataS3Location", "")
        if not location or not location.startswith("s3://"):
            return {}
        bkt, _, key = location[len("s3://"):].partition("/")
        if not bkt or not key:
            return {}
        try:
            resp = s3c.get_object(Bucket=bkt, Key=key)
            payload = json.loads(resp["Body"].read().decode("utf-8"))
        except Exception:  # nosec B110 - best-effort; an unreadable metadata file yields {}
            return {}
        if not isinstance(payload, dict):
            return {}
        if payload.get("schemaVersion") == er.METADATA_SCHEMA_VERSION_GROUPED and "assets" in payload:
            first = (manifest.get("inputFiles") or [{}])[0] or {}
            return er.to_legacy_vams_view(
                payload, first.get("databaseId", ""), first.get("assetId", ""),
                first.get("relativePath", "/"))
        if "metadata" in payload and "schemaVersion" in payload:
            return payload.get("metadata") or {}
        return payload

    rendered = tr.render_config(raw_cfg, manifest, exec_context, metadata_loader=_metadata_payload)
    s3c.put_object(Bucket=wf_exec_bucket, Key=next_config_key,
                   Body=rendered.encode("utf-8"), ContentType="application/json")


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
