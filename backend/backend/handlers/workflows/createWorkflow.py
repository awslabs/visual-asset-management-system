#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import sys
import json
from boto3.dynamodb.conditions import Key, Attr
import datetime
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
import os
import uuid
from backend.common.validators import validate

import stepfunctions
from stepfunctions.steps import (
    Chain,
    ProcessingStep,
    LambdaStep
)
from stepfunctions.workflow import Workflow
from sagemaker.processing import ProcessingInput, ProcessingOutput
from sagemaker.processing import Processor


dynamodb = boto3.resource('dynamodb')

response = {
    'statusCode': 200,
    'body': '',
    'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Credentials': True,
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
    }
}

client = boto3.client('lambda')
sf_client = boto3.client('stepfunctions')
try:
    workflow_Database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    upload_all_function = os.environ['UPLOAD_ALL_LAMBDA_FUNCTION_NAME']
except:
    print("Failed Loading Environment Variables")
    response['body'] = json.dumps({
        "message": "Failed Loading Environment Variables"
    })


def create_workflow(payload):
    workflow_arn = create_step_function(
        payload['specifiedPipelines']['functions'], payload['databaseId'], payload['workflowId'])
    table = dynamodb.Table(workflow_Database)
    print("Payload")
    print(payload)
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


def lambda_handler(event, context):
    print(event)
    response = {
        'statusCode': 200,
        'body': '',
        'headers': {
            'Content-Type': 'application/json',
                'Access-Control-Allow-Credentials': True,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
        }
    }
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])
    # event['body']=json.loads(event['body'])
    try:
        # TODO: Validate if database and pipelines exist before proceeding
        if 'databaseId' not in event['body']:
            message = "No databaseId in API Call"
            response['body'] = json.dumps({"message": message})
            print(response['body'])
            return response
        if 'specifiedPipelines' not in event['body'] or len(event['body']['specifiedPipelines']) == 0:
            message = "No pipelines in API Call"
            response['body'] = json.dumps({"message": message})
            print(response['body'])
            return response

        pipelineArray = []
        for pipeline in event['body']['specifiedPipelines']['functions']:
            pipelineArray.append(pipeline['name'])

        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['databaseId'],
                'validator': 'ID'
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
            print(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Trying to get Data")
        response['body'] = create_workflow(event['body'])
        print(response)
        return response
    except Exception as e:
        response['statusCode'] = 500
        print("Error!", e.__class__, "occurred.")
        try:
            print(e)
            response['body'] = json.dumps({"message": str(e)})
        except:
            print("Can't Read Error")
            response['body'] = json.dumps(
                {"message": "An unexpected error occurred while executing the request"})
        return response


def create_step_function(pipelines, databaseId, workflowId):
    print("Creating state machine")
    region = os.environ['AWS_REGION']

    # SageMaker Execution Role
    # You can use sagemaker.get_execution_role() if running inside sagemaker's notebook instance
    role = os.environ['LAMBDA_ROLE_ARN']
    #step_role = "arn:aws:iam::611143256665:role/AmazonSageMaker-StepFunctionsWorkflowExecutionRole"

    client = boto3.client('sts')
    account_id = client.get_caller_identity()['Account']

    # Generate unique names for Pre-Processing Job, Training Job, and Model Evaluation Job for the Step Functions Workflow
    job_names = [
        # Each Training Job requires a unique name
        x['name']+"-{}".format(uuid.uuid1().hex) for x in pipelines
    ]
    print(job_names)
    instance_type = os.getenv("INSTANCE_TYPE", "ml.m5.large")

    # Step function failed state
    failed_state_sagemaker_processing_failure = stepfunctions.steps.states.Fail(
        "Workflow failed", cause="SageMakerProcessingJobFailed"
    )

    catch_state_processing = stepfunctions.steps.states.Catch(
        error_equals=["States.TaskFailed"],
        next_step=failed_state_sagemaker_processing_failure,
    )

    steps = []
    for i, pipeline in enumerate(pipelines):
        if i == 0:
            input_s3_uri = "States.Format('s3://{}/{}', $.bucket, $.key)"
        else:
            input_s3_uri = output_s3_uri

        output_s3_uri = "States.Format('s3://{}/pipelines/" + pipeline["name"] + "/" + job_names[i]+ "/output/{}/', $.bucket, $$.Execution.Name)"
        print(output_s3_uri)
        if ('pipelineType' in pipeline and pipeline['pipelineType'] == 'Lambda'):
            step = create_lambda_step(pipeline, input_s3_uri, output_s3_uri)
        else:
            step = create_sagemaker_step(databaseId, region, role, account_id, job_names, instance_type, i, pipeline, input_s3_uri, output_s3_uri)
        step.add_retry(retry=stepfunctions.steps.Retry(
            error_equals=["States.ALL"],
            interval_seconds=5,
            backoff_rate=2,
            max_attempts=3
        ))
        step.add_catch(catch_state_processing)
        steps.append(step)

        l_payload = {
            "body": {
                "databaseId.$": "$.databaseId",
                "assetId.$": "$.assetId",
                "workflowId.$": "$.workflowId",
                "bucket.$": "$.bucket",
                "key.$": "States.Format('pipelines/" + pipeline["name"] +"/"+ job_names[i] + "/output/{}/', $$.Execution.Name)",
                "description": f'Output from {pipeline["name"]}',
                "executionId.$": "$$.Execution.Name",
                "pipeline": pipeline["name"],
                "outputType": pipeline["outputType"]
            }
        }
        steps.append(LambdaStep(
            state_id="upload-assets-{}".format(uuid.uuid1().hex),
            parameters={
                "FunctionName": upload_all_function,  # replace with the name of your function
                "Payload": l_payload
            }
        ))

    workflow_graph = Chain(steps)
    branching_workflow = Workflow(
        name=workflowId,
        definition=workflow_graph,
        role=role,
    )

    workflow_arn = branching_workflow.create()
    response = sf_client.describe_state_machine(
        stateMachineArn=workflow_arn
    )

    original_workflow = json.loads(response['definition'])
    for i, step_name in enumerate(original_workflow["States"]):
        try:

            if original_workflow["States"][step_name]["Type"] == "Task":
                original_workflow["States"][step_name]["ResultPath"] = "$." + \
                    step_name+".output"

            pipelineName = step_name.split("-")[0]
            original_workflow["States"][step_name]["Parameters"].pop(
                "ProcessingJobName")
            # Two jobs cant have the same name, appending job name with ExecutionId
            original_workflow["States"][step_name]["Parameters"]["ProcessingJobName.$"] = "States.Format('"+pipelineName+"-"+str(
                i)+"-{}', $$.Execution.Name)"

            original_workflow["States"][step_name]["Parameters"]["ProcessingInputs"][0]['S3Input']["S3Uri.$"] = \
                original_workflow["States"][step_name]["Parameters"]["ProcessingInputs"][0]['S3Input'].pop(
                    "S3Uri")

            original_workflow["States"][step_name]["Parameters"]["ProcessingOutputConfig"]['Outputs'][0]['S3Output']["S3Uri.$"] = \
                original_workflow["States"][step_name]["Parameters"]["ProcessingOutputConfig"]['Outputs'][0]['S3Output'].pop(
                    "S3Uri")

        except KeyError:
            continue

    new_workflow = json.dumps(original_workflow, indent=2)

    sf_client.update_state_machine(
        stateMachineArn=workflow_arn,
        definition=new_workflow,
        roleArn=role
    )
    print("State machine created successfully")
    return workflow_arn


def create_sagemaker_step(databaseId, region, role, account_id, job_names, instance_type, i, pipeline, input_s3_uri, output_s3_uri):
    image_uri = account_id+'.dkr.ecr.'+region + \
        '.amazonaws.com/'+pipeline['name']
    processor = Processor(
        role=role,
        image_uri=image_uri,
        instance_count=1,
        instance_type=instance_type,
        base_job_name=databaseId,
        volume_size_in_gb=32
    )

    inputs = [
        ProcessingInput(
            input_name='input',
            source=input_s3_uri,
            destination='/opt/ml/processing/input')
    ]
    outputs = [
        ProcessingOutput(
            output_name='output',
            source='/opt/ml/processing/output',
            destination=output_s3_uri
        )
    ]

    # preprocessing_job_name = generate_job_name()
    step = ProcessingStep(
        job_names[i],
        processor=processor,
        job_name=job_names[i],
        inputs=inputs,
        outputs=outputs,
        container_arguments=None
    )

    return step


def create_lambda_step(pipeline, input_s3_uri, output_s3_uri):
    lambda_payload = {
        "body": {
            "inputPath.$": input_s3_uri,
            "outputPath.$": output_s3_uri,
        }
    }
    return LambdaStep(
        state_id="{}-{}".format(pipeline['name'], uuid.uuid1().hex),
        parameters={
            # replace with the name of your function
            "FunctionName": pipeline['name'],
            "Payload": lambda_payload
        })
