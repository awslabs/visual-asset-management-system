#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
import time

_role=os.environ['LAMBDA_ROLE_ARN']
_jobFunction=os.environ['JOB_FUNCTION_LAMBDA_NAME']
def get_unique_job_name(base_name):
    """ Returns a unique job name based on a given base_name
        and the current timestamp """
    timestamp = time.strftime('%Y%m%d-%H%M%S')
    return f'{base_name}-{timestamp}'
    
def get_file_input(name, input_s3_uri, output_path):
    """ Returns the input file configuration
        Modify if you need different input method """
    return {
        'InputName': name,
        'S3Input': {
            'S3Uri': input_s3_uri,
            'LocalPath': output_path,
            'S3DataType': 'S3Prefix',
            'S3InputMode': 'File'
        }
    }

def get_file_output(name, local_path, ouput_s3_uri):
    """ Returns output file configuration
        Modify for different output method """
    return {
        'OutputName': name,
        'S3Output': {
            'S3Uri': ouput_s3_uri,
            'LocalPath': local_path,
            'S3UploadMode': 'EndOfJob'
        }
    }
    
def get_app_spec(image_uri, container_arguments=None, entrypoint=None):
    app_spec = {
        'ImageUri': image_uri
    }
    
    if container_arguments is not None:
        app_spec['ContainerArguments'] = container_arguments

    if entrypoint is not None:
        # Similar to ScriptProcessor in sagemaker SDK:
        # Run a custome script within the container
        app_spec['ContainerEntrypoint'] = ['python', entrypoint]

    return app_spec

def lambda_handler(event, context):
    print(event)
    print(context)
    region = os.environ['AWS_REGION']
    client = boto3.client('sts')
    account_id = client.get_caller_identity()['Account']
    bucket_name = event['Bucket']
    object_name = event['Key']
    output_type=event['outputType']
    pipeline_name=event['pipelineId']
    s3_uri = f's3://{bucket_name}/{object_name}'
    assetId=event['assetId']
    databaseId=event['databaseId']
    output_name=assetId+'-'+pipeline_name+output_type
    output_s3_uri = f's3://{bucket_name}/{output_name}'
    print(f'Received file: {s3_uri}')
    
    # Grab some configurations from env
    instance_type = os.getenv("INSTANCE_TYPE", "ml.m5.large")
    image_uri=account_id+'.dkr.ecr.'+region+'.amazonaws.com/'+pipeline_name
    role=_role
    print(role)
    
    # Declare parameters
    inputs = [
        get_file_input('input', s3_uri, '/opt/ml/processing/input')
    ]
    outputs = [
        get_file_output('output', '/opt/ml/processing/output', output_s3_uri)
    ]
    job_name = get_unique_job_name(assetId)
    cluster_config = {
        'InstanceCount': 1,
        'InstanceType': instance_type,
        'VolumeSizeInGB': 32
    }

    # TODO: implement container argument parsing
    container_arguments = None
    if 'container_arguments' in event:
        container_arguments=event['container_arguments']
    app_spec = get_app_spec(image_uri, container_arguments=container_arguments)
    
    sm = boto3.client('sagemaker')
    
    sm.create_processing_job(
        ProcessingInputs=inputs,
        ProcessingOutputConfig={
            'Outputs': outputs
        },
        ProcessingJobName=job_name,
        ProcessingResources={
            'ClusterConfig': cluster_config
        },
        AppSpecification=app_spec,
        RoleArn=role
    )
    _lambda=boto3.client('lambda')
    event['jobId']=job_name
    _lambda.invoke(FunctionName=_jobFunction,InvocationType='Event',Payload=json.dumps(event).encode('utf-8'))
    return {
        'statusCode': 200,
        'body': json.dumps({'ProcessingJobName': job_name})
    }