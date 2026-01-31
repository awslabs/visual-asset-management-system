#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
from botocore.exceptions import ClientError
from botocore.config import Config
import json
import datetime
import uuid
import random
import string
from typing import List, Optional
from common.validators import validate
from common.constants import STANDARD_JSON_RESPONSE
from common.stepfunctions_builder import (
    create_lambda_task_state,
    create_fail_state,
    create_retry_config,
    create_catch_config,
    create_workflow_definition,
    create_state_machine,
    update_state_machine
)
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import VAMSGeneralErrorResponse

# Set boto environment variable to use regional STS endpoint
os.environ["AWS_STS_REGIONAL_ENDPOINTS"] = 'regional'

logger = safeLogger(service="CreateWorkflow")

main_rest_response = STANDARD_JSON_RESPONSE

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
    workflow_Database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    stack_name = os.environ["VAMS_STACK_NAME"]
    process_workflow_output_function = os.environ['PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME']
    region = os.environ['AWS_REGION']
    role = os.environ['LAMBDA_ROLE_ARN']
    logGroupArn = os.environ['LOG_GROUP_ARN']
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


def generate_random_string(length=8):
    """Generates a random character alphanumeric string with a set input length."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))


def validate_and_parse_extensions(auto_trigger_extensions: Optional[str]) -> Optional[List[str]]:
    """
    Validate and parse comma-delimited extension list
    
    Args:
        auto_trigger_extensions: Comma-delimited string of extensions
    
    Returns:
        List of normalized extensions, or None if invalid/empty
    """
    # Skip if field is None, empty, or whitespace
    if not auto_trigger_extensions or not auto_trigger_extensions.strip():
        return None
    
    # Check for "all" trigger - return special marker
    trigger_value = auto_trigger_extensions.strip().lower()
    if trigger_value in ['.all', 'all']:
        return ['__ALL__']  # Special marker for "all files"
    
    # Parse comma-delimited extensions
    extensions = []
    for ext in auto_trigger_extensions.split(','):
        # Strip whitespace
        ext = ext.strip()
        
        # Skip empty strings
        if not ext:
            continue
        
        # Remove leading dots and convert to lowercase
        ext = ext.lstrip('.').lower()
        
        # Skip if empty after processing
        if not ext:
            continue
        
        # Validate extension format (alphanumeric and common chars only)
        if not all(c.isalnum() or c in ['-', '_'] for c in ext):
            logger.warning(f"Invalid extension format: {ext}, skipping")
            continue
        
        extensions.append(ext)
    
    # Return None if no valid extensions found
    return extensions if extensions else None


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

    # Create catch configuration that points to failure state
    catch_config = [create_catch_config(
        error_equals=["States.TaskFailed"],
        next_state=failed_state_id
    )]

    # Create retry configuration
    retry_config = create_retry_config(
        error_equals=["States.ALL"],
        interval_seconds=5,
        backoff_rate=2.0,
        max_attempts=3
    )

    # Generate GLOBAL output paths (shared by ALL pipelines)
    # Use the FIRST pipeline's name for the global output location
    first_pipeline_name = pipelines[0]['name']
    first_job_name = job_names[0]
    
    global_output_s3_asset_files_uri = f"States.Format('s3://{{}}/pipelines/{first_pipeline_name}/{first_job_name}/output/{{}}/files/', $.bucketAsset, $$.Execution.Name)"
    global_output_s3_asset_preview_uri = f"States.Format('s3://{{}}/pipelines/{first_pipeline_name}/{first_job_name}/output/{{}}/previews/', $.bucketAsset, $$.Execution.Name)"
    global_output_s3_asset_metadata_uri = f"States.Format('s3://{{}}/pipelines/{first_pipeline_name}/{first_job_name}/output/{{}}/metadata/', $.bucketAsset, $$.Execution.Name)"

    # Build list of pipeline states
    states = []
    
    for i, pipeline in enumerate(pipelines):
        assetAuxiliaryAssetSubFolderName = "pipelines"
        if pipeline.get('pipelineType', 'standardFile') == 'previewFile':
            assetAuxiliaryAssetSubFolderName = "preview"

        inputOutput_s3_assetAuxiliary_files_uri = f"States.Format('s3://{{}}/{{}}/{assetAuxiliaryAssetSubFolderName}/{pipeline['name']}/', $.bucketAssetAuxiliary, $.inputAssetFileKey)"

        # First pipeline uses original input, subsequent pipelines use global output paths
        if i == 0:
            input_s3_asset_uri = "States.Format('s3://{}/{}', $.bucketAsset, $.inputAssetFileKey)"
        else:
            input_s3_asset_uri = global_output_s3_asset_files_uri

        logger.info(f"Processing pipeline {i}: {pipeline['name']}")
        
        if ('pipelineExecutionType' in pipeline and pipeline['pipelineExecutionType'] == 'Lambda'):
            # Create Lambda step
            # Trim UID to first 5 chars, place in front of pipeline name, then trim to 80 chars
            lambda_state_id = (uuid.uuid1().hex[:5] + "-" + pipeline['name'])[:80]
            
            # Build callback configuration
            callback_config = {}
            if 'waitForCallback' in pipeline and pipeline['waitForCallback'] == 'Enabled':
                callback_config['wait_for_callback'] = True
                
                if 'taskTimeout' in pipeline and pipeline['taskTimeout'].isdigit() and int(pipeline['taskTimeout']) > 0:
                    callback_config['timeout_seconds'] = int(pipeline['taskTimeout'])
                
                if 'taskHeartbeatTimeout' in pipeline and pipeline['taskHeartbeatTimeout'].isdigit() and int(pipeline['taskHeartbeatTimeout']) > 0:
                    callback_config['heartbeat_seconds'] = int(pipeline['taskHeartbeatTimeout'])

            # Parse user-provided resource
            userResource = json.loads(pipeline['userProvidedResource'])
            functionName = userResource['resourceId']

            # Check for input parameters
            inputParameters = ''
            if 'inputParameters' in pipeline and pipeline["inputParameters"] is not None and pipeline["inputParameters"] != "":
                try:
                    json.loads(pipeline['inputParameters'])
                    inputParameters = pipeline['inputParameters']
                except json.decoder.JSONDecodeError:
                    logger.warn("Input parameters provided is not a JSON object.... skipping inclusion")

            # Build Lambda payload with GLOBAL output paths (shared by all pipelines)
            lambda_payload = {
                "body": {
                    "inputS3AssetFilePath.$": input_s3_asset_uri,
                    "outputS3AssetFilesPath.$": global_output_s3_asset_files_uri,
                    "outputS3AssetPreviewPath.$": global_output_s3_asset_preview_uri,
                    "outputS3AssetMetadataPath.$": global_output_s3_asset_metadata_uri,
                    "inputOutputS3AssetAuxiliaryFilesPath.$": inputOutput_s3_assetAuxiliary_files_uri,
                    "bucketAssetAuxiliary.$": "$.bucketAssetAuxiliary",
                    "bucketAsset.$": "$.bucketAsset",
                    "inputAssetFileKey.$": "$.inputAssetFileKey",
                    "inputAssetLocationKey.$": "$.inputAssetLocationKey",
                    "outputType": pipeline["outputType"],
                    "inputMetadata.$": "$.inputMetadata",
                    "inputParameters": inputParameters,
                    "executingUserName.$": "$.executingUserName",
                    "executingRequestContext.$": "$.executingRequestContext"
                }
            }

            # Add TaskToken if callback is enabled
            if callback_config.get('wait_for_callback'):
                lambda_payload['body']['TaskToken.$'] = "$$.Task.Token"

            # Create the Lambda task state
            lambda_state = create_lambda_task_state(
                state_id=lambda_state_id,
                function_name=functionName,
                payload=lambda_payload,
                result_path=f"$.{lambda_state_id}.output",
                wait_for_callback=callback_config.get('wait_for_callback', False),
                timeout_seconds=callback_config.get('timeout_seconds'),
                heartbeat_seconds=callback_config.get('heartbeat_seconds'),
                retry_config=retry_config,
                catch_config=catch_config
            )
            
            states.append((lambda_state_id, lambda_state))

    # Create SINGLE process output state (runs ONCE after ALL pipelines complete)
    # Use the LAST pipeline's information for the process output
    last_pipeline = pipelines[-1]
    last_job_name = job_names[-1]
    
    process_output_state_id = f"process-outputs-{uuid.uuid1().hex}"
    process_output_payload = {
        "body": {
            "databaseId.$": "$.databaseId",
            "assetId.$": "$.assetId",
            "workflowDatabaseId.$": "$.workflowDatabaseId",
            "workflowId.$": "$.workflowId",
            "assetLocationKey.$": "$.inputAssetLocationKey",
            "filesPathKey.$": f"States.Format('pipelines/{first_pipeline_name}/{first_job_name}/output/{{}}/files/', $$.Execution.Name)",
            "metadataPathKey.$": f"States.Format('pipelines/{first_pipeline_name}/{first_job_name}/output/{{}}/metadata/', $$.Execution.Name)",
            "previewPathKey.$": f"States.Format('pipelines/{first_pipeline_name}/{first_job_name}/output/{{}}/previews/', $$.Execution.Name)",
            "description": f'Output from {last_job_name}',
            "executionId.$": "$$.Execution.Name",
            "pipeline": last_pipeline['name'],
            "outputType": last_pipeline["outputType"],
            "executingUserName.$": "$.executingUserName",
            "executingRequestContext.$": "$.executingRequestContext"
        }
    }

    process_output_state = create_lambda_task_state(
        state_id=process_output_state_id,
        function_name=process_workflow_output_function,
        payload=process_output_payload,
        result_path=f"$.{process_output_state_id}.output",
        retry_config=retry_config,
        catch_config=catch_config
    )
    
    # Add the single process_output state to the states list
    states.append((process_output_state_id, process_output_state))

    # Create the complete workflow definition (without fail state in sequential flow)
    workflow_definition = create_workflow_definition(
        states=states,
        comment=f"VAMS Pipeline Workflow for {workflowId}"
    )
    
    # Add the failure state to States dict (reachable only via Catch handlers)
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
        ARN of the created state machine
        
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
        return workflow_arn
        
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
        ARN of the updated state machine (same as input)
        
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
        return existing_arn
        
    except Exception as e:
        logger.exception(f"Error updating state machine {existing_arn}: {e}")
        raise VAMSGeneralErrorResponse("Error updating workflow state machine")


def create_workflow(payload):
    """
    Create or update a workflow.
    
    Handles three scenarios:
    1. New workflow - Creates new state machine and DynamoDB record
    2. Update existing - Updates state machine definition (preserves execution history)
    3. Orphaned record - Creates new state machine if old one was deleted
    
    Args:
        payload: Workflow creation/update payload
        
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
    
    if existing_workflow and 'workflow_arn' in existing_workflow:
        # Workflow exists - check if state machine still exists
        existing_arn = existing_workflow['workflow_arn']
        logger.info(f"Found existing workflow with ARN: {existing_arn}")
        
        if verify_state_machine_exists(existing_arn):
            # UPDATE existing state machine (preserves execution history)
            logger.info(f"Updating existing workflow: {workflow_id}")
            workflow_arn = update_step_function_existing(
                existing_arn,
                pipelines,
                database_id,
                workflow_id
            )
            is_update = True
        else:
            # State machine was deleted - CREATE new one
            logger.info(f"State machine {existing_arn} not found, creating new one")
            workflow_arn = create_step_function_new(pipelines, database_id, workflow_id)
    else:
        # New workflow - CREATE
        logger.info(f"Creating new workflow: {workflow_id}")
        workflow_arn = create_step_function_new(pipelines, database_id, workflow_id)
    
    # Update DynamoDB record
    try:
        table = dynamodb.Table(workflow_Database)
        dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
        
        # Get username from claims_and_roles tokens array
        username = claims_and_roles["tokens"][0] if len(claims_and_roles.get("tokens", [])) > 0 else "system"
        
        Item = {
            'databaseId': database_id,
            'workflowId': workflow_id,
            'description': payload['description'],
            'specifiedPipelines': payload['specifiedPipelines'],
            'workflow_arn': workflow_arn,
            'dateModified': json.dumps(dtNow),
            'modifiedBy': username
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
        
        table.put_item(Item=Item)
        
        action = "updated" if is_update else "created"
        logger.info(f"Workflow {action} by {username}: {workflow_id}")
        
        return json.dumps({"message": 'Succeeded'})
        
    except ClientError as e:
        logger.exception(f"Error saving workflow {workflow_id} to DynamoDB: {e}")
        raise VAMSGeneralErrorResponse("Error saving workflow")
    except Exception as e:
        logger.exception(f"Error saving workflow {workflow_id} to DynamoDB: {e}")
        raise VAMSGeneralErrorResponse("Error saving workflow")


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)
    logger.info(event)

    # Parse request body
    if not event.get('body'):
        message = 'Request body is required'
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    if isinstance(event['body'], str):
        try:
            event['body'] = json.loads(event['body'])
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Invalid JSON in request body"})
            return response

    try:
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            # TODO: Validate if database and pipelines exist before proceeding
            if 'databaseId' not in event['body']:
                message = "No databaseId in API Call"
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": message})
                logger.info(response['body'])
                return response
            if 'specifiedPipelines' not in event['body'] or len(event['body']['specifiedPipelines']) == 0:
                message = "No pipelines in API Call"
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": message})
                logger.info(response['body'])
                return response
            
            # Validate that functions array exists and is not empty
            if 'functions' not in event['body']['specifiedPipelines'] or len(event['body']['specifiedPipelines']['functions']) == 0:
                message = "No pipeline functions specified in API Call"
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": message})
                logger.error(response)
                return response
            
            # Check for missing fields
            required_field_names = ['databaseId', 'workflowId', 'description']
            missing_field_names = list(set(required_field_names).difference(event['body']))
            if missing_field_names:
                message = 'Missing body parameter(s) (%s) in API call' % (', '.join(missing_field_names))
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                logger.error(response)
                return response

            (valid, message) = validate({
                'databaseId': {
                    'value': event['body']['databaseId'],
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                },
                'pipelineId': {
                    'value': pipelineArray,
                    'validator': 'ID_ARRAY'
                },
                'workflowId': {
                    'value': event['body']['workflowId'],
                    'validator': 'ID'
                },
                'description': {
                    'value': event['body']['description'],
                    'validator': 'STRING_256'
                },
            })
            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            # Validate autoTriggerOnFileExtensionsUpload if provided
            auto_trigger = event['body'].get('autoTriggerOnFileExtensionsUpload', '')
            if auto_trigger and auto_trigger.strip():
                parsed_extensions = validate_and_parse_extensions(auto_trigger)
                if parsed_extensions is None:
                    message = "Invalid autoTriggerOnFileExtensionsUpload format. Must be comma-delimited extensions (e.g., 'jpg,png,pdf') or 'all'."
                    response['statusCode'] = 400
                    response['body'] = json.dumps({"message": message})
                    logger.error(response)
                    return response

            pipelineArray = []
            for pipeline in event['body']['specifiedPipelines']['functions']:
                logger.info("pipeline in workflow creation: ")
                logger.info(pipeline)
                # If global workflow, included pipeline should also be global
                if event['body']['databaseId'] == "GLOBAL":
                    if pipeline['databaseId'] != "GLOBAL":
                        response['statusCode'] = 400
                        response['body'] = json.dumps({"message": "Only global pipelines are allowed in global workflows."})
                        return response
                else:
                    if pipeline['databaseId'] != "GLOBAL" and event['body']['databaseId'] != pipeline['databaseId']:
                        response['statusCode'] = 400
                        response['body'] = json.dumps({"message": "Only global or same database pipelines are allowed in a database specifc workflows."})
                        return response
                # Add Casbin Enforcer to check if the current user has permissions to GET the pipeline:
                pipeline_allowed = False
                pipeline.update({
                    "object__type": "pipeline",
                    "databaseId": event['body']['databaseId'],
                    "pipelineId": pipeline['name'],
                    "pipelineType": pipeline['pipelineType'],
                    "pipelineExecutionType": pipeline['pipelineExecutionType'],
                })
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(pipeline, "GET"):
                        pipeline_allowed = True

                if pipeline_allowed:
                    pipelineArray.append(pipeline['name'])
                else:
                    response['statusCode'] = 403
                    response['body'] = json.dumps({"message": "Not Authorized to read the pipeline"})
                    return response


            logger.info("Validating workflow authorization")
            workflow_allowed = False
            # Add Casbin Enforcer to check if the current user has permissions to PUT the workflow:
            workflow = {
                "object__type": "workflow",
                'databaseId': event['body']['databaseId'],
                'workflowId': event['body']['workflowId'],
            }
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(workflow, "PUT"):
                    workflow_allowed = True

            if workflow_allowed:
                response['body'] = create_workflow(event['body'])
                logger.info(response)
                return response
            else:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Not Authorized"})
                return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except VAMSGeneralErrorResponse as v:
        logger.exception("VAMS error in workflow creation. ")
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": str(v)})
        return response
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] == 'LimitExceededException' or err.response['Error']['Code'] == 'ThrottlingException':
            logger.exception("Throttling Error")
            response['statusCode'] = err.response['ResponseMetadata']['HTTPStatusCode']
            response['body'] = json.dumps({"message": "ThrottlingException: Too many requests within a given period."})
            return response
        else:
            logger.exception("AWS Client Error")
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
            return response
    except Exception as e:
        logger.exception("Internal error in workflow creation")
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
