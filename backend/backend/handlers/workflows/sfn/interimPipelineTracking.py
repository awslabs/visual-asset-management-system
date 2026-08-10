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
from botocore.config import Config
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger
from common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from common.workflows import executionRecords as er
from common.workflows import executionOutputs as eo
from common.workflows import executionValidation as ev
from common.workflows import templateRender as tr
from models.common import VAMSGeneralErrorResponse

logger = safeLogger(service="InterimPipelineTracking")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
s3c = boto3.client('s3', config=retry_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)

# The declared arity of a step that consumes no input files (models/pipelines.py inputFileArity).
ARITY_NONE = "none"

# S3 error codes that mean the object genuinely is not there. Any other failure on an S3 read is a
# fault, not an absent file, and must surface rather than degrade the step's inputs.
_ABSENT_OBJECT_ERROR_CODES = ("NoSuchKey", "NoSuchBucket", "404")

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
                # The concrete version the run resolved at launch, so a later step reads the same
                # object bytes step 1 read rather than whatever is latest now.
                "versionId": row.get('versionId', '') or "",
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

    # Advance the next pipeline NEW -> RUNNING (pipeline 1 is set RUNNING at launch).
    to_pipeline_execution_id = body.get('nextPipelineExecutionId', '') or body.get('toPipelineExecutionId', '')
    if to_pipeline_execution_id:
        eo.set_pipeline_status_running(
            dynamodb, pipeline_executions_table, to_pipeline_execution_id, workflow_execution_id)
        _stamp_pipeline_start_date(to_pipeline_execution_id, workflow_execution_id, er.iso_now())

    return current_files


def _stamp_pipeline_start_date(pipeline_execution_id, workflow_execution_id, start_date):
    """Record when a pipeline step began, alongside its NEW -> RUNNING flip, so the execution
    details can report a per-step duration.

    Written only with a real value: executionStartDate is a GSI sort key on the execution tables and
    DynamoDB rejects an empty string for an indexed key attribute. Conditioned on the row not
    already carrying one so a re-invoked interim step keeps the first start. Best-effort — a failed
    timing stamp must not fail the step transition."""
    if not start_date or not pipeline_execution_id:
        return
    table = dynamodb.Table(pipeline_executions_table)
    try:
        table.update_item(
            Key={"pipelineExecutionId": pipeline_execution_id,
                 "workflowExecutionId": workflow_execution_id},
            UpdateExpression="SET executionStartDate = :sd",
            ConditionExpression=(
                "attribute_not_exists(executionStartDate) OR executionStartDate = :empty"),
            ExpressionAttributeValues={":sd": start_date, ":empty": ""},
        )
    except Exception as e:
        if _error_code(e) != "ConditionalCheckFailedException":
            logger.warning(
                f"Could not stamp the start date on pipeline execution {pipeline_execution_id}: {e}")


