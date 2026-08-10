# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Builds a workflow's Amazon States Language definition from its ordered pipeline list.

A pure function of (pipelines, workflowId) plus four deployment-specific values the caller passes
explicitly (rather than reading module globals): the process-output / interim-tracking /
error-handler Lambda function names and the AWS partition. Has no AWS or env dependency at import —
only the side-effect-free stepfunctions_builder + s3PathPatterns imports.
"""

import uuid

from customLogging.logger import safeLogger
from common.workflows.stepfunctions_builder import (
    create_lambda_task_state,
    create_fail_state,
    create_retry_config,
    create_catch_all_configs,
    create_workflow_definition,
    create_interim_tracking_state,
    create_error_handler_state,
    get_task_builder,
)
from common.s3PathPatterns import (
    PIPELINES_PREFIX,
    PIPELINE_OUTPUT_PREFIX,
    PIPELINE_OUTPUT_FILES_PREFIX,
    PIPELINE_OUTPUT_PREVIEWS_PREFIX,
    PIPELINE_OUTPUT_METADATA_PREFIX,
    PIPELINE_OUTPUT_RESULTS_PREFIX,
)

logger = safeLogger(service_name="WorkflowAslBuilder")

# ASL schema version stamped in the definition Comment field.
ASL_SCHEMA_VERSION = 1

# Characters that cannot appear in a pipeline name spliced into a States.Format() intrinsic
# argument: the single quote closes the intrinsic's string literal, the braces are its
# placeholder syntax, and the backslash is its escape character. A name carrying any of them
# produces an intrinsic Step Functions rejects when the definition is created.
_ASL_INTRINSIC_UNSAFE_CHARS = "'{}\\"


def _validate_asl_pipeline_name(name):
    """Reject a pipeline name that cannot be embedded in the generated ASL's States.Format
    intrinsics (its output-path templates). Raises ValueError, which the workflow save path
    surfaces as a validation error rather than a Step Functions definition failure."""
    if not name:
        raise ValueError("Each workflow pipeline requires a job name or pipeline id")
    if any(c in name for c in _ASL_INTRINSIC_UNSAFE_CHARS) or any(ord(c) < 0x20 for c in name):
        logger.warning(f"Pipeline job name unusable in ASL output paths: {name}")
        raise ValueError(
            "Pipeline job names cannot contain quotes, braces, backslashes, or control "
            "characters. Use letters, numbers, dashes, and underscores.")


def generate_workflow_asl(pipelines, databaseId, workflowId,
                          process_workflow_output_function,
                          interim_tracking_function,
                          error_handler_function,
                          aws_partition="aws"):
    """
    Generate the ASL workflow definition for a workflow.
    Uses the builder pattern from stepfunctions_builder to dispatch
    Lambda, SQS, EventBridge, and DeadlineCloud task states.

    Args:
        pipelines: List of pipeline configurations (V1-shaped dicts: name, pipelineExecutionType,
            waitForCallback, userProvidedResource, taskTimeout, ...)
        databaseId: Database ID for the workflow
        workflowId: Workflow ID
        process_workflow_output_function: name of the end-state process-output Lambda
        interim_tracking_function: name of the interim pipeline-tracking Lambda
        error_handler_function: name of the execution error-handler Lambda
        aws_partition: deployment AWS partition for service-integration ARNs (default "aws")

    Returns:
        Tuple of (workflow_definition dict, job_names list)
    """
    logger.info("Generating workflow ASL definition")

    # Each pipeline name becomes a path segment inside the ASL's States.Format intrinsics, so it is
    # checked before any template is built.
    for pipeline in pipelines:
        _validate_asl_pipeline_name(pipeline.get('name'))

    # Generate unique names for each pipeline job
    # Trim UID to first 5 chars, place in front of pipeline name, then trim to 80 chars
    job_names = [
        (uuid.uuid1().hex[:5] + "-" + x['name'])[:80] for x in pipelines
    ]
    logger.info(f"Generated job names: {job_names}")

    # Create failure state
    failed_state_id = "WorkflowProcessingJobFailed"
    failed_state = create_fail_state(
        state_id=failed_state_id,
        cause="WorkflowProcessingJobFailed",
        error="States.TaskFailed"
    )

    # Error-handler state: every Catch routes here (error at $.errorInfo) to reconcile the
    # tables to FAILED, then transitions to the Fail state.
    error_handler_state_id = "HandleExecutionError"
    error_handler_payload = {
        "body": {
            "workflowExecutionId.$": "$.workflowExecutionId",
            "workflowDatabaseId.$": "$.workflowDatabaseId",
            "workflowId.$": "$.workflowId",
        },
        "errorInfo.$": "$.errorInfo",
    }
    error_handler_state = create_error_handler_state(
        state_id=error_handler_state_id,
        function_name=error_handler_function,
        payload=error_handler_payload,
        fail_state=failed_state_id,
        partition=aws_partition,
    )

    # Generate GLOBAL output paths (shared by ALL pipelines)
    # Use the FIRST pipeline's name for the global output location
    first_pipeline_name = pipelines[0]['name']
    first_job_name = job_names[0]

    # Asset-bucket-RELATIVE output prefixes (no s3:// scheme, no bucket) for each output kind. The
    # next pipeline's manifest carries these plus the output bucket separately; downstream
    # reconstructs s3://{bucket}/{prefix} as needed. The output bucket is the workflow-execution
    # I/O bucket ($.workflowExecutionS3InputOutputBucket), threaded to the interim lambda.
    output_files_prefix_template = f"{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_FILES_PREFIX}"
    output_previews_prefix_template = f"{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_PREVIEWS_PREFIX}"
    output_metadata_prefix_template = f"{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_METADATA_PREFIX}"
    output_results_prefix_template = f"{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_RESULTS_PREFIX}"
    output_files_prefix_uri = f"States.Format('{output_files_prefix_template}', $$.Execution.Name)"
    output_previews_prefix_uri = f"States.Format('{output_previews_prefix_template}', $$.Execution.Name)"
    output_metadata_prefix_uri = f"States.Format('{output_metadata_prefix_template}', $$.Execution.Name)"
    output_results_prefix_uri = f"States.Format('{output_results_prefix_template}', $$.Execution.Name)"

    # Per-execution input-definition folder (asset bucket), keyed only on the execution id so
    # it matches er.execution_input_prefix(executionId) and the keys executeWorkflow writes.
    input_folder_template = f"{PIPELINES_PREFIX}workflowExecutionInputs/{{}}/"

    def _pipeline_input_uri(pipeline_index, filename):
        return (f"States.Format('s3://{{}}/{input_folder_template}pipeline{pipeline_index}/{filename}', "
                f"$.workflowExecutionS3InputOutputBucket, $$.Execution.Name)")

    input_metadata_uri = (f"States.Format('s3://{{}}/{input_folder_template}metadata.json', "
                          f"$.workflowExecutionS3InputOutputBucket, $$.Execution.Name)")

    # Build list of pipeline states, inserting an interim-tracking state between each pair
    states = []

    for i, pipeline in enumerate(pipelines):
        logger.info(f"Processing pipeline {i}: {pipeline['name']}")

        # Determine execution type (default to Lambda)
        exec_type = pipeline.get('pipelineExecutionType', 'Lambda')

        # Build path context for the builder. The pipeline reads its resolved inputs/outputs from
        # the manifest; only the manifest + per-pipeline config S3 locations travel in the body
        # (asset bucket execution input folder; 1-indexed pipeline folders). The step's
        # pipelineExecutionId reference is supplied for builders whose completion callback
        # must locate the pipeline-execution row (DeadlineCloud); other builders ignore it.
        path_context = {
            "inputManifestS3Location": _pipeline_input_uri(i + 1, "manifest.json"),
            "inputConfigurationS3Location": _pipeline_input_uri(i + 1, "config.json"),
            "pipelineExecutionIdRef": f"$.pipelineExecutionIds[{i}]",
        }

        # Get the appropriate builder (partition-aware service-integration ARNs)
        builder = get_task_builder(exec_type, partition=aws_partition)

        # Build payload using the builder (shared payload construction)
        payload = builder.build_payload(pipeline, path_context)

        # Apply callback (adds TaskToken if enabled)
        payload = builder.apply_callback(payload, pipeline)

        # State name. The step position leads so it survives the 80-char trim, making the name
        # unique within the definition even when two steps carry the same job name — states are
        # keyed by name, so a repeated name would drop a step from the deployed workflow.
        state_name = f"step{i + 1}-{uuid.uuid1().hex[:5]}-{pipeline['name']}"[:80]

        # Build the task state using the builder
        # EventBridge: payload is placed directly as the Detail object in Entries,
        # Step Functions serializes it automatically. No Pass state needed.
        task_state = builder.build_task_state(pipeline, state_name, payload)

        # Re-point the Catch to the error-handler state (caught error at $.errorInfo).
        task_state["Catch"] = create_catch_all_configs(
            next_state=error_handler_state_id, result_path="$.errorInfo")

        states.append((state_name, task_state))

        # Insert an interim-tracking state between this pipeline and the next.
        if i < len(pipelines) - 1:
            interim_state_id = f"interim-{i + 1}-{uuid.uuid1().hex[:8]}"
            # Bucket-relative, execution-scoped aux temp working prefix for the NEXT pipeline
            # (pipelines/{nextName}/{executionId}/). The next pipeline's per-input-file aux preview
            # prefix is built inside the interim lambda from the input rows, not here.
            next_pipeline = pipelines[i + 1]
            next_aux_temp_prefix_uri = (
                f"States.Format('{PIPELINES_PREFIX}{next_pipeline['name']}/{{}}/', "
                f"$$.Execution.Name)")
            interim_payload = {
                "body": {
                    # --- Workflow-execution identity + I/O bucket (the aux bucket is resolved by
                    #     the interim lambda itself, not threaded through the SFN input) ---
                    "workflowExecutionId.$": "$.workflowExecutionId",
                    "workflowExecutionS3InputOutputBucket.$": "$.workflowExecutionS3InputOutputBucket",

                    # --- Just-finished pipeline: output diff (list its output files, attribute,
                    #     and record them). outputFilesPrefix is the asset-bucket-RELATIVE listing
                    #     prefix, reused below as the next manifest's relative output FILES prefix.
                    "fromPipelineExecutionId.$": f"$.pipelineExecutionIds[{i}]",
                    "priorPipelineExecutionIds.$": "$.pipelineExecutionIds",
                    "outputFilesPrefix.$": output_files_prefix_uri,

                    # --- Next pipeline: where to write its manifest + config, its id and aux prefix ---
                    "nextPipelineExecutionId.$": f"$.pipelineExecutionIds[{i + 1}]",
                    "nextPipelineManifestS3Key.$": (
                        f"States.Format('{input_folder_template}pipeline{i + 2}/manifest.json', "
                        f"$$.Execution.Name)"),
                    "nextPipelineConfigS3Key.$": (
                        f"States.Format('{input_folder_template}pipeline{i + 2}/config.json', "
                        f"$$.Execution.Name)"),
                    "nextPipelineAuxTempPrefix.$": next_aux_temp_prefix_uri,
                    # The NEXT step's own narrowed input-metadata key, or "" when that step reads the
                    # shared per-execution envelope. Resolved per execution (templates are chosen at
                    # execute time, the ASL is baked at save time), so only the index is static here.
                    "nextPipelineMetadataS3Key.$": f"$.stepMetadataS3Keys[{i + 1}]",
                    # The NEXT step's effective input-file filters and arity, so the interim step
                    # narrows its manifest to what that step accepts instead of handing it inputs its
                    # own gate rejects. Indexed by static position for the same reason as the metadata
                    # key above.
                    "nextPipelineInputFileFilters.$": f"$.stepInputFilters[{i + 1}]",
                    "nextPipelineInputFileArity.$": f"$.stepInputArity[{i + 1}]",
                    # Next pipeline identity for template-tag rendering of its input configuration
                    # (pipeline id + database id + job name are known at ASL-build time; the
                    # workflow ids and executing-user context come from the SFN input). The
                    # pipeline id — not the jobName-derived path name — is threaded so the
                    # {{pipelineId}}/{{pipelineName}} tags render the same value the execute
                    # handler supplies for the first pipeline.
                    "nextPipelineId": next_pipeline.get('pipelineId') or next_pipeline['name'],
                    "nextPipelineDatabaseId": next_pipeline.get('databaseId', ''),
                    "nextPipelineJobName": job_names[i + 1],
                    "workflowId.$": "$.workflowId",
                    "workflowDatabaseId.$": "$.workflowDatabaseId",
                    "executingUserName.$": "$.executingUserName",

                    # --- Envelope context written into the NEXT pipeline's manifest. Output
                    #     prefixes are asset-bucket-RELATIVE (no s3://); the output bucket is the
                    #     workflow-execution I/O bucket, threaded separately. ---
                    "outputFilesPrefixRelative.$": output_files_prefix_uri,
                    "outputPreviewsPrefixRelative.$": output_previews_prefix_uri,
                    "outputMetadataPrefixRelative.$": output_metadata_prefix_uri,
                    "outputResultsPrefixRelative.$": output_results_prefix_uri,
                    "inputMetadataS3Location.$": input_metadata_uri,
                    "outputLocationType.$": "$.outputLocationType",
                    "outputAssetId.$": "$.outputAssetId",
                    "outputDatabaseId.$": "$.outputDatabaseId",
                    "outputFileBaseExecutionPathExtension.$": "$.outputFileBaseExecutionPathExtension",
                },
            }
            interim_state = create_interim_tracking_state(
                state_id=interim_state_id,
                function_name=interim_tracking_function,
                payload=interim_payload,
                result_path=f"$.{interim_state_id}.output",
                error_handler_state=error_handler_state_id,
                partition=aws_partition,
            )
            states.append((interim_state_id, interim_state))

    # Create SINGLE process output state (runs ONCE after ALL pipelines complete)
    # Use the LAST pipeline's information for the process output
    last_pipeline = pipelines[-1]
    last_job_name = job_names[-1]

    process_output_state_id = f"process-outputs-{uuid.uuid1().hex}"
    process_output_payload = {
        "body": {
            # --- Workflow-execution identity ---
            "workflowExecutionId.$": "$.workflowExecutionId",
            "workflowDatabaseId.$": "$.workflowDatabaseId",
            "workflowId.$": "$.workflowId",
            "endStatePipelineExecutionId.$": "$.endStatePipelineExecutionId",
            # All pipeline-execution ids for the end-state output diff baseline.
            "priorPipelineExecutionIds.$": "$.pipelineExecutionIds",
            "pipeline": last_pipeline['name'],
            "description": f'Output from {last_job_name}',

            # --- Shared output-folder prefixes the end-state lambda lists for produced files ---
            "filesPathKey.$": f"States.Format('{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_FILES_PREFIX}', $$.Execution.Name)",
            "metadataPathKey.$": f"States.Format('{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_METADATA_PREFIX}', $$.Execution.Name)",
            "previewPathKey.$": f"States.Format('{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_PREVIEWS_PREFIX}', $$.Execution.Name)",
            "resultsPathKey.$": f"States.Format('{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_RESULTS_PREFIX}', $$.Execution.Name)",

            # --- Run I/O bucket where the pipelines actually STAGED those output prefixes (the VAMS
            #     default asset bucket, threaded by the execute handler). The end-state lambda LISTS
            #     produced files from this bucket, then writes them back to the output asset's own
            #     bucket. Without this it would list from the output asset's bucket and find nothing
            #     when the run bucket differs (multi-bucket deployments).
            "workflowExecutionS3InputOutputBucket.$": "$.workflowExecutionS3InputOutputBucket",

            # --- Output target identity (where outputs are written; == the input asset today). The
            #     end-state lambda writes outputs to this asset; it does not receive the input asset.
            "outputLocationType.$": "$.outputLocationType",
            "outputAssetId.$": "$.outputAssetId",
            "outputDatabaseId.$": "$.outputDatabaseId",
            "outputFileBaseExecutionPathExtension.$": "$.outputFileBaseExecutionPathExtension",

            # --- Executing-user context ---
            "executingUserName.$": "$.executingUserName",
            "executingRequestContext.$": "$.executingRequestContext",
        }
    }

    # Create retry and catch configs for process output (Catch routes through the
    # error-handler state, capturing the error at $.errorInfo).
    # Retry only transient AWS/Lambda faults. This handler ingests produced files into the output
    # asset before it records them, so a blanket States.ALL retry of an application error would
    # re-run ingestion and duplicate files/versions; such errors go straight to the Catch instead.
    po_retry_config = create_retry_config(
        error_equals=[
            "Lambda.ServiceException",
            "Lambda.AWSLambdaException",
            "Lambda.SdkClientException",
            "Lambda.TooManyRequestsException",
            "States.Timeout",
        ],
        interval_seconds=5,
        backoff_rate=2.0,
        max_attempts=3
    )
    po_catch_config = create_catch_all_configs(
        next_state=error_handler_state_id, result_path="$.errorInfo")

    process_output_state = create_lambda_task_state(
        state_id=process_output_state_id,
        function_name=process_workflow_output_function,
        payload=process_output_payload,
        result_path=f"$.{process_output_state_id}.output",
        retry_config=po_retry_config,
        catch_config=po_catch_config,
        partition=aws_partition
    )

    # Add the single process_output state to the states list
    states.append((process_output_state_id, process_output_state))

    # Create the complete workflow definition (without fail state in sequential flow)
    workflow_definition = create_workflow_definition(
        states=states,
        comment=f"VAMS Pipeline Workflow for {workflowId} | aslSchemaVersion={ASL_SCHEMA_VERSION}"
    )

    # Add the error-handler + failure states to the States dict (reachable only via Catch handlers)
    workflow_definition["States"][error_handler_state_id] = error_handler_state
    workflow_definition["States"][failed_state_id] = failed_state

    return workflow_definition, job_names
