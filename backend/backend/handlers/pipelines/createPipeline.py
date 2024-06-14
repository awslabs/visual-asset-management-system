#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import random
import string
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="CreatePipeline")

def generate_random_string(length=8):
    """Generates a random character alphanumeric string with a set input length."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))

class CreatePipeline():

    def __init__(self, dynamodb, lambda_client, env):
        self.dynamodb = dynamodb
        self.lambda_client = lambda_client

        self.db_table_name = env["PIPELINE_STORAGE_TABLE_NAME"]
        self.enable_pipeline_function_name = env["ENABLE_PIPELINE_FUNCTION_NAME"]
        self.enable_pipeline_function_arn = env["ENABLE_PIPELINE_FUNCTION_ARN"]
        self.s3_bucket = env['S3_BUCKET']
        self.asset_bucket_arn = env['ASSET_BUCKET_ARN']
        self.lambda_role_to_attach = env['ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE']
        self.lambda_pipeline_sample_function_bucket = env['LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET']
        self.lambda_pipeline_sample_function_key = env['LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY']
        self.subNetIdsString = env['SUBNET_IDS']
        self.securityGroupIdsString = env['SECURITYGROUP_IDS']
        self.lambdaPythonVersion = env['LAMBDA_PYTHON_VERSION']

        #Create SubnetIds & SecurityGroupIds lists from string
        #Set to empty array if string is empty 
        if self.subNetIdsString == '':
            self.subNetIds = []
        else:
            self.subNetIds = self.subNetIdsString.split(',')

        if self.securityGroupIdsString == '':
            self.securityGroupIds = []
        else:
            self.securityGroupIds = self.securityGroupIdsString.split(',')

        #logger.info(self.subNetIds)
        #logger.info(self.securityGroupIds)

        self.table = dynamodb.Table(self.db_table_name)

    @staticmethod
    def from_env():
        dynamodb = boto3.resource('dynamodb')
        lambda_client = boto3.client('lambda')
        return CreatePipeline(
            dynamodb,
            lambda_client,
            os.environ)

    def _now(self):
        return datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')

    def upload_Pipeline(self, body):
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to PUT the pipeline:
        pipeline = {
            "object__type": "pipeline",
            "databaseId": body['databaseId'],
            "pipelineId": body['pipelineId'],
            "pipelineType": body['pipelineType'],
            "pipelineExecutionType": body['pipelineExecutionType'],
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", pipeline, "PUT"):
                allowed = True
                break

        if not allowed:
            return {
                "statusCode": 403,
                "body": json.dumps({"message": 'Not Authorized'})
            }

        logger.info("Setting Time Stamp")
        dtNow = self._now()

        userResource = {
            'isProvided': False,
            'resourceId': ''
        }

        if 'lambdaName' in body:
            userResource['isProvided'] = True
            userResource['resourceId'] = body['lambdaName']

        #Create new lambda function if one not provided
        if 'lambdaName' not in body:

            #Generate unique name for the Lambda with randomization
            #Workflow name must have 'vams' in it for permissing
            # Make sure lambdaName is not longer than 64 characters
            lambdaName = body['pipelineId']
            if len(lambdaName) > 50:
                lambdaName = lambdaName[-50:]  # use 50 characters
            lambdaName = lambdaName + generate_random_string(8)
            lambdaName = "vams-"+ lambdaName
            if len(lambdaName) > 64:
                lambdaName = lambdaName[-63:]  # use 63 characters for buffer

            userResource['isProvided'] = False
            userResource['resourceId'] = lambdaName
            self.createLambdaPipeline(lambdaName)

        #TODO: Check if we have invoke permission on provided lambdaFunction. Otherwise error. 

        logger.info("Running CFT")
        if body['pipelineExecutionType'] == 'Lambda':

            item = {
                'databaseId': body['databaseId'],
                'pipelineId': body['pipelineId'],
                'assetType': body['assetType'],
                'outputType': body['outputType'],
                'description': body['description'],
                'dateCreated': json.dumps(dtNow),
                'pipelineType': body['pipelineType'],
                'pipelineExecutionType': body['pipelineExecutionType'],
                'inputParameters': body.get("inputParameters", ""),
                'object__type': 'pipeline',
                'waitForCallback': body['waitForCallback'],
                'userProvidedResource': json.dumps(userResource),
                'enabled': True
            }

            #Set callback parameters if waitForCallback is enabled
            if body['waitForCallback'] == "Enabled":
                item['taskTimeout'] = body.get("taskTimeout", "86400") #default to 24 hours
                item['taskHeartbeatTimeout'] = body.get("taskHeartbeatTimeout", "3600") #default to 1 hour

            self.table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(databaseId) and attribute_not_exists(pipelineId)'
                )

        else:
            raise ValueError("Unknown Pipeline ExecutionType")


        return {
            "statusCode": 200,
            "body": json.dumps({"message": 'Succeeded'})
        }

    def createLambdaPipeline(self, lambdaName):
        logger.info('Creating a lambda function')

        #if we have subnetIds and security Group IDs and they are not a empty array, include them in creating the lambda
        if(self.subNetIds and self.securityGroupIds and len(self.subNetIds) > 0 and len(self.securityGroupIds) > 0):
            self.lambda_client.create_function(
                FunctionName=lambdaName,
                Role=self.lambda_role_to_attach,
                PackageType='Zip',
                Code={
                    'S3Bucket': self.lambda_pipeline_sample_function_bucket,
                    'S3Key': self.lambda_pipeline_sample_function_key
                },
                Handler='lambda_function.lambda_handler',
                Runtime=self.lambdaPythonVersion, #'pythonX.X'
                VpcConfig={
                    'SubnetIds': self.subNetIds,
                    'SecurityGroupIds': self.securityGroupIds
                }
            )
        else:
            self.lambda_client.create_function(
                FunctionName=lambdaName,
                Role=self.lambda_role_to_attach,
                PackageType='Zip',
                Code={
                    'S3Bucket': self.lambda_pipeline_sample_function_bucket,
                    'S3Key': self.lambda_pipeline_sample_function_key
                },
                Handler='lambda_function.lambda_handler',
                Runtime=self.lambdaPythonVersion #'pythonX.X'
        )

    def enablePipeline(self):
        logger.info("Starting Pipeline Enablement")


def lambda_handler(event, context, create_pipeline_fn=CreatePipeline.from_env):
    logger.info(event)
    create_pipeline = create_pipeline_fn()
    response = STANDARD_JSON_RESPONSE
    logger.info(event['body'])
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])
    try:
        # Check for missing fields - TODO: would need to keep these synchronized
        #
        required_field_names = ['databaseId', 'pipelineId', 'description', 'assetType', 'pipelineType','pipelineExecutionType',
                                'waitForCallback']
        missing_field_names = list(set(required_field_names).difference(event['body']))
        if missing_field_names:
            message = 'Missing body parameter(s) (%s) in API call' % (', '.join(missing_field_names))
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response
        
        if event['body']['pipelineType'] == 'standardFile' and 'outputType' not in event['body']:
            message = 'Missing body parameter(s) (outputType) in API call'
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response

        logger.info("Validating Parameters")

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
                'validator': 'FILE_EXTENSION',
                'optional': True
            },
            'inputParameters': {
                'value':  event['body']['inputParameters'],
                'validator': 'STRING_JSON',
                'optional': True
            }
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        global claims_and_roles
        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
                break

        if method_allowed_on_api:
            logger.info("Trying to get Data")
            if 'starting' in event['body'] and event['body']['starting'] == 'enabling':
                create_pipeline.enablePipeline()
            else:
                response.update(create_pipeline.upload_Pipeline(event['body']))
            return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
