#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
import json
import datetime
import os
import uuid
import random
import string
from common.validators import validate
from common.constants import STANDARD_JSON_RESPONSE
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

import stepfunctions
from stepfunctions.steps import (
    Chain,
    ProcessingStep,
    LambdaStep
)
from stepfunctions.workflow import Workflow

# ########################################### NOTE############################################
# Due to the unique library imports of stepfunctions, this function is assigned
# its own lambda layer due to library sizes (close to the 250mb layer limit by itself).
# --------------------------------------------------------------------------------------------
# Please be cautious of adding anymore new library references, including downstream references
# when importing VAMS common files that use other libraries.
# ############################################################################################

# Set boto environment variable to use regional STS endpoint
# (https://stackoverflow.com/questions/71255594/request-times-out-when-try-to-assume-a-role-with-aws-sts-from-a-private-subnet-u)
# AWS_STS_REGIONAL_ENDPOINTS='regional'
os.environ["AWS_STS_REGIONAL_ENDPOINTS"] = 'regional'

logger = safeLogger(service="CreateWorkflow")

main_rest_response = STANDARD_JSON_RESPONSE

claims_and_roles = {}
lambda_client= boto3.client('lambda')
sf_client = boto3.client('stepfunctions')
#sts_client = boto3.client('sts')
dynamodb = boto3.resource('dynamodb')

try:
    workflow_Database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    stack_name = os.environ["VAMS_STACK_NAME"]
    process_workflow_output_function = os.environ['PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME']
    region = os.environ['AWS_REGION']
    role = os.environ['LAMBDA_ROLE_ARN']
    logGroupArn = os.environ['LOG_GROUP_ARN']
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps({
        "message": "Failed Loading Environment Variables"
    })

