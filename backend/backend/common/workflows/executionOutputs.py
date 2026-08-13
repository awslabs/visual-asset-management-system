# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared output-attribution + status-finalization logic for the workflow-execution
interim tracking lambda, the end-state processWorkflowExecutionOutput lambda, and the
error-handler lambda.

This module centralizes the parts those three lambdas must do identically:

  - S3 versionId snapshots of the shared execution output FILES folder, and the diff that
    attributes "which files did pipeline N produce" (a key is N's output if it is new since
    the pre-N snapshot OR its latest versionId changed).
  - Writing PipelineExecutionOutputFiles rows (with s3VersionId) and setting a pipeline
    row's stop date + status.
  - Finalizing the V2 main execution row (stop date, status, error, full log), stopping the
    sub-processes an in-flight pipeline registered, and marking in-flight pipeline rows
    terminal (used by the error handler).
  - Building the resolved per-pipeline input manifest (original asset file, or the latest
    output-files version when a prior pipeline shadowed that relative path).

Callers inject their boto3 dynamo resource + s3 client and the resolved table names so this
module stays free of os.environ / client construction (mirrors common.workflows.executionRecords).
"""

from common.workflows import executionRecords as er

# Terminal statuses: a pipeline/main row already in one of these is finished and is not
# re-stamped by the error handler (only in-flight rows are marked terminal).
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT")

# Sub-process resource types a registered locator can name (mirrors registerPipelineExecution).
RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION = "stepFunctionsExecution"
# An AWS Batch job a pipeline submitted ITSELF. A job submitted by a nested state machine through the
# Step Functions `.sync` integration needs no entry: Step Functions owns that job's lifecycle, so
# stopping the sub-execution already terminates it.
RESOURCE_TYPE_BATCH_JOB = "batchJob"


# ---------------------------------------------------------------------------
# S3 version snapshots + output attribution
# ---------------------------------------------------------------------------

def _listed_version_id(version_entry):
    """The usable versionId from a list_object_versions entry. An unversioned bucket reports the
    literal 'null', which carries no change signal, so it reads as absent — attribution then treats
    the key as produced rather than assuming it was untouched."""
    version_id = version_entry.get('VersionId', '') or ''
    return "" if version_id == "null" else version_id


def snapshot_output_versions(s3_client, bucket, files_prefix):
    """Return {key: latestVersionId} for every current object version under the output
    files prefix. Used as the 'before pipeline N' baseline for output attribution.

    Best-effort: returns {} on any error (attribution then treats everything found after as
    new, which over-attributes rather than losing outputs)."""
    snapshot = {}
    if not bucket or not files_prefix:
        return snapshot
    try:
        paginator = s3_client.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=bucket, Prefix=files_prefix):
            for v in page.get('Versions', []):
                if v.get('IsLatest') and not v.get('Key', '').endswith('/'):
                    snapshot[v['Key']] = _listed_version_id(v)
    except Exception:
        # list_object_versions can fail if versioning is suspended or perms differ; the
        # caller still functions with an empty baseline.
        return {}
    return snapshot


def list_current_output_files(s3_client, bucket, files_prefix):
    """Return a list of {key, relativePath, versionId, fileSize, contentType} for every
    current object under the output files prefix. relativePath is asset-relative to the
    files prefix (leading slash). Tolerates a missing/expired object (size/contentType may
    be 0/'').

    Both the key set and each key's versionId come from ONE list_object_versions pagination (the
    IsLatest entry per key), so a thousand-file output folder costs a handful of list calls rather
    than a request per object. A prefix whose versions cannot be listed falls back to a plain
    listing with no version signal, which over-attributes rather than losing outputs."""
    out = []
    if not bucket or not files_prefix:
        return out
    try:
        paginator = s3_client.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=bucket, Prefix=files_prefix):
            for v in page.get('Versions', []):
                key = v.get('Key', '')
                if not key or key.endswith('/') or not v.get('IsLatest'):
                    continue
                out.append(_output_file_entry(key, files_prefix, _listed_version_id(v),
                                              v.get('Size', 0)))
        return out
    except Exception:
        return _list_current_output_files_unversioned(s3_client, bucket, files_prefix)


def _list_current_output_files_unversioned(s3_client, bucket, files_prefix):
    """The output-files listing with no version signal, for a prefix whose versions cannot be listed
    (versioning suspended, or list_object_versions denied)."""
    out = []
    paginator = s3_client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=files_prefix):
        for obj in page.get('Contents', []):
            key = obj.get('Key', '')
            if not key or key.endswith('/'):
                continue
            out.append(_output_file_entry(key, files_prefix, "", obj.get('Size', 0)))
    return out


def _output_file_entry(key, files_prefix, version_id, size):
    """One output-files listing entry. relativePath is asset-relative to the files prefix."""
    relative = key[len(files_prefix):] if key.startswith(files_prefix) else key
    return {
        "key": key,
        "relativePath": er.normalize_file_key(relative),
        "versionId": version_id,
        "fileSize": size or 0,
        "contentType": "",
    }


def attribute_pipeline_outputs(current_files, snapshot_before):
    """Given the current output-files listing and the {key: versionId} snapshot taken
    before this pipeline ran, return the subset of current_files this pipeline produced:
    keys not present in the snapshot, OR whose latest versionId changed.

    A key with an empty versionId carries no evidence either way and is attributed to this
    pipeline, erring toward over-attribution rather than dropping a produced output."""
    produced = []
    for f in current_files:
        prev_version = snapshot_before.get(f["key"])
        version = f.get("versionId", "")
        if prev_version is None or not version or prev_version != version:
            produced.append(f)
    return produced


def recorded_output_versions(dynamo, output_files_table, prior_pipeline_execution_ids):
    """Reconstruct the {s3Key: s3VersionId} snapshot of the output FILES folder as it stood
    after the prior pipelines, from the PipelineExecutionOutputFiles rows already recorded for
    those pipeline executions. This is the versionId baseline an interim/end-state diff uses to
    attribute the next pipeline's outputs (new key OR changed version), without a separate S3
    snapshot file. Idempotent: re-running a step diffs against the same recorded baseline."""
    snapshot = {}
    if not prior_pipeline_execution_ids:
        return snapshot
    table = dynamo.Table(output_files_table)
    for pexec_id in prior_pipeline_execution_ids:
        if not pexec_id:
            continue
        kwargs = {'KeyConditionExpression': _key_eq('pipelineExecutionId', pexec_id)}
        resp = table.query(**kwargs)
        while True:
            for row in resp.get('Items', []):
                if row.get('s3Key'):
                    snapshot[row['s3Key']] = row.get('s3VersionId', '')
            if 'LastEvaluatedKey' not in resp:
                break
            kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
            resp = table.query(**kwargs)
    return snapshot


def _key_eq(attr, value):
    """Local Key().eq() builder (kept here so this module needs no boto3 import for callers
    that only use the pure helpers)."""
    from boto3.dynamodb.conditions import Key
    return Key(attr).eq(value)


# ---------------------------------------------------------------------------
# Resolved per-pipeline input manifest (shadowing)
# ---------------------------------------------------------------------------

def resolve_manifest_input_files(s3_client, original_inputs, output_bucket, output_files_prefix,
                                 current_output_files=None):
    """Resolve the next pipeline's input FILE entries (the shadowing logic).

    original_inputs: list of self-locating entries {relativePath, databaseId, assetId,
        assetRootS3Key, auxPreviewPrefix, bucket, key, versionId} for the execution's original
        input files. For each original input, if a file exists at the SAME asset-relative path
        under the output files prefix, the resolved entry points at that output file's
        bucket/key/latest versionId (a prior pipeline shadowed it) -- preserving the original's
        databaseId/assetId/root/aux-preview for traceability; otherwise it keeps the original
        entry. Only the output FILES folder shadows. Output files at relative paths with no
        matching input are appended as additional inputs (new files a prior pipeline produced),
        located in the output bucket.

    current_output_files: the output-files listing when the caller already has it (the attribution
        step in the same invocation lists the identical set), so the listing is not repeated."""
    output_files = (current_output_files if current_output_files is not None
                    else list_current_output_files(s3_client, output_bucket, output_files_prefix))
    output_by_rel = {}
    for f in output_files:
        output_by_rel[f["relativePath"]] = f

    resolved = []
    seen_rel = set()
    for entry in original_inputs:
        rel = er.normalize_file_key(entry.get("relativePath", ""))
        seen_rel.add(rel)
        shadow = output_by_rel.get(rel)
        if shadow:
            # Shadowed in place: point at the output file's version but keep the original's
            # asset identity/root/aux-preview for traceability.
            resolved.append(er.build_manifest_entry(
                rel, output_bucket, shadow["key"], shadow.get("versionId", ""),
                database_id=entry.get("databaseId", ""), asset_id=entry.get("assetId", ""),
                asset_root_s3_key=entry.get("assetRootS3Key", ""),
                aux_preview_prefix=entry.get("auxPreviewPrefix", "")))
        else:
            resolved.append(er.build_manifest_entry(
                rel, entry.get("bucket", ""), entry.get("key", ""), entry.get("versionId", ""),
                database_id=entry.get("databaseId", ""), asset_id=entry.get("assetId", ""),
                asset_root_s3_key=entry.get("assetRootS3Key", ""),
                aux_preview_prefix=entry.get("auxPreviewPrefix", "")))

    # New output files (no matching original input path) become additional inputs, located in
    # the output bucket (no originating asset identity).
    for rel, f in output_by_rel.items():
        if rel not in seen_rel:
            resolved.append(er.build_manifest_entry(rel, output_bucket, f["key"], f.get("versionId", "")))
    return resolved


def build_resolved_manifest(s3_client, original_inputs, output_bucket, output_files_prefix,
                            envelope_context=None, current_output_files=None):
    """Build the next pipeline's full manifest envelope: resolve the input files (shadowing),
    then wrap them with the output/aux locations + system config from envelope_context.

    envelope_context: {inputMetadataS3Location, outputs{bucket,files,previews,metadata,results},
        outputTarget{locationType,assetId,databaseId}, auxBucket, auxTempPrefix,
        auxPreviewPipelineSuffix, systemConfig}. When omitted (legacy callers/tests), returns an
        envelope with empty location/config fields. Per-input-file aux preview prefixes live on
        each resolved input file entry (carried through from the original inputs).

    current_output_files: the output-files listing when the caller already has it, passed through to
        resolve_manifest_input_files so the listing is not repeated."""
    ctx = envelope_context or {}
    resolved_files = resolve_manifest_input_files(
        s3_client, original_inputs, output_bucket, output_files_prefix,
        current_output_files=current_output_files)
    return er.build_manifest_envelope(
        input_files=resolved_files,
        input_metadata_s3_location=ctx.get("inputMetadataS3Location", ""),
        outputs=ctx.get("outputs", {}),
        aux_bucket=ctx.get("auxBucket", ""),
        aux_temp_prefix=ctx.get("auxTempPrefix", ""),
        aux_preview_pipeline_suffix=ctx.get("auxPreviewPipelineSuffix", ""),
        system_config=ctx.get("systemConfig", {}),
        output_target=ctx.get("outputTarget"),
    )


# ---------------------------------------------------------------------------
# Recording output files + pipeline / main row finalization
# ---------------------------------------------------------------------------

def record_pipeline_output_files(dynamo, output_files_table, pipeline_execution_id, bucket,
                                 produced_files):
    """Write one PipelineExecutionOutputFiles row per produced file (with s3VersionId)."""
    if not produced_files:
        return
    table = dynamo.Table(output_files_table)
    for f in produced_files:
        table.put_item(Item=er.build_output_file_record(
            pipeline_execution_id=pipeline_execution_id,
            file_type=f.get("fileType", "file"),
            relative_file_path=f.get("relativePath", "").lstrip("/"),
            s3_bucket=bucket, s3_key=f.get("key", ""),
            file_size=f.get("fileSize", 0), content_type=f.get("contentType", ""),
            s3_version_id=f.get("versionId", ""),
        ))


def _is_conditional_check_failure(error):
    """Whether a DynamoDB write error is a ConditionalCheckFailedException (the row already holds a
    terminal status), as opposed to a real failure that must surface."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code", "")
        if code:
            return code == "ConditionalCheckFailedException"
    return "ConditionalCheckFailed" in str(error)


