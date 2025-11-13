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
from botocore.exceptions import ClientError

from common.dynamodb import to_update_expr
from handlers.workflows import update_pipeline_workflows

claims_and_roles = {}
logger = safeLogger(service="CreatePipeline")

dynamodb = boto3.resource('dynamodb')
db_table = dynamodb.Table(os.environ["DATABASE_STORAGE_TABLE_NAME"])

# Hard-coded allowed values for pipeline fields
ALLOWED_PIPELINE_TYPES = [
    'standardFile',
    'previewFile',
]

ALLOWED_CALLBACK_VALUES = [
    'Enabled',
    'Disabled'
]

ALLOWED_EXECUTION_TYPES = [
    'Lambda'
]

def validate_pipeline_fields(body):
    """Validate pipeline fields against allowed values"""
    
    # Validate databaseId exists if body['databaseId'] (lowered) is not global
    if body['databaseId'].lower().strip() != 'global':
        db_response = db_table.get_item(Key={'databaseId': body['databaseId']})
        if 'Item' not in db_response:
            raise ValueError(f"Database provided does not exist")
    
    # Validate pipelineType
    if body['pipelineType'] not in ALLOWED_PIPELINE_TYPES:
        raise ValueError(f"Invalid pipelineType. Allowed values: {', '.join(ALLOWED_PIPELINE_TYPES)}")
    
    # Validate waitForCallback
    if body['waitForCallback'] not in ALLOWED_CALLBACK_VALUES:
        raise ValueError(f"Invalid waitForCallback. Allowed values: {', '.join(ALLOWED_CALLBACK_VALUES)}")
    
    # Validate pipelineExecutionType
    if body['pipelineExecutionType'] not in ALLOWED_EXECUTION_TYPES:
        raise ValueError(f"Invalid pipelineExecutionType. Allowed values: {', '.join(ALLOWED_EXECUTION_TYPES)}")
    
    return True
            
def format_pipeline(item, body):
    item['pipelineId'] = body['pipelineId']
    item['databaseId'] = body['databaseId']
    item['name'] = body['pipelineId']
    if "description" in item:
        del item['description']
    if "dateCreated" in item:
        del item['dateCreated']
    if "enabled" in item:
        del item['enabled']
    if "assetType" in item:
        del item['assetType']
    array = []
    array.append(item)
    response = {}
    response['functions'] = array
    return response

def generate_random_string(length=8):
    """Generates a random character alphanumeric string with a set input length."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))

class CreatePipeline():

    def __init__(self, dynamodb, lambda_client, env):
        self.dynamodb = dynamodb
        self.lambda_client = lambda_client

        self.db_table_name = env["PIPELINE_STORAGE_TABLE_NAME"]
        self.workflow_db_table_name = env["WORKFLOW_STORAGE_TABLE_NAME"]
        self.enable_pipeline_function_name = env["ENABLE_PIPELINE_FUNCTION_NAME"]
        self.enable_pipeline_function_arn = env["ENABLE_PIPELINE_FUNCTION_ARN"]
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

    def upload_Pipeline(self, body, event):
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to PUT the pipeline:
        pipeline = {
            "object__type": "pipeline",
            "databaseId": body['databaseId'],
            "pipelineId": body['pipelineId'],
            "pipelineType": body['pipelineType'],
            "pipelineExecutionType": body['pipelineExecutionType'],
        }
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(pipeline, "PUT"):
                allowed = True

        if not allowed:
            return {
                "statusCode": 403,
                "body": json.dumps({"message": 'Not Authorized'})
            }

        # Validate pipeline fields against allowed values
        try:
            validate_pipeline_fields(body)
        except ValueError as e:
            return {
                "statusCode": 400,
                "body": json.dumps({"message": str(e)})
            }

        logger.info("Setting Time Stamp")
        dtNow = self._now()

        userResource = {
            'isProvided': False,
            'resourceId': ''
        }

        if 'lambdaName' in body and body.get('lambdaName', "") != "":
            userResource['isProvided'] = True
            userResource['resourceId'] = body['lambdaName'].strip() #Strip whitespace

        #Create new lambda function if one not provided
        if userResource['isProvided'] == False:

            #Generate unique name for the Lambda with randomization
            #Workflow name must have 'vams' in it for permissing
            # Make sure lambdaName is not longer than 64 characters
            lambdaName = body['pipelineId']
            if len(lambdaName) > 50:
                lambdaName = lambdaName[-50:]  # use 50 characters

            #strip out any special characters from pipelineId, make everything lowercase, and strip any numbers at the start
            lambdaName = ''.join(e for e in lambdaName if e.isalnum())
            lambdaName = lambdaName.lower()
            lambdaName = lambdaName.lstrip(string.digits)

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
                # 'databaseId': body['databaseId'],
                # 'pipelineId': body['pipelineId'],
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
                
                
            keys_map, values_map, expr = to_update_expr(item)

            # self.table.put_item(
            #     Item=item,
            #     ConditionExpression='attribute_not_exists(databaseId) and attribute_not_exists(pipelineId)'
            #     )
            self.table.update_item(
                Key={
                    'databaseId': body['databaseId'],
                    'pipelineId': body['pipelineId'],
                },
                UpdateExpression=expr,
                ExpressionAttributeNames=keys_map,
                ExpressionAttributeValues=values_map,
            )

            if body['updateAssociatedWorkflows'] == True:
                response = format_pipeline(item, body)
                update_pipeline_workflows(self, response, event)

        else:
            raise ValueError("Unknown Pipeline ExecutionType")

        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Succeeded"})
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
                'validator': 'ID',
                'allowGlobalKeyword': True
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
                'value':  event['body'].get('inputParameters', ''),
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
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            logger.info("Trying to get Data")
            if 'starting' in event['body'] and event['body']['starting'] == 'enabling':
                create_pipeline.enablePipeline()
            else:
                response.update(create_pipeline.upload_Pipeline(event['body'], event))
            return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except json.JSONDecodeError as e:
        logger.exception(e)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Could not decode JSON in input chain"})
        return response
    except ValueError as v:
        logger.exception(v)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Invalid input provided"})
        return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
