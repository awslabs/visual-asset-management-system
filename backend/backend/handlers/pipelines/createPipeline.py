#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

from distutils.command.config import config
import os
from venv import create
import boto3
import sys
import json
import datetime
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from backend.common.validators import validate
dynamodb = boto3.resource('dynamodb')
cloudformation= boto3.client('cloudformation')
lambda_client = boto3.client('lambda')

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

unitTest = {
    "body": {
        "databaseId": "Unit_Test",
        "pipelineId":"stl_to_glb_converter",
        "description": "converts stl to glb",
        "assetType":".stl",
        "lambdaName":"stl_to_glb_converter"
    }
}
unitTest['body']=json.dumps(unitTest['body'])

db_table_name = None

try:
    db_table_name = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    enable_pipeline_function_name = os.environ["ENABLE_PIPELINE_FUNCTION_NAME"]
    enable_pipeline_function_arn = os.environ["ENABLE_PIPELINE_FUNCTION_ARN"]
    s3_bucket=os.environ['S3_BUCKET']
    sagemaker_bucket_name=os.environ['SAGEMAKER_BUCKET_NAME']
    sagemaker_bucket_arn = os.environ['SAGEMAKER_BUCKET_ARN']
    asset_bucket_arn = os.environ['ASSET_BUCKET_ARN']
    lambda_role_to_attach = os.environ['ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE']
    lambda_pipeline_sample_function_bucket = os.environ['LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET']
    lambda_pipeline_sample_function_key = os.environ['LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY']

except:
    print("Failed Loading Environment Variables")
    response['body'] = json.dumps({
        "message": "Failed Loading Environment Variables"
    })
    response['statusCode']=500

def upload_Pipeline(body):
    print("Setting Table")
    table = dynamodb.Table(db_table_name)
    print("Setting Time Stamp")
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    
    userResource = {
        'isProvided': False,
        'resourceId': ''
    }
    if body['containerUri'] != None:
        userResource['isProvided'] = True 
        userResource['resourceId'] = body['containerUri'] 
    elif body['lambdaName'] != None:
        userResource['isProvided'] = True 
        userResource['resourceId'] = body['lambdaName']

    item = {
        'databaseId': body['databaseId'],
        'pipelineId':body['pipelineId'],
        'assetType':body['assetType'],
        'outputType':body['outputType'],
        'description': body['description'],
        'dateCreated': json.dumps(dtNow),
        'pipelineType':body['pipelineType'],
        'userProvidedResource': json.dumps(userResource),
        'enabled':False #Not doing anything with this yet
    }
    table.put_item(
        Item=item,
        ConditionExpression='attribute_not_exists(databaseId) and attribute_not_exists(pipelineId)'
    )
    #If a lambda function name or ECR container URI was provided by the user, creation is not necessary
    if userResource['isProvided'] == True:
        return json.dumps({"message": 'Succeeded'})
    
    print("Running CFT")
    if body['pipelineType']=='SageMaker':
        createSagemakerPipeline(body)
    elif body['pipelineType']=='Lambda':
        createLambdaPipeline(body)
    else:
        raise ValueError("Unknown pipelineType")

    return json.dumps({"message": 'Succeeded'})

def createLambdaPipeline(body):
    print('Creating a lambda function')
    lambda_client.create_function(
        FunctionName=body['pipelineId'],
        Role=lambda_role_to_attach,
        PackageType='Zip',
        Code={
            'S3Bucket': lambda_pipeline_sample_function_bucket,
            'S3Key': lambda_pipeline_sample_function_key
        },
        Handler='lambda_function.lambda_handler',
        Runtime='python3.8'
    )
    
def readSagemakerTemplate():
    s3 = boto3.resource('s3')
    obj = s3.Object(s3_bucket, "cloudformation/sagemaker_notebook.yaml")
    return obj.get()['Body'].read().decode('utf-8') 