def set_pipeline_status(dynamo, pipeline_executions_table, pipeline_execution_id,
                        workflow_execution_id, status, stop_date=None):
    """Set a pipeline-execution row's status (and stop date when terminal), conditioned on the row not
    already being terminal so a still-in-flight writer (the interim tracking lambda) cannot regress a
    row the abort/error path already finished. A row that is already terminal is left untouched."""
    table = dynamo.Table(pipeline_executions_table)
    expr = "SET executionStatus = :st"
    values = {":st": status}
    if stop_date:
        expr += ", executionStopDate = :s"
        values[":s"] = stop_date
    terminal_names = {}
    for index, terminal in enumerate(TERMINAL_STATUSES):
        placeholder = f":term{index}"
        terminal_names[placeholder] = terminal
    values.update(terminal_names)
    condition = ("attribute_not_exists(executionStatus) OR NOT executionStatus IN ("
                 + ", ".join(terminal_names) + ")")
    try:
        table.update_item(
            Key={"pipelineExecutionId": pipeline_execution_id,
                 "workflowExecutionId": workflow_execution_id},
            UpdateExpression=expr,
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
        )
    except Exception as e:
        if not _is_conditional_check_failure(e):
            raise


def set_main_status_running(dynamo, main_table_name, workflow_execution_id, workflow_database_id,
                            workflow_id):
    """Flip the main row NEW -> RUNNING, conditioned on NEW so it never clobbers a terminal status a
    fast end-state lambda already wrote. Best-effort (the ConditionalCheckFailed is expected)."""
    table = dynamo.Table(main_table_name)
    try:
        table.update_item(
            Key={"workflowExecutionId": workflow_execution_id,
                 "workflowDatabaseId:workflowId":
                     er.workflow_composite_key(workflow_database_id, workflow_id)},
            UpdateExpression="SET executionStatus = :st",
            ConditionExpression="executionStatus = :new",
            ExpressionAttributeValues={":st": "RUNNING", ":new": "NEW"},
        )
    except Exception:  # nosec B110 - ConditionalCheckFailed (already terminal) is expected
        pass


