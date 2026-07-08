#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.dynamodb.conditions import Attr
import json
import datetime
import uuid
import random
import string
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.resourceNames import get_table_name, ResourceKeys
from common.workflows.stepfunctions_builder import (
    create_lambda_task_state,
    create_fail_state,
    create_retry_config,
    create_catch_config,
    create_workflow_definition,
    create_state_machine,
    update_state_machine,
    create_interim_tracking_state,
    create_error_handler_state,
    get_task_builder
)
from common.s3PathPatterns import (
    PIPELINES_PREFIX,
    AUXILIARY_PREVIEW_PREFIX,
    PIPELINE_OUTPUT_PREFIX,
    PIPELINE_OUTPUT_FILES_PREFIX,
    PIPELINE_OUTPUT_PREVIEWS_PREFIX,
    PIPELINE_OUTPUT_METADATA_PREFIX,
    PIPELINE_OUTPUT_RESULTS_PREFIX,
)
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse
)
from models.workflows import CreateWorkflowRequestModel

# Set boto environment variable to use regional STS endpoint
os.environ["AWS_STS_REGIONAL_ENDPOINTS"] = 'regional'

logger = safeLogger(service="CreateWorkflow")

# Version of the generated ASL definition shape, stamped on the workflow record and the
# state machine Comment so a stale state machine can be detected later.
ASL_SCHEMA_VERSION = 1

# Claims/roles for the current request (set per-invocation in lambda_handler).
claims_and_roles = {}

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

lambda_client = boto3.client('lambda', config=retry_config)
sf_client = boto3.client('stepfunctions', config=retry_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)

try:
    workflow_Database = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE)
    stack_name = os.environ["VAMS_STACK_NAME"]
    process_workflow_output_function = os.environ['PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME']
    # Interim pipeline-tracking lambda and the error-handler lambda.
    interim_tracking_function = os.environ['INTERIM_PIPELINE_TRACKING_LAMBDA_FUNCTION_NAME']
    error_handler_function = os.environ['HANDLE_EXECUTION_ERROR_LAMBDA_FUNCTION_NAME']
    region = os.environ['AWS_REGION']
    role = os.environ['LAMBDA_ROLE_ARN']
    logGroupArn = os.environ['LOG_GROUP_ARN']
    # Deployment AWS partition for Step Functions service-integration ARNs embedded in the
    # generated ASL (arn:{partition}:states:::...). Defaults to "aws" (commercial); GovCloud/
    # China/ISO deployments inject the matching partition so the ASL is valid there.
    aws_partition = os.environ.get('AWS_PARTITION', 'aws') or 'aws'
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e


