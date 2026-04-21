#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import random
import string
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import to_update_expr
from handlers.workflows import update_pipeline_workflows
from models.common import (
    APIGatewayProxyResponseV2,
    success,
    validation_error,
    authorization_error,
    internal_error,
    general_error,
    VAMSGeneralErrorResponse,
)
from models.pipelines import CreatePipelineRequestModel, UserProvidedResource

logger = safeLogger(service_name="CreatePipeline")

# Configure AWS clients
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Load environment variables
try:
    db_table_name = os.environ.get("DATABASE_STORAGE_TABLE_NAME")
    pipeline_table_name = os.environ.get("PIPELINE_STORAGE_TABLE_NAME")
    workflow_table_name = os.environ.get("WORKFLOW_STORAGE_TABLE_NAME")
    enable_pipeline_function_name = os.environ.get("ENABLE_PIPELINE_FUNCTION_NAME")
    enable_pipeline_function_arn = os.environ.get("ENABLE_PIPELINE_FUNCTION_ARN")
    lambda_role_to_attach = os.environ.get("ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE")
    lambda_pipeline_sample_function_bucket = os.environ.get("LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET")
    lambda_pipeline_sample_function_key = os.environ.get("LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY")
    subnet_ids_string = os.environ.get("SUBNET_IDS", "")
    security_group_ids_string = os.environ.get("SECURITYGROUP_IDS", "")
    lambda_python_version = os.environ.get("LAMBDA_PYTHON_VERSION")

    if not all([pipeline_table_name, db_table_name]):
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Parse subnet and security group IDs
subnet_ids = subnet_ids_string.split(',') if subnet_ids_string else []
security_group_ids = security_group_ids_string.split(',') if security_group_ids_string else []


#######################
# Utility Functions
#######################