def set_pipeline_status_running(dynamo, pipeline_executions_table, pipeline_execution_id,
                                workflow_execution_id):
    """Flip a pipeline row NEW -> RUNNING (conditioned on NEW; best-effort)."""
    table = dynamo.Table(pipeline_executions_table)
    try:
        table.update_item(
            Key={"pipelineExecutionId": pipeline_execution_id,
                 "workflowExecutionId": workflow_execution_id},
            UpdateExpression="SET executionStatus = :st",
            ConditionExpression="executionStatus = :new",
            ExpressionAttributeValues={":st": "RUNNING", ":new": "NEW"},
        )
    except Exception:  # nosec B110 - ConditionalCheckFailed (already terminal) is expected
        pass


def finalize_main_row(dynamo, main_table_name, workflow_execution_id, workflow_database_id,
                      workflow_id, status, stop_date, execution_log="", execution_error=None):
    """Set terminal status + stop date (+ optional log/error) on the V2 main execution row."""
    table = dynamo.Table(main_table_name)
    expr = "SET executionStopDate = :s, executionStatus = :st, lastSfnSyncCheckDate = :s"
    values = {":s": stop_date, ":st": status}
    if execution_log is not None:
        expr += ", executionLog = :lg"
        values[":lg"] = execution_log or ""
    if execution_error is not None:
        expr += ", executionError = :er"
        values[":er"] = execution_error or ""
    table.update_item(
        Key={"workflowExecutionId": workflow_execution_id,
             "workflowDatabaseId:workflowId": er.workflow_composite_key(workflow_database_id, workflow_id)},
        UpdateExpression=expr,
        ExpressionAttributeValues=values,
    )


