#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
from venv import create
import boto3
import sys
import json
import datetime
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from backend.common.validators import validate
import traceback


class CreatePipeline():

    def __init__(self, dynamodb, cloudformation, lambda_client, env):
        self.dynamodb = dynamodb
        self.cloudformation = cloudformation
        self.lambda_client = lambda_client


        self.db_table_name                 = env["PIPELINE_STORAGE_TABLE_NAME"]
        self.enable_pipeline_function_name = env["ENABLE_PIPELINE_FUNCTION_NAME"]
        self.enable_pipeline_function_arn  = env["ENABLE_PIPELINE_FUNCTION_ARN"]
        self.s3_bucket                     = env['S3_BUCKET']
        self.sagemaker_bucket_name         = env['SAGEMAKER_BUCKET_NAME']
        self.sagemaker_bucket_arn          = env['SAGEMAKER_BUCKET_ARN']
        self.asset_bucket_arn              = env['ASSET_BUCKET_ARN']
        self.lambda_role_to_attach         = env['ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE']
        self.lambda_pipeline_sample_function_bucket = env['LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET']
        self.lambda_pipeline_sample_function_key    = env['LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY']


        self.table = dynamodb.Table(self.db_table_name)


    @staticmethod
    def from_env():
        dynamodb = boto3.resource('dynamodb')
        cloudformation= boto3.client('cloudformation')
        lambda_client = boto3.client('lambda')
        return CreatePipeline(
            dynamodb, 
            cloudformation, 
            lambda_client, 
            os.environ)


    def _now(self):
        return datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    
    def upload_Pipeline(self, body):
        print("Setting Time Stamp")
        dtNow = self._now()
    
        userResource = {
            'isProvided': False,
            'resourceId': ''
        }
        if 'containerUri' in body:
            userResource['isProvided'] = True 
            userResource['resourceId'] = body['containerUri'] 
        elif 'lambdaName' in body:
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
            'waitForCallback': body['waitForCallback'],
            'userProvidedResource': json.dumps(userResource),
            'enabled':False #Not doing anything with this yet
        }

        if 'taskTimeout' in body:
            item['taskTimeout'] = body['taskTimeout']

        if 'taskHeartbeatTimeout' in body:
            item['taskHeartbeatTimeout'] = body['taskHeartbeatTimeout']

        self.table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(databaseId) and attribute_not_exists(pipelineId)'
        )
        #If a lambda function name or ECR container URI was provided by the user, creation is not necessary
        if userResource['isProvided'] == True:
            return json.dumps({"message": 'Succeeded'})
    
        print("Running CFT")
        if body['pipelineType']=='SageMaker':
            self.createSagemakerPipeline(body)
        elif body['pipelineType']=='Lambda':
            self.createLambdaPipeline(body)
        else:
            raise ValueError("Unknown pipelineType")

        return json.dumps({"message": 'Succeeded'})

    def createLambdaPipeline(self, body):
        print('Creating a lambda function')
        self.lambda_client.create_function(
            FunctionName=body['pipelineId'],
            Role=self.lambda_role_to_attach,
            PackageType='Zip',
            Code={
                'S3Bucket': self.lambda_pipeline_sample_function_bucket,
                'S3Key': self.lambda_pipeline_sample_function_key
            },
            Handler='lambda_function.lambda_handler',
            Runtime='python3.8'
        )
    
    def readSagemakerTemplate(self):
        s3 = boto3.resource('s3')
        obj = s3.Object(self.s3_bucket, "cloudformation/sagemaker_notebook.yaml")
        return obj.get()['Body'].read().decode('utf-8') 

    def createSagemakerPipeline(self, body):
        print('Running SageMaker CFT')
        # configPath = os.environ['LAMBDA_TASK_ROOT'] + "/nested_cft/sagemaker_notebook.yaml"
        # print("Looking for CFT at " + configPath)
        configContent = self.readSagemakerTemplate()
        print(configContent)
        # TODO: if this stack creation fails, we need to rollback to the database saved
        cft_response=self.cloudformation.create_stack(
                StackName=body['pipelineId'],
                TemplateBody=configContent,
                Parameters=[
                    {
                        'ParameterKey': 'EnablePipelineLambdaFunction', 
                        'ParameterValue': self.enable_pipeline_function_name,
                    },
                    {
                        'ParameterKey': 'EnablePipelineLambdaFunctionArn', 
                        'ParameterValue': self.enable_pipeline_function_arn,
                    },
                    {
                        'ParameterKey': 'DatabaseId', 
                        'ParameterValue': body['databaseId'],
                    },
                    {
                        'ParameterKey': 'S3Bucket', 
                        'ParameterValue': self.s3_bucket,
                    },
                    {
                        'ParameterKey': 'SagemakerBucketName', 
                        'ParameterValue': self.sagemaker_bucket_name,
                    },
                    {
                        'ParameterKey': 'SagemakerBucketArn', 
                        'ParameterValue': self.sagemaker_bucket_arn,
                    },
                    {
                        'ParameterKey': 'AssetBucketArn', 
                        'ParameterValue': self.asset_bucket_arn,
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

    def enablePipeline(self):
        print("Starting Pipeline Enablement")

def lambda_handler(event, context, create_pipeline_fn=CreatePipeline.from_env):
    print(event)
    create_pipeline = create_pipeline_fn()
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
            create_pipeline.enablePipeline()
        else:
            response['body'] = create_pipeline.upload_Pipeline(event['body'])
        return response
    except Exception as e:
        response['statusCode'] = 500
        print("Error!", e.__class__, "occurred.", traceback.format_exc())
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