def generate_random_string(length=8):
    """Generates a random character alphanumeric string with a set input length."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))


def _now():
    return datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')


def validate_database_exists(database_id):
    """Validate databaseId exists if not 'global'"""
    if database_id.lower().strip() != 'global':
        db_table = dynamodb.Table(db_table_name)
        db_response = db_table.get_item(Key={'databaseId': database_id})
        if 'Item' not in db_response:
            raise ValueError("Database provided does not exist")


def create_lambda_pipeline(lambda_name):
    """Create a new Lambda function for the pipeline"""
    logger.info('Creating a lambda function')
    create_params = {
        'FunctionName': lambda_name,
        'Role': lambda_role_to_attach,
        'PackageType': 'Zip',
        'Code': {
            'S3Bucket': lambda_pipeline_sample_function_bucket,
            'S3Key': lambda_pipeline_sample_function_key
        },
        'Handler': 'lambda_function.lambda_handler',
        'Runtime': lambda_python_version,
    }

    if subnet_ids and security_group_ids:
        create_params['VpcConfig'] = {
            'SubnetIds': subnet_ids,
            'SecurityGroupIds': security_group_ids
        }

    lambda_client.create_function(**create_params)


def build_lambda_name(pipeline_id):
    """Generate a unique Lambda name from the pipeline ID"""
    lambda_name = pipeline_id
    if len(lambda_name) > 50:
        lambda_name = lambda_name[-50:]

    # Strip special characters, lowercase, strip leading digits
    lambda_name = ''.join(e for e in lambda_name if e.isalnum())
    lambda_name = lambda_name.lower()
    lambda_name = lambda_name.lstrip(string.digits)

    lambda_name = lambda_name + generate_random_string(8)
    lambda_name = "vams-" + lambda_name
    if len(lambda_name) > 64:
        lambda_name = lambda_name[-63:]

    return lambda_name


def build_user_provided_resource(request_model):
    """Build the UserProvidedResource based on execution type"""
    execution_type = request_model.pipelineExecutionType

    if execution_type == 'Lambda':
        if request_model.lambdaName and request_model.lambdaName.strip():
            # User provided a Lambda name
            return UserProvidedResource(
                resourceId=request_model.lambdaName.strip(),
                resourceType="Lambda",
                isProvided=True,
            )
        else:
            # Auto-create a Lambda function
            lambda_name = build_lambda_name(request_model.pipelineId)
            create_lambda_pipeline(lambda_name)
            return UserProvidedResource(
                resourceId=lambda_name,
                resourceType="Lambda",
                isProvided=False,
            )

    elif execution_type == 'SQS':
        return UserProvidedResource(
            resourceId=request_model.sqsQueueUrl,
            resourceType="SQS",
            isProvided=True,
        )

    elif execution_type == 'EventBridge':
        return UserProvidedResource(
            resourceId=request_model.eventBridgeBusArn or "default",
            resourceType="EventBridge",
            isProvided=True,
            eventSource=request_model.eventBridgeSource,
            eventDetailType=request_model.eventBridgeDetailType,
        )

    else:
        raise ValueError(f"Unknown pipelineExecutionType: {execution_type}")


def format_pipeline(item, body):
    """Format pipeline item for workflow update response"""
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


def upload_pipeline(request_model, claims_and_roles, event):
    """Create or update a pipeline in DynamoDB"""
    # Tier 2: Object-level authorization
    pipeline = {
        "object__type": "pipeline",
        "databaseId": request_model.databaseId,
        "pipelineId": request_model.pipelineId,
        "pipelineType": request_model.pipelineType,
        "pipelineExecutionType": request_model.pipelineExecutionType,
    }
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(pipeline, "PUT"):
            return authorization_error()

    # Validate database exists
    try:
        validate_database_exists(request_model.databaseId)
    except ValueError as e:
        return validation_error(body={'message': str(e)}, event=event)

    # Prevent changing pipelineExecutionType on existing pipelines
    table = dynamodb.Table(pipeline_table_name)
    existing_item = table.get_item(
        Key={
            'databaseId': request_model.databaseId,
            'pipelineId': request_model.pipelineId,
        }
    ).get('Item')

    if existing_item:
        existing_exec_type = existing_item.get('pipelineExecutionType', 'Lambda')
        if request_model.pipelineExecutionType != existing_exec_type:
            return validation_error(
                body={
                    'message': f"Cannot change pipelineExecutionType from '{existing_exec_type}' to "
                               f"'{request_model.pipelineExecutionType}'. Pipeline execution type is "
                               f"immutable after creation. Delete and recreate the pipeline to change its type."
                },
                event=event
            )

    # Build user-provided resource based on execution type
    try:
        user_resource = build_user_provided_resource(request_model)
    except Exception as e:
        logger.exception(f"Error building pipeline resource: {e}")
        return internal_error(event=event)

    logger.info("Setting Time Stamp")
    dt_now = _now()

    item = {
        'assetType': request_model.assetType,
        'outputType': request_model.outputType,
        'description': request_model.description,
        'dateCreated': json.dumps(dt_now),
        'pipelineType': request_model.pipelineType,
        'pipelineExecutionType': request_model.pipelineExecutionType,
        'inputParameters': request_model.inputParameters or "",
        'object__type': 'pipeline',
        'waitForCallback': request_model.waitForCallback,
        'userProvidedResource': json.dumps(user_resource.dict()),
        'enabled': True
    }

    # Set callback parameters if waitForCallback is enabled
    if request_model.waitForCallback == "Enabled":
        item['taskTimeout'] = request_model.taskTimeout or "86400"
        if request_model.taskHeartbeatTimeout and request_model.taskHeartbeatTimeout.strip():
            item['taskHeartbeatTimeout'] = request_model.taskHeartbeatTimeout

    # table already initialized above for the existence check
    keys_map, values_map, expr = to_update_expr(item)

    table.update_item(
        Key={
            'databaseId': request_model.databaseId,
            'pipelineId': request_model.pipelineId,
        },
        UpdateExpression=expr,
        ExpressionAttributeNames=keys_map,
        ExpressionAttributeValues=values_map,
    )

    if request_model.updateAssociatedWorkflows:
        body_dict = request_model.dict()
        response_for_workflows = format_pipeline(item, body_dict)
        # Build a minimal self-like object for update_pipeline_workflows
        class _WorkflowCtx:
            def __init__(self):
                self.workflow_db_table_name = workflow_table_name
                self.enable_pipeline_function_name = enable_pipeline_function_name
                self.enable_pipeline_function_arn = enable_pipeline_function_arn
        update_pipeline_workflows(_WorkflowCtx(), response_for_workflows, event)

    return success(body={"message": "Succeeded"})


#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for creating pipelines"""
    logger.info(event)

    try:
        # Parse request body
        if not event.get('body'):
            return validation_error(body={'message': 'Request body is required'}, event=event)

        event_body = event['body']
        if isinstance(event_body, str):
            try:
                event_body = json.loads(event_body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': 'Invalid JSON in request body'}, event=event)

        # Parse and validate request via Pydantic model (includes field validation)
        try:
            request_model = parse(event_body, model=CreatePipelineRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error: {v}")
            return validation_error(body={'message': str(v)}, event=event)

        # Tier 1: API-level authorization
        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Create pipeline
        return upload_pipeline(request_model, claims_and_roles, event)

    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return internal_error(event=event)