def stop_registered_sub_process(sub, sfn_client=None, batch_client=None):
    """Best-effort stop of one registered sub-process. Dispatches on the entry's resourceType and
    returns a message describing what could NOT be stopped, or "" when nothing needs surfacing
    (stopped cleanly, already finished, no actionable locator, or no client for that type).

    Never raises: this runs on the failure/abort paths, where a stop failure must not mask the
    original outcome. A resource type with no stop API surfaces its locator so the caller can report
    what was left running."""
    resource_type = sub.get("resourceType", "") or RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION
    if resource_type == RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION:
        execution_arn = sub.get("executionArn", "")
        if not execution_arn or sfn_client is None:
            return ""
        try:
            sfn_client.stop_execution(executionArn=execution_arn)
            return ""
        except Exception as e:
            if _is_benign_stop_error(e):
                return ""
            return f"Sub-process stop failed for {execution_arn}: {_error_code(e) or e}"
    if resource_type == RESOURCE_TYPE_BATCH_JOB:
        # Only pipelines that submit their Batch job THEMSELVES register it: when a nested state
        # machine submits through the Step Functions `.sync` integration, Step Functions owns the job
        # and stopping that execution already terminates it. A self-submitted job has no such owner,
        # so without this it would keep running (and billing) after the execution is recorded failed.
        job_id = sub.get("jobId", "") or sub.get("jobArn", "")
        if not job_id or batch_client is None:
            return ""
        try:
            batch_client.terminate_job(jobId=job_id, reason="Stopped by VAMS execution failure")
            return ""
        except Exception as e:
            return f"Sub-process stop failed for Batch job {job_id}: {_error_code(e) or e}"
    locator = (sub.get("executionArn") or sub.get("jobArn") or sub.get("jobId")
               or sub.get("taskArn") or sub.get("arn") or resource_type)
    return (f"Sub-process of type '{resource_type}' ({locator}) could not be stopped: stopping this "
            "resource type is not supported; it may still be running.")