def generate_random_string(length=8):
    """Generates a random character alphanumeric string with a set input length."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))


def get_existing_workflow(database_id, workflow_id):
    """
    Check if workflow already exists in DynamoDB.

    Args:
        database_id: Database ID
        workflow_id: Workflow ID

    Returns:
        Existing workflow item or None

    Raises:
        VAMSGeneralErrorResponse: On database errors
    """
    try:
        table = dynamodb.Table(workflow_Database)
        response = table.get_item(
            Key={
                'databaseId': database_id,
                'workflowId': workflow_id
            }
        )
        return response.get('Item')
    except ClientError as e:
        logger.exception(f"Error checking existing workflow for {workflow_id}: {e}")
        raise VAMSGeneralErrorResponse("Error checking workflow existence")
    except Exception as e:
        logger.exception(f"Error checking existing workflow for {workflow_id}: {e}")
        raise VAMSGeneralErrorResponse("Error checking workflow existence")


def find_conflicting_database(database_id, workflow_id):
    """Find an active workflow with the same workflowId owned by a different database.

    Workflow IDs must be unique across all databases (including GLOBAL) because
    downstream records reference a workflow only by its workflowId, without the
    owning databaseId. Soft-deleted records (databaseId ending in '#deleted') and
    the record being created/updated (same databaseId) are not treated as conflicts.

    Args:
        database_id: The databaseId of the incoming request.
        workflow_id: The workflowId being created or updated.

    Returns:
        The conflicting databaseId string, or None if the workflowId is available.

    Raises:
        VAMSGeneralErrorResponse: On database errors.
    """
    try:
        table = dynamodb.Table(workflow_Database)
        scan_kwargs = {'FilterExpression': Attr('workflowId').eq(workflow_id)}
        while True:
            response = table.scan(**scan_kwargs)
            for item in response.get('Items', []):
                existing_database_id = item.get('databaseId', '')
                # Ignore soft-deleted records - their IDs are considered free
                if '#deleted' in existing_database_id:
                    continue
                # The record being created/updated is not a conflict with itself
                if existing_database_id == database_id:
                    continue
                return existing_database_id
            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        return None
    except ClientError as e:
        logger.exception(f"Error checking workflowId uniqueness for {workflow_id}: {e}")
        raise VAMSGeneralErrorResponse("Error checking workflow uniqueness")
    except Exception as e:
        logger.exception(f"Error checking workflowId uniqueness for {workflow_id}: {e}")
        raise VAMSGeneralErrorResponse("Error checking workflow uniqueness")


def verify_state_machine_exists(workflow_arn):
    """
    Verify if Step Functions state machine still exists.

    Args:
        workflow_arn: ARN of the state machine

    Returns:
        True if exists, False otherwise
    """
    try:
        sf_client.describe_state_machine(stateMachineArn=workflow_arn)
        logger.info(f"State machine exists: {workflow_arn}")
        return True
    except sf_client.exceptions.StateMachineDoesNotExist:
        logger.warn(f"State machine does not exist: {workflow_arn}")
        return False
    except Exception as e:
        logger.exception(f"Error verifying state machine existence for {workflow_arn}: {e}")
        return False


def generate_workflow_asl(pipelines, databaseId, workflowId):
    """
    Generate the ASL workflow definition for a workflow.
    Uses the builder pattern from stepfunctions_builder to dispatch
    Lambda, SQS, and EventBridge task states.

    Args:
        pipelines: List of pipeline configurations
        databaseId: Database ID for the workflow
        workflowId: Workflow ID

    Returns:
        Tuple of (workflow_definition dict, job_names list)
    """
    logger.info("Generating workflow ASL definition")

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

    global_output_s3_asset_files_uri = f"States.Format('s3://{{}}/{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_FILES_PREFIX}', $.workflowExecutionS3InputOutputBucket, $$.Execution.Name)"
    global_output_s3_asset_preview_uri = f"States.Format('s3://{{}}/{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_PREVIEWS_PREFIX}', $.workflowExecutionS3InputOutputBucket, $$.Execution.Name)"
    global_output_s3_asset_metadata_uri = f"States.Format('s3://{{}}/{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_METADATA_PREFIX}', $.workflowExecutionS3InputOutputBucket, $$.Execution.Name)"
    global_output_s3_asset_results_uri = f"States.Format('s3://{{}}/{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_RESULTS_PREFIX}', $.workflowExecutionS3InputOutputBucket, $$.Execution.Name)"

    # Asset-bucket-relative output FILES prefix (no s3:// scheme) for the interim output diff.
    output_files_prefix_template = f"{PIPELINES_PREFIX}{first_pipeline_name}/{first_job_name}{PIPELINE_OUTPUT_PREFIX}{{}}{PIPELINE_OUTPUT_FILES_PREFIX}"
    output_files_prefix_uri = f"States.Format('{output_files_prefix_template}', $$.Execution.Name)"

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

        # Determine execution type (default to Lambda for backwards compat)
        exec_type = pipeline.get('pipelineExecutionType', 'Lambda')

        # Build path context for the builder. The pipeline reads its resolved inputs/outputs from
        # the manifest; only the manifest + per-pipeline config S3 locations travel in the body
        # (asset bucket execution input folder; 1-indexed pipeline folders).
        path_context = {
            "inputManifestS3Location": _pipeline_input_uri(i + 1, "manifest.json"),
            "inputConfigurationS3Location": _pipeline_input_uri(i + 1, "config.json"),
        }

        # Get the appropriate builder (partition-aware service-integration ARNs)
        builder = get_task_builder(exec_type, partition=aws_partition)

        # Build payload using the builder (shared payload construction)
        payload = builder.build_payload(pipeline, path_context)

        # Apply callback (adds TaskToken if enabled)
        payload = builder.apply_callback(payload, pipeline)

        # Generate state name
        state_name = (uuid.uuid1().hex[:5] + "-" + pipeline['name'])[:80]

        # Build the task state using the builder
        # EventBridge: payload is placed directly as the Detail object in Entries,
        # Step Functions serializes it automatically. No Pass state needed.
        task_state = builder.build_task_state(pipeline, state_name, payload)

        # Re-point the Catch to the error-handler state (caught error at $.errorInfo).
        task_state["Catch"] = [create_catch_config(
            error_equals=["States.ALL"], next_state=error_handler_state_id,
            result_path="$.errorInfo")]

        states.append((state_name, task_state))

        # Insert an interim-tracking state between this pipeline and the next.
        if i < len(pipelines) - 1:
            interim_state_id = f"interim-{i + 1}-{uuid.uuid1().hex[:8]}"
            # Aux working prefix for the NEXT pipeline.
            next_pipeline = pipelines[i + 1]
            next_aux_subfolder = PIPELINES_PREFIX.rstrip('/')
            if next_pipeline.get('pipelineType', 'standardFile') == 'previewFile':
                next_aux_subfolder = AUXILIARY_PREVIEW_PREFIX.rstrip('/')
            next_aux_prefix_uri = (
                f"States.Format('{{}}/{next_aux_subfolder}/{next_pipeline['name']}/', "
                f"$.inputAssetFileKey)")
            interim_payload = {
                "body": {
                    # --- Workflow-execution identity + buckets ---
                    "workflowExecutionId.$": "$.workflowExecutionId",
                    "workflowExecutionS3InputOutputBucket.$": "$.workflowExecutionS3InputOutputBucket",
                    "bucketAssetAuxiliary.$": "$.bucketAssetAuxiliary",

                    # --- Just-finished pipeline: output diff (list its output files, attribute,
                    #     and record them). outputFilesPrefix is the asset-bucket-RELATIVE listing
                    #     prefix (vs. outputFilesUri below, the full s3:// URI for the next manifest).
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
                    "nextPipelineAuxPrefix.$": next_aux_prefix_uri,

                    # --- Envelope context written into the NEXT pipeline's manifest ---
                    "outputFilesUri.$": global_output_s3_asset_files_uri,
                    "outputPreviewsUri.$": global_output_s3_asset_preview_uri,
                    "outputMetadataUri.$": global_output_s3_asset_metadata_uri,
                    "outputResultsUri.$": global_output_s3_asset_results_uri,
                    "inputMetadataS3Location.$": input_metadata_uri,
                    "outputLocationType.$": "$.outputLocationType",
                    "outputAssetId.$": "$.outputAssetId",
                    "outputDatabaseId.$": "$.outputDatabaseId",
                    "outputFileBaseExecutionPathExtension.$": "$.outputFileBaseExecutionPathExtension",

                    # --- Orchestration (next pipeline's event prefix is built from these) ---
                    "orchestrationBusArn.$": "$.orchestrationBusArn",
                    "orchestrationEventSourcePrefix.$": "$.orchestrationEventSourcePrefix",
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
    po_retry_config = create_retry_config(
        error_equals=["States.ALL"],
        interval_seconds=5,
        backoff_rate=2.0,
        max_attempts=3
    )
    po_catch_config = [create_catch_config(
        error_equals=["States.ALL"],
        next_state=error_handler_state_id,
        result_path="$.errorInfo",
    )]

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


def create_step_function_new(pipelines, databaseId, workflowId):
    """
    Create a NEW Step Functions state machine.

    Args:
        pipelines: List of pipeline configurations
        databaseId: Database ID for the workflow
        workflowId: Workflow ID

    Returns:
        Tuple of (ARN of the created state machine, job_names baked into the ASL)

    Raises:
        VAMSGeneralErrorResponse: On errors
    """
    logger.info(f"Creating NEW state machine for workflow: {workflowId}")

    try:
        # Generate workflow definition
        workflow_definition, job_names = generate_workflow_asl(pipelines, databaseId, workflowId)

        # Generate unique name for the Step Functions Workflow
        # Workflow name must have 'vams' in it for permissions
        # Make sure workFlowName is not longer than 80 characters
        workFlowName = workflowId
        if len(workFlowName) > 66:
            workFlowName = workFlowName[-66:]
        workFlowName = workFlowName + generate_random_string(8)
        workFlowName = "vams-" + workFlowName
        if len(workFlowName) > 80:
            workFlowName = workFlowName[-79:]

        logger.info(f"Creating state machine with name: {workFlowName}")

        # Create the state machine
        workflow_arn = create_state_machine(
            sf_client=sf_client,
            name=workFlowName,
            definition=workflow_definition,
            role_arn=role,
            log_group_arn=logGroupArn,
            state_machine_type='STANDARD'
        )

        logger.info(f"State machine created successfully: {workflow_arn}")
        return workflow_arn, job_names

    except Exception as e:
        logger.exception(f"Error creating state machine for workflow {workflowId}: {e}")
        raise VAMSGeneralErrorResponse("Error creating workflow state machine")


def update_step_function_existing(existing_arn, pipelines, databaseId, workflowId):
    """
    Update an EXISTING Step Functions state machine.

    Args:
        existing_arn: ARN of existing state machine
        pipelines: List of pipeline configurations
        databaseId: Database ID for the workflow
        workflowId: Workflow ID

    Returns:
        Tuple of (ARN of the updated state machine (same as input), job_names baked into the ASL)

    Raises:
        VAMSGeneralErrorResponse: On errors
    """
    logger.info(f"Updating EXISTING state machine: {existing_arn}")

    try:
        # Generate workflow definition (same logic as create)
        workflow_definition, job_names = generate_workflow_asl(pipelines, databaseId, workflowId)

        # Update the existing state machine
        update_state_machine(
            sf_client=sf_client,
            state_machine_arn=existing_arn,
            definition=workflow_definition,
            role_arn=role,
            log_group_arn=logGroupArn
        )

        logger.info(f"State machine updated successfully: {existing_arn}")
        return existing_arn, job_names

    except Exception as e:
        logger.exception(f"Error updating state machine {existing_arn}: {e}")
        raise VAMSGeneralErrorResponse("Error updating workflow state machine")


def create_workflow(payload, claims_and_roles):
    """
    Create or update a workflow.

    Handles three scenarios:
    1. New workflow - Creates new state machine and DynamoDB record
    2. Update existing - Updates state machine definition (preserves execution history)
    3. Orphaned record - Creates new state machine if old one was deleted

    Args:
        payload: Workflow creation/update payload
        claims_and_roles: User claims and roles for authorization

    Returns:
        JSON success message

    Raises:
        VAMSGeneralErrorResponse: On errors
    """
    database_id = payload['databaseId']
    workflow_id = payload['workflowId']
    pipelines = payload['specifiedPipelines']['functions']

    # Check if workflow already exists in DynamoDB
    existing_workflow = get_existing_workflow(database_id, workflow_id)

    workflow_arn = None
    is_update = False
    job_names = []

    if existing_workflow and 'workflow_arn' in existing_workflow:
        # Workflow exists - check if state machine still exists
        existing_arn = existing_workflow['workflow_arn']
        logger.info(f"Found existing workflow with ARN: {existing_arn}")

        if verify_state_machine_exists(existing_arn):
            # UPDATE existing state machine (preserves execution history)
            logger.info(f"Updating existing workflow: {workflow_id}")
            workflow_arn, job_names = update_step_function_existing(
                existing_arn,
                pipelines,
                database_id,
                workflow_id
            )
            is_update = True
        else:
            # State machine was deleted - CREATE new one
            logger.info(f"State machine {existing_arn} not found, creating new one")
            workflow_arn, job_names = create_step_function_new(pipelines, database_id, workflow_id)
    else:
        # New workflow - CREATE
        logger.info(f"Creating new workflow: {workflow_id}")
        workflow_arn, job_names = create_step_function_new(pipelines, database_id, workflow_id)

    # Update DynamoDB record
    try:
        table = dynamodb.Table(workflow_Database)
        dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')

        # Get username from claims_and_roles tokens array
        username = claims_and_roles["tokens"][0] if len(claims_and_roles.get("tokens", [])) > 0 else "SYSTEM_USER"

        Item = {
            'databaseId': database_id,
            'workflowId': workflow_id,
            'description': payload['description'],
            'specifiedPipelines': payload['specifiedPipelines'],
            'workflow_arn': workflow_arn,
            'dateModified': json.dumps(dtNow),
            'modifiedBy': username,
            # Schema version of the deployed state machine definition (also in the ASL Comment).
            'aslSchemaVersion': ASL_SCHEMA_VERSION,
            # Per-pipeline job names baked into the ASL output S3 paths; executeWorkflow reads
            # jobNames[0] at launch so the manifest's output locations match the ASL's.
            'jobNames': job_names,
        }

        # Add autoTriggerOnFileExtensionsUpload if provided
        auto_trigger = payload.get('autoTriggerOnFileExtensionsUpload', '')
        if auto_trigger:
            Item['autoTriggerOnFileExtensionsUpload'] = auto_trigger
        else:
            Item['autoTriggerOnFileExtensionsUpload'] = ''

        # Preserve dateCreated for updates
        if existing_workflow:
            Item['dateCreated'] = existing_workflow.get('dateCreated', json.dumps(dtNow))
        else:
            Item['dateCreated'] = json.dumps(dtNow)

        # On create, guard against a concurrent create of the same (databaseId, workflowId)
        # racing between the uniqueness check and this write. This closes the same-key
        # clobber window; cross-database workflowId uniqueness (a non-key attribute) is
        # still enforced by find_conflicting_database above.
        if is_update:
            table.put_item(Item=Item)
        else:
            try:
                table.put_item(
                    Item=Item,
                    ConditionExpression='attribute_not_exists(databaseId) AND attribute_not_exists(workflowId)'
                )
            except ClientError as e:
                if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    logger.warning(f"Concurrent create detected for workflowId {workflow_id} in database {database_id}")
                    raise VAMSGeneralErrorResponse("Workflow ID is already in use. Choose a different ID.")
                raise

        action = "updated" if is_update else "created"
        logger.info(f"Workflow {action} by {username}: {workflow_id}")

        return json.dumps({"message": 'Succeeded'})

    except ClientError as e:
        logger.exception(f"Error saving workflow {workflow_id} to DynamoDB: {e}")
        raise VAMSGeneralErrorResponse("Error saving workflow")
    except Exception as e:
        logger.exception(f"Error saving workflow {workflow_id} to DynamoDB: {e}")
        raise VAMSGeneralErrorResponse("Error saving workflow")


def parse_request_body(event):
    """Parse the JSON request body. Returns (body_dict, error_response); the error
    response is None on success, otherwise a 400 for a missing or malformed body."""
    if not event.get('body'):
        return None, validation_error(body={'message': 'Request body is required'}, event=event)

    body = event['body']
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            return None, validation_error(body={'message': 'Invalid JSON in request body'}, event=event)

    return body, None


def validate_pipeline_required_fields(event, pipelines):
    """Ensure each pipeline entry carries every required field. Returns an error
    response for the first incomplete entry, or None when all entries are complete."""
    pipeline_required_fields = ['name', 'databaseId', 'pipelineType', 'pipelineExecutionType',
                                'outputType', 'waitForCallback', 'userProvidedResource']
    for idx, pipeline in enumerate(pipelines):
        missing_pipeline_fields = [f for f in pipeline_required_fields if f not in pipeline or not pipeline[f]]
        if missing_pipeline_fields:
            message = f"Pipeline entry {idx} is missing required field(s): {', '.join(missing_pipeline_fields)}"
            return validation_error(body={'message': message}, event=event)
    return None


def authorize_pipelines(event, database_id, pipelines):
    """Validate each pipeline's database scope and Tier-2 GET authorization. Returns
    an error response on the first failure, or None when every pipeline is in scope
    and accessible.

    Annotates each pipeline dict in place (object__type / databaseId / pipelineId /
    type fields); those mutated entries are persisted with the workflow record."""
    for pipeline in pipelines:
        logger.info("pipeline in workflow creation: ")
        logger.info(pipeline)
        # If global workflow, included pipeline should also be global
        if database_id == "GLOBAL":
            if pipeline['databaseId'] != "GLOBAL":
                return validation_error(
                    body={'message': 'Only global pipelines are allowed in global workflows.'},
                    event=event
                )
        else:
            if pipeline['databaseId'] != "GLOBAL" and database_id != pipeline['databaseId']:
                return validation_error(
                    body={'message': 'Only global or same database pipelines are allowed in a database specifc workflows.'},
                    event=event
                )
        # Add Casbin Enforcer to check if the current user has permissions to GET the pipeline (Tier 2):
        pipeline_allowed = False
        pipeline.update({
            "object__type": "pipeline",
            "databaseId": database_id,
            "pipelineId": pipeline['name'],
            "pipelineType": pipeline['pipelineType'],
            "pipelineExecutionType": pipeline['pipelineExecutionType'],
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(pipeline, "GET"):
                pipeline_allowed = True

        if not pipeline_allowed:
            return authorization_error(body={'message': 'Not Authorized to read the pipeline'})
    return None


def authorize_workflow(database_id, workflow_id):
    """Tier-2 PUT authorization on the workflow object. Returns True if allowed."""
    logger.info("Validating workflow authorization")
    # Add Casbin Enforcer to check if the current user has permissions to PUT the workflow (Tier 2):
    workflow = {
        "object__type": "workflow",
        'databaseId': database_id,
        'workflowId': workflow_id,
    }
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(workflow, "PUT"):
            return True
    return False


def check_workflow_id_uniqueness(event, database_id, workflow_id):
    """Reject a workflowId already owned by a different (non-deleted) database.
    Returns an error response on conflict, or None when the id is available."""
    conflicting_database_id = find_conflicting_database(database_id, workflow_id)
    if conflicting_database_id:
        logger.info(
            f"workflowId '{workflow_id}' already in use by database '{conflicting_database_id}'"
        )
        return validation_error(
            body={
                'message': "Workflow ID is already in use by another database. Workflow IDs must be "
                           "unique across all databases (including GLOBAL). Choose a different ID."
            },
            event=event
        )
    return None


def handle_create_request(event):
    """Parse + validate the body, authorize the pipelines and workflow, enforce
    workflowId uniqueness, then create/update the workflow and its state machine."""
    body, error = parse_request_body(event)
    if error:
        return error

    # Validate required fields via Pydantic model
    try:
        parse(body, model=CreateWorkflowRequestModel)
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)

    pipelines = body['specifiedPipelines']['functions']

    # Validate required fields in each pipeline entry before proceeding
    error = validate_pipeline_required_fields(event, pipelines)
    if error:
        return error

    # Pipeline database-scope + Tier-2 GET authorization
    error = authorize_pipelines(event, body['databaseId'], pipelines)
    if error:
        return error

    # Tier-2 PUT authorization on the workflow
    if not authorize_workflow(body['databaseId'], body['workflowId']):
        return authorization_error()

    # Enforce cross-database uniqueness of the workflowId
    error = check_workflow_id_uniqueness(event, body['databaseId'], body['workflowId'])
    if error:
        return error

    result = create_workflow(body, claims_and_roles)
    return success(body=json.loads(result))


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for the create/update-workflow API.

    Reached two ways with identical behavior: PUT /workflows from API Gateway, and a
    POST lambda-to-lambda invocation from importGlobalPipelineWorkflow. Both create or
    update a workflow, so PUT and POST dispatch to the same create handler."""
    global claims_and_roles
    logger.info(event)
    claims_and_roles = request_to_claims(event)

    try:
        method = event['requestContext']['http']['method']

        # Check if method is allowed on API (Tier 1)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if method in ('PUT', 'POST'):
            return handle_create_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)

    except VAMSGeneralErrorResponse as v:
        logger.exception("VAMS error in workflow creation.")
        return general_error(body={'message': str(v)}, event=event)
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] in ('LimitExceededException', 'ThrottlingException'):
            logger.exception("Throttling Error")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ThrottlingException: Too many requests within a given period.'},
                event=event
            )
        else:
            logger.exception("AWS Client Error")
            return internal_error(event=event)
    except KeyError as e:
        logger.exception(f"Missing required field in workflow creation: {e}")
        return validation_error(body={'message': f'Missing required field: {e}'}, event=event)
    except Exception as e:
        logger.exception("Internal error in workflow creation")
        return internal_error(event=event)