def createSagemakerPipeline(body):
    print('Running SageMaker CFT')
    # configPath = os.environ['LAMBDA_TASK_ROOT'] + "/nested_cft/sagemaker_notebook.yaml"
    # print("Looking for CFT at " + configPath)
    configContent = readSagemakerTemplate()
    print(configContent)
    # TODO: if this stack creation fails, we need to rollback to the database saved
    cft_response=cloudformation.create_stack(
            StackName=body['pipelineId'],
            TemplateBody=configContent,
            Parameters=[
                {
                    'ParameterKey': 'EnablePipelineLambdaFunction', 
                    'ParameterValue': enable_pipeline_function_name,
                },
                {
                    'ParameterKey': 'EnablePipelineLambdaFunctionArn', 
                    'ParameterValue': enable_pipeline_function_arn,
                },
                {
                    'ParameterKey': 'DatabaseId', 
                    'ParameterValue': body['databaseId'],
                },
                {
                    'ParameterKey': 'S3Bucket', 
                    'ParameterValue': s3_bucket,
                },
                {
                    'ParameterKey': 'SagemakerBucketName', 
                    'ParameterValue': sagemaker_bucket_name,
                },
                {
                    'ParameterKey': 'SagemakerBucketArn', 
                    'ParameterValue': sagemaker_bucket_arn,
                },
                {
                    'ParameterKey': 'AssetBucketArn', 
                    'ParameterValue': asset_bucket_arn,
                },
                {
                    'ParameterKey':'PipelineName',
                    'ParameterValue':body['pipelineId']
                },
                {
                    'ParameterKey':'SageMakeNotebookInstanceType',
                    'ParameterValue':'ml.t2.medium'
                }
            ],
            Tags=[
                {
                    'Key': 'StackController',
                    'Value': 'VAMS'
                }
            ],
            Capabilities=[
                'CAPABILITY_IAM',
            ],
        )

def enablePipeline():
    print("Starting Pipeline Enablement")

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
    print(event['body'])
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body']) 
    try:
        reqs=['databaseId','pipelineId','description','assetType','pipelineType','outputType']
        for r in reqs:
            if r not in event['body']:
                message = "No "+str(r)+" in API Call"
                response['body'] = response['body']+message
            if response['body']!="":
                response['statusCode']=500
                print(response)
                response['body']=json.dumps({"message":message})
                return response

        print("Validating Parameters")
        if event['body']['pipelineType'] == 'SageMaker':
            (valid, message) = validate({
                'databaseId': {
                    'value': event['body']['databaseId'], 
                    'validator': 'ID'
                },
                'pipelineId': {
                    'value': event['body']['pipelineId'], 
                    'validator': 'SAGEMAKER_NOTEBOOK_ID'
                },
                'description': {
                    'value': event['body']['description'], 
                    'validator': 'STRING_256'
                },
                'assetType':  {
                    'value':  event['body']['assetType'], 
                    'validator': 'FILE_EXTENSION'
                }, 
                'outputType': {
                    'value':  event['body']['outputType'], 
                    'validator': 'FILE_EXTENSION'
                }
            })
        else:
            (valid, message) = validate({
                'databaseId': {
                    'value': event['body']['databaseId'], 
                    'validator': 'ID'
                },
                'pipelineId': {
                    'value': event['body']['pipelineId'], 
                    'validator': 'ID'
                },
                'description': {
                    'value': event['body']['description'], 
                    'validator': 'STRING_256'
                },
                'assetType':  {
                    'value':  event['body']['assetType'], 
                    'validator': 'FILE_EXTENSION'
                }, 
                'outputType': {
                    'value':  event['body']['outputType'], 
                    'validator': 'FILE_EXTENSION'
                }
            })

        if not valid:
            print(message)
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Trying to get Data")
        if 'starting' in event['body'] and event['body']['starting']=='enabling':
            enablePipeline()
        else:
            response['body'] = upload_Pipeline(event['body'])
        return response
    except Exception as e:
        response['statusCode'] = 500
        print("Error!", e.__class__, "occurred.")
        if e.response['Error']['Code']=='ConditionalCheckFailedException':
            response['statusCode']=500
            response['body'] = json.dumps({"message":"Pipeline "+str(event['body']['pipelineId']+" already exists.")})
            return response
        else:
            response['statusCode'] = 500
            print("Error!", e.__class__, "occurred.")
            try:
                print(e)
                response['body'] = json.dumps({"message": str(e)})
            except:
                print("Can't Read Error")
                response['body'] = json.dumps({"message": "An unexpected error occurred while executing the request"})
            return response