def _error_code(error):
    """The AWS error code from a botocore ClientError, or '' when the error carries none."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return (response.get("Error") or {}).get("Code", "")
    return ""


def _is_benign_stop_error(error):
    """Whether a StopExecution failure means the execution is already gone or already stopped — the
    normal race when a run finishes as it is being stopped."""
    code = _error_code(error) or type(error).__name__
    return code in ("ExecutionDoesNotExist", "ExecutionAlreadyStopped", "ExecutionLimitExceeded")


def stop_registered_sub_processes(pipeline_rows, sfn_client=None, batch_client=None):
    """Stop every sub-process registered by a non-terminal pipeline row. Returns the list of messages
    for the ones that could not be stopped, so the caller can record what was left running."""
    warnings = []
    for prow in pipeline_rows:
        if prow.get("executionStatus", "") in TERMINAL_STATUSES:
            continue
        for sub in prow.get("registeredSubExecutions", []) or []:
            message = stop_registered_sub_process(
                sub or {}, sfn_client=sfn_client, batch_client=batch_client)
            if message:
                warnings.append(message)
    return warnings


def mark_inflight_pipelines_terminal(dynamo, pipeline_executions_table, pipeline_rows,
                                     status, stop_date, sfn_client=None, batch_client=None):
    """Set every non-terminal pipeline row to `status` + stop_date (used by the error handler to fail
    in-flight pipelines), stopping each row's registered sub-processes FIRST.

    The order matters: a row stamped terminal is no longer a candidate for the abort API, so a
    sub-process left running here has no in-product remedy. Returns the messages for sub-processes
    that could not be stopped. Clients are injected (and optional) so a caller with no stop
    permissions still reconciles the rows."""
    warnings = stop_registered_sub_processes(
        pipeline_rows, sfn_client=sfn_client, batch_client=batch_client)
    for prow in pipeline_rows:
        if prow.get("executionStatus", "") in TERMINAL_STATUSES:
            continue
        set_pipeline_status(
            dynamo, pipeline_executions_table,
            prow.get("pipelineExecutionId", ""), prow.get("workflowExecutionId", ""),
            status, stop_date)
    return warnings