def _error_code(error):
    """The AWS error code on a boto3 exception, or '' when it carries none."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return (response.get("Error") or {}).get("Code", "") or ""
    return ""


def narrow_next_pipeline_inputs(body, input_files):
    """The next step's own share of the resolved input files: the entries passing its effective
    inputFileFilters, gated by its declared inputFileArity.

    Per-step INTAKE, the counterpart to the metadata narrowing in resolve_next_metadata_location.
    Step 1's manifest is narrowed at launch, where the effective config resolves; steps 2+ have their
    manifests assembled here, so the step's filters and arity travel in the interim payload
    (nextPipelineInputFileFilters / nextPipelineInputFileArity) the same way its metadata key does.
    Without them the manifest carries the run's entire selection plus every file a prior step
    produced, so a step receives files it never declared it could read.

    An absent or empty filter map narrows nothing, so an execution launched without the keys keeps
    the full selection. An arity the narrowed set cannot satisfy raises: the interim state's Catch
    reconciles the run as FAILED with the reason on the record, rather than handing the pipeline a
    selection it rejects opaquely (or waits out its callback timeout on)."""
    arity = (body or {}).get('nextPipelineInputFileArity', '') or ""
    if arity == ARITY_NONE:
        return []

    filters = (body or {}).get('nextPipelineInputFileFilters') or {}
    narrowed = input_files
    if isinstance(filters, dict) and (filters.get("allow") or filters.get("exclude")):
        # The filter helper reads each entry's 'relativeFileKey'; manifest entries carry the same
        # asset-relative path as 'relativePath'.
        candidates = [{"relativeFileKey": f.get("relativePath", ""), "manifestEntry": f}
                      for f in input_files]
        narrowed = [c["manifestEntry"]
                    for c in ev.apply_input_file_filters(candidates, filters)]

    if arity:
        violation = ev._arity_violation(arity, len(narrowed))
        if violation:
            next_pipeline_id = (body or {}).get('nextPipelineId', '')
            raise VAMSGeneralErrorResponse(
                f"Pipeline '{next_pipeline_id}' {violation} after its input filters were applied to "
                f"the previous pipeline's outputs.")
    return narrowed


def prepare_next_pipeline(body, current_output_files=None):
    """Write pipeline N+1's resolved input manifest envelope to the asset bucket and return the
    N+1 config + manifest S3 locations for the SFN result.

    The envelope context (output/aux locations, metadata location, system config) is threaded
    from the ASL into the interim payload; the input FILES are resolved here (shadowing) from
    the execution's original inputs overlaid by the shared output FILES folder.

    current_output_files: the output-files listing the attribution step already took in this
    invocation, so the shared output folder is listed once per step transition rather than twice."""
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
    next_metadata_location = resolve_next_metadata_location(body, wf_exec_bucket)

    envelope_context = {
        "inputMetadataS3Location": next_metadata_location,
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
        s3c, original_inputs, wf_exec_bucket, output_files_prefix,
        envelope_context=envelope_context, current_output_files=current_output_files)
    manifest["inputFiles"] = narrow_next_pipeline_inputs(body, manifest.get("inputFiles") or [])

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


def resolve_next_metadata_location(body, wf_exec_bucket):
    """The metadata S3 location the NEXT pipeline step reads.

    Per-step DELIVERY of the two-level metadataInputs contract: the next step reads its OWN narrowed
    metadata file when executeWorkflow wrote one for it (its key threaded through the ASL as
    nextPipelineMetadataS3Key), otherwise the shared per-execution envelope. Narrowing is computed at
    launch, where template overrides resolve; only the resulting key travels. The INTAKE half is the
    workflow gate in executeWorkflow._build_grouped_metadata.

    Fails CLOSED to today's behavior: this runs MID-EXECUTION, so an absent or unusable threaded key
    delivers the SHARED envelope rather than nothing. Handing a step an empty payload would break a
    pipeline that needs metadata — worse than delivering the wider set."""
    shared_location = (body or {}).get('inputMetadataS3Location', '')
    next_metadata_key = (body or {}).get('nextPipelineMetadataS3Key', '')
    if isinstance(next_metadata_key, str) and next_metadata_key.strip():
        return f"s3://{wf_exec_bucket}/{next_metadata_key.strip()}"
    if next_metadata_key not in ("", None):
        logger.warning(
            f"Ignoring malformed nextPipelineMetadataS3Key ({next_metadata_key!r}); delivering the "
            f"shared execution metadata envelope to the next pipeline.")
    return shared_location


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
    except ClientError as e:
        if _error_code(e) not in _ABSENT_OBJECT_ERROR_CODES:
            raise
        logger.info(f"No input configuration to render for next pipeline: {e}")
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
        except ClientError as e:
            if _error_code(e) not in _ABSENT_OBJECT_ERROR_CODES:
                raise
            logger.info(f"No metadata envelope to render for next pipeline: {e}")
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
    current_files = record_previous_pipeline_outputs(body)

    # 2) Prepare pipeline N+1's resolved input manifest + return its input locations. The output
    #    folder listing from step 1 is reused: the shadowing pass needs the identical set.
    next_locations = prepare_next_pipeline(body, current_output_files=current_files)

    logger.info(f"Interim tracking complete; next pipeline locations: {next_locations}")
    return next_locations