def generate_random_string(length=8):
    """Generates a random character alphanumeric string with a set input length."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))


def create_workflow(payload):
    workflow_arn = create_step_function(
        payload['specifiedPipelines']['functions'], payload['databaseId'], payload['workflowId'])
    table = dynamodb.Table(workflow_Database)
    logger.info("Payload")
    logger.info(payload)
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    Item = {
        'databaseId': payload['databaseId'],
        'workflowId': payload['workflowId'],
        'description': payload['description'],
        'specifiedPipelines': payload['specifiedPipelines'],
        'workflow_arn': workflow_arn,
        'dateCreated': json.dumps(dtNow),
    }
    table.put_item(
        Item=Item
    )
    return json.dumps({"message": 'Succeeded'})

def create_step_function(pipelines, databaseId, workflowId):
    logger.info("Creating state machine flow")

    # Generate unique names for Pre-Processing Job, Training Job, and Model Evaluation Job for the Step Functions Workflow
    job_names = [
        # Each Training Job requires a unique name
        x['name'] + "-{}".format(uuid.uuid1().hex) for x in pipelines
    ]
    logger.info(job_names)

    # Step function failed state
    failed_state_processing_failure = stepfunctions.steps.states.Fail(
        "Workflow failed", cause="WorkflowProcessingJobFailed"
    )

    catch_state_processing = stepfunctions.steps.states.Catch(
        error_equals=["States.TaskFailed"],
        next_step=failed_state_processing_failure,
    )



    steps = []
    for i, pipeline in enumerate(pipelines):

        assetAuxiliaryAssetSubFolderName = "pipelines"
        if pipeline.get('pipelineType', 'standardFile') == 'previewFile':
            assetAuxiliaryAssetSubFolderName = "preview"

        output_s3_asset_files_uri = "States.Format('s3://{}/pipelines/" + \
                pipeline["name"] + "/" + job_names[i] + "/output/{}/files/', $.bucketAsset, $$.Execution.Name)"

        output_s3_asset_preview_uri = "States.Format('s3://{}/pipelines/" + \
                pipeline["name"] + "/" + job_names[i] + "/output/{}/previews/', $.bucketAsset, $$.Execution.Name)"

        output_s3_asset_metadata_uri = "States.Format('s3://{}/pipelines/" + \
                pipeline["name"] + "/" + job_names[i] + "/output/{}/metadata/', $.bucketAsset, $$.Execution.Name)"

        inputOutput_s3_assetAuxiliary_files_uri = "States.Format('s3://{}/{}/" + \
               assetAuxiliaryAssetSubFolderName + "/" + pipeline["name"] + "/', $.bucketAssetAuxiliary, $.inputAssetFileKey)"

        if i == 0:
            input_s3_asset_uri = "States.Format('s3://{}/{}', $.bucketAsset, $.inputAssetFileKey)"
        else:
            input_s3_asset_uri = output_s3_asset_files_uri

        logger.info(output_s3_asset_files_uri)
        if ('pipelineExecutionType' in pipeline and pipeline['pipelineExecutionType'] == 'Lambda'):
            step = create_lambda_step(pipeline, input_s3_asset_uri, output_s3_asset_files_uri, output_s3_asset_preview_uri, output_s3_asset_metadata_uri, inputOutput_s3_assetAuxiliary_files_uri)
            step.add_retry(retry=stepfunctions.steps.Retry(
                error_equals=["States.ALL"],
                interval_seconds=5,
                backoff_rate=2,
                max_attempts=3
            ))
            step.add_catch(catch_state_processing)
            logger.info(step)
            steps.append(step)

        #For standard pipelines executed on a file, add a upload asset step at the end of the workflow
        #TODO: Make this global after a workflow as right now its being added after pipeline component
        l_payload = {
            "body": {
                "databaseId.$": "$.databaseId",
                "assetId.$": "$.assetId",
                "workflowDatabaseId.$": "$.workflowDatabaseId",
                "workflowId.$": "$.workflowId",
                "filesPathKey.$": "States.Format('pipelines/" + pipeline["name"] + "/" + job_names[
                    i] + "/output/{}/files/', $$.Execution.Name)",
                "metadataPathKey.$": "States.Format('pipelines/" + pipeline["name"] + "/" + job_names[
                    i] + "/output/{}/metadata/', $$.Execution.Name)",
                "previewPathKey.$": "States.Format('pipelines/" + pipeline["name"] + "/" + job_names[
                    i] + "/output/{}/previews/', $$.Execution.Name)",
                "description": f'Output from {pipeline["name"]}',
                "executionId.$": "$$.Execution.Name",
                "pipeline": pipeline["name"],
                "outputType": pipeline["outputType"],
                "executingUserName.$": "$.executingUserName",
                "executingRequestContext.$": "$.executingRequestContext"
            }
        }

        steps.append(LambdaStep(
            state_id="process-outputs-{}".format(uuid.uuid1().hex),
            parameters={
                "FunctionName": process_workflow_output_function,  # replace with the name of your function
                "Payload": l_payload
            }
        ))

    #Generate unique name for the Step Functions Workflow with randomization
    #Workflow name must have 'vams' in it for permissiong
    # Make sure workFlowName is not longer than 80 characters
    workFlowName = workflowId
    if len(workFlowName) > 66:
        workFlowName = workFlowName[-66:]  # use 66 characters
    workFlowName = workFlowName + generate_random_string(8)
    workFlowName = "vams-"+ workFlowName
    if len(workFlowName) > 80:
        workFlowName = workFlowName[-79:]  # use 79 characters for buffer

    workflow_graph = Chain(steps)

    branching_workflow = Workflow(
        name=workFlowName,
        definition=workflow_graph,
        role=role
    )

    logger.info("Submitting state machine flow 1")

    workflow_arn = branching_workflow.create()
    response = sf_client.describe_state_machine(
        stateMachineArn=workflow_arn
    )

    original_workflow = json.loads(response['definition'])
    for i, step_name in enumerate(original_workflow["States"]):
        try:

            if original_workflow["States"][step_name]["Type"] == "Task":
                outputResultPath = "$." + step_name + ".output"
                original_workflow["States"][step_name]["ResultPath"] = outputResultPath

            pipelineName = step_name.split("-")[0]
            original_workflow["States"][step_name]["Parameters"].pop(
                "ProcessingJobName")
            # Two jobs can't have the same name, appending job name with ExecutionId
            original_workflow["States"][step_name]["Parameters"][
                "ProcessingJobName.$"] = "States.Format('" + pipelineName + "-" + str(
                i) + "-{}', $$.Execution.Name)"

            original_workflow["States"][step_name]["Parameters"]["ProcessingInputs"][0]['S3Input']["S3Uri.$"] = \
                original_workflow["States"][step_name]["Parameters"]["ProcessingInputs"][0]['S3Input'].pop(
                    "S3Uri")

            original_workflow["States"][step_name]["Parameters"]["ProcessingOutputConfig"]['Outputs'][0]['S3Output'][
                "S3Uri.$"] = \
                original_workflow["States"][step_name]["Parameters"]["ProcessingOutputConfig"]['Outputs'][0][
                    'S3Output'].pop(
                    "S3Uri")

        except KeyError:
            continue

    new_workflow = json.dumps(original_workflow, indent=2)

    logger.info("Submitting state machine flow 2")

    sf_client.update_state_machine(
        stateMachineArn=workflow_arn,
        definition=new_workflow,
        roleArn=role,
        loggingConfiguration={
            'destinations': [{
                'cloudWatchLogsLogGroup': {
                    'logGroupArn': logGroupArn
                }}],
            'level': 'ALL'
        },
        tracingConfiguration={
            'enabled': True
        }
    )
    logger.info("State machine created successfully")
    return workflow_arn


def create_lambda_step(pipeline, input_s3_asset_file_uri, output_s3_asset_files_uri, output_s3_asset_preview_uri, output_s3_asset_metadata_uri, inputOutput_s3_assetAuxiliary_files_uri):

    userResource = json.loads(pipeline['userProvidedResource'])
    functionName = userResource['resourceId']

    #Check if we have inputParameters in pipeline and if so, check to make sure we can JSON parse them
    inputParameters = ''
    if 'inputParameters' in pipeline and pipeline["inputParameters"] != None and pipeline["inputParameters"] != "":
        try:
            json.loads(pipeline['inputParameters'])
            inputParameters = pipeline['inputParameters']
        except json.decoder.JSONDecodeError:
            logger.warn("Input parameters provided is not a JSON object.... skipping inclusion")

    #TODO: Generate Presigned URLs for download to S3. Create function / API for pipelines to call to generate upload URLs. More secure and extensible.
    lambda_payload = {
        "body": {
            "inputS3AssetFilePath.$": input_s3_asset_file_uri,
            "outputS3AssetFilesPath.$": output_s3_asset_files_uri,
            "outputS3AssetPreviewPath.$": output_s3_asset_preview_uri,
            "outputS3AssetMetadataPath.$": output_s3_asset_metadata_uri,
            "inputOutputS3AssetAuxiliaryFilesPath.$": inputOutput_s3_assetAuxiliary_files_uri,
            "bucketAssetAuxiliary.$": "$.bucketAssetAuxiliary",
            "bucketAsset.$": "$.bucketAsset",
            "inputAssetFileKey.$": "$.inputAssetFileKey",
            "outputType": pipeline["outputType"],           
            "inputMetadata.$": "$.inputMetadata",
            "inputParameters": inputParameters,
            "executingUserName.$": "$.executingUserName",
            "executingRequestContext.$": "$.executingRequestContext"
        }
    }

    callback_args = {}
    if 'waitForCallback' in pipeline and pipeline['waitForCallback'] == 'Enabled':
        callback_args['wait_for_callback'] = True

        #Add taskToken parameter to lambda payload
        lambda_payload['body']['TaskToken.$'] = "$$.Task.Token"

        f = 'taskTimeout'
        if f in pipeline and pipeline[f].isdigit() and int(pipeline[f]) > 0:
            callback_args['timeout_seconds'] = int(pipeline[f])

        f = 'taskHeartbeatTimeout'
        if f in pipeline and pipeline[f].isdigit() and int(pipeline[f]) > 0:
            callback_args['heartbeat_seconds'] = int(pipeline[f])

    return LambdaStep(
        state_id="{}-{}".format(pipeline['name'], uuid.uuid1().hex),
        parameters={
            # replace with the name of your function
            "FunctionName": functionName,
            "Payload": lambda_payload
        },
        **callback_args,
    )


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
    # event['body']=json.loads(event['body'])
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

            # Check for missing fields - TODO: would need to keep these synchronized
            #
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

            logger.info("Trying to get Data")
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
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] == 'LimitExceededException' or err.response['Error']['Code'] == 'ThrottlingException':
            logger.exception("Throttling Error")
            response['statusCode'] = err.response['ResponseMetadata']['HTTPStatusCode']
            response['body'] = json.dumps({"message": "ThrottlingException: Too many requests within a given period."})
            return response
        else:
            logger.exception(err)
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
            return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
