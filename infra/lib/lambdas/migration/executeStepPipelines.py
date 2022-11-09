#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
from boto3.dynamodb.conditions import Key
import os
import os
import uuid

import boto3
import stepfunctions
from stepfunctions.steps import (
    Chain,
    ProcessingStep,
    LambdaStep
)
from stepfunctions.workflow import Workflow
from sagemaker.processing import ProcessingInput, ProcessingOutput
from sagemaker.processing import Processor

try:
    client = boto3.client('lambda')
    error_Function = os.environ['ERROR_FUNCTION']
    sagemaker_execution=os.environ['SAGEMAKER_FUNCTION']
    upload_all_function=os.environ['UPLOAD_ALL_LAMBDA_FUNCTION_NAME']
    _err = lambda payload: client.invoke(FunctionName=error_Function,InvocationType='Event', Payload=json.dumps(payload).encode('utf-8'))
except Exception as e:
    print(str(e))
    print("Failed Loading Error Functions")    
s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    pipeline_Database=os.environ["PIPELINE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
def updateDatabase(pName, body, status, message="running"):
    print(pName+":"+json.dumps(body)+":"+message)
    table = dynamodb.Table(asset_Database)
    try:
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                body['databaseId']) & Key('assetId').eq(body['assetId']),
            ScanIndexForward=False,
        )
        if len(resp['Items']) == 0:
            raise ValueError('No Items of this AssetId found in Database')
        else:
            item = resp['Items'][0]
            version = body['VersionId']
            pipelines = []
            if version == item['currentVersion']['S3Version']:
                pipelines = item['currentVersion']['specifiedPipelines']
                test = True
                for i, p in enumerate(pipelines):
                    if p['name'] == pName:
                        pipelines[i]['status'] = status
                        test = False
                        break
                if test:
                    new = {
                        'name': pName,
                        'status': status
                    }
                    pipelines.append(new)
                item['currentVersion']['specifiedPipelines'] = pipelines
                item['specifiedPipelines']=pipelines
            else:
                item0=item
                for i, f in item0['versions']:
                    if f['S3Version'] == version:
                        pipelines = f['specifiedPipelines']
                        test = True
                        for j, p in enumerate(pipelines):
                            if p['name'] == pName:
                                test = False
                                pipelines[j]['status'] = status
                                break
                        if test:
                            new = {
                                'name': pName,
                                'status': status
                            }
                            pipelines.append(new)
                        item['versions'][i]['specifiedPipelines']=pipelines
                        break
    except Exception as e:
        print(e)
        raise(e)
        return json.dumps({"message": str(e)})

def get_Pipelines(databaseId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
    )
    return response['Items']

def launchPipelines(data, bucketName, key, version):
    pipelines = []
    mData = ""
    print(data)
    if "Metadata" in data:
        mData = data['Metadata']
        print(mData)
        print('M True')
    else:
        raise ValueError("No Metadata in launchPipelines")
    message = "Pipelines Executed:["
    if 'pipelines' in mData:
        print("p True")
        pipelines = mData['pipelines']
        # TODO remove this, I'm not sure why this is necessary. Is a string passed somewhere else in the code?
        try: 
            pipelines = json.loads(pipelines)
        except Exception as e:
            print("Json loads failed")
        pipelines = pipelines['functions']
        print(pipelines)
    if len(pipelines) > 0:
        db_pipelines=get_Pipelines(mData['databaseId'])

        create_step_function(pipelines, mData)

    message = message+"]"
    print(message)

def lambda_handler(event, context):
    print(event)
    try:
        if 'Records' not in event:
            if isinstance(event['body'], str):
                event['body'] = json.loads(event['body'])    
            data=event['body']
            mData={
                'Metadata':{
                    'assetId':data['assetId'],
                    'databaseId':data['databaseId'],
                    'Bucket':data['bucketName'],
                    'Key':data['key'],
                    'pipelines':data['specifiedPipelines']
                }
            }
            if 'versionId' in data:
                launchPipelines(mData, data['bucketName'], data['key'], data['versionId'])
            else:
                response = s3c.head_object(Bucket=data['bucketName'], Key=data['key'])
                version=response['VersionId']
                launchPipelines(mData, data['bucketName'], data['key'],version)
            print('Relaunching Pipleines')

        else:
            print("S3 Upload")
            eS3 = event['Records'][0]['s3']
            bucketName = eS3['bucket']['name']
            print(bucketName)
            key = eS3['object']['key']
            version = eS3['object']['versionId']
            response = s3c.head_object(Bucket=bucketName, Key=key, VersionId=version)
            print(response)
            launchPipelines(response, bucketName, key, version)
            print("Success")
    except Exception as e:
        raise(e)
        e=str(e)
        print(e)
        _err(e)



def create_step_function(pipelines, mData):
    region = os.environ['AWS_REGION']

    # SageMaker Execution Role
    # You can use sagemaker.get_execution_role() if running inside sagemaker's notebook instance
    role = os.environ['LAMBDA_ROLE_ARN']
    #step_role = "arn:aws:iam::611143256665:role/AmazonSageMaker-StepFunctionsWorkflowExecutionRole"

    client = boto3.client('sts')
    account_id = client.get_caller_identity()['Account']

    bucket_name = mData['Bucket']
    object_name = mData['Key']

    # Generate unique names for Pre-Processing Job, Training Job, and Model Evaluation Job for the Step Functions Workflow
    job_names = [
        x['name']+"-{}".format(uuid.uuid1().hex) for x in pipelines  # Each Training Job requires a unique name
    ]

    instance_type = os.getenv("INSTANCE_TYPE", "ml.m5.large")
    
    # Step function failed state
    failed_state_sagemaker_processing_failure = stepfunctions.steps.states.Fail(
        "Workflow failed", cause="SageMakerProcessingJobFailed"
    )

    catch_state_processing = stepfunctions.steps.states.Catch(
        error_equals=["States.TaskFailed"],
        next_step=failed_state_sagemaker_processing_failure,
    )

    input_s3_uri = f's3://{bucket_name}/{object_name}'
    output_s3_uri = f's3://{bucket_name}'

    steps = []
    for i, pipeline in enumerate(pipelines):
        output_s3_uri += f'/{pipeline["name"]}'
    
        image_uri = account_id+'.dkr.ecr.'+region+'.amazonaws.com/'+pipeline['name']
        processor = Processor(
            role=role,
            image_uri=image_uri,
            instance_count=1,
            instance_type=instance_type,
            base_job_name=mData['databaseId'],
            volume_size_in_gb = 32
        )

        inputs=[
            ProcessingInput(
                input_name='input',
                source=input_s3_uri,
                destination='/opt/ml/processing/input')
        ]
        outputs=[
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

        step.add_catch(catch_state_processing)
        steps.append(step)

        # Update the input/outputs so that the next step knows where to start/end
        # New input = old output
        # New output = old output + next pipeline step
        input_s3_uri = output_s3_uri

        # TODO Load assets here
        l_payload = {
            "body": {
                "databaseId": mData['databaseId'],
                "bucket": mData['Bucket'],
                "key" : output_s3_uri.split(mData['Bucket'], 1)[-1],
                "description": f'Output from {pipeline["name"]}',
            }
        }
        steps.append(LambdaStep(
            state_id="upload-assets-{}".format(uuid.uuid1().hex),
            parameters={
                "FunctionName": upload_all_function, #replace with the name of your function
                "Payload": l_payload
            }
        ))


    workflow_graph = Chain(steps)
    branching_workflow = Workflow(
        name=f"{mData['databaseId']}-{'-'.join([x['name'] for x in pipelines])}",
        definition=workflow_graph,
        role=role,
    )

    branching_workflow.create()

    job_name_inputs = dict()
    for job_name in job_names:
        job_name_inputs[job_name] = job_name

    # Execute workflow
    execution = branching_workflow.execute(
        inputs=job_name_inputs# Each pre processing job (SageMaker processing job) requires a unique name, 
    )
    execution_output = execution.get_output(wait=False)
