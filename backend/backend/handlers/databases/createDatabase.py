# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import to_update_expr
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, authorization_error, VAMSGeneralErrorResponse
from models.databases import CreateDatabaseRequestModel, CreateDatabaseResponseModel

# Configure AWS clients
dynamodb = boto3.resource('dynamodb')
logger = safeLogger(service_name="CreateDatabase")

# Load environment variables
try:
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

#######################
# Utility Functions
#######################

def create_database(request_model: CreateDatabaseRequestModel):
    """Create a new database entry in DynamoDB"""
    try:
        table = dynamodb.Table(db_database)
        dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
        
        # Check if the bucket exists in S3_ASSET_BUCKETS_STORAGE_TABLE
        # Using query instead of get_item because the table has a sort key
        buckets_table = dynamodb.Table(s3_asset_buckets_table)
        from boto3.dynamodb.conditions import Key
        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(request_model.defaultBucketId)
        )
        
        if not bucket_response.get('Items') or len(bucket_response['Items']) == 0:
            raise VAMSGeneralErrorResponse(f"Bucket with ID {request_model.defaultBucketId} not found")
        

        # First update the description and defaultBucketId
        item = {
            'description': request_model.description,
            'defaultBucketId': request_model.defaultBucketId
        }
        keys_map, values_map, expr = to_update_expr(item)
        table.update_item(
            Key={
                'databaseId': request_model.databaseId,
            },
            UpdateExpression=expr,
            ExpressionAttributeNames=keys_map,
            ExpressionAttributeValues=values_map,
        )

        # Then update the assetCount and dateCreated if they don't exist
        keys_map, values_map, expr = to_update_expr({
            'assetCount': json.dumps(0),
            'dateCreated': json.dumps(dtNow),
        })
        try:
            table.update_item(
                Key={
                    'databaseId': request_model.databaseId,
                },
                UpdateExpression=expr,
                ExpressionAttributeNames=keys_map,
                ExpressionAttributeValues=values_map,
                ConditionExpression="attribute_not_exists(assetCount)"
            )
        except ClientError as ex:
            # This just means the record already exists, and we are updating an existing record
            if ex.response['Error']['Code'] == 'ConditionalCheckFailedException':
                pass
            else:
                raise ex

        return CreateDatabaseResponseModel(
            databaseId=request_model.databaseId,
            message="Database created successfully"
        )
    except Exception as e:
        logger.exception(f"Error creating database: {e}")
        raise VAMSGeneralErrorResponse(f"Error creating database: {str(e)}")

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for database creation API"""
    claims_and_roles = request_to_claims(event)
    
    try:
        # Get HTTP method and path
        http_method = event['requestContext']['http']['method']
        path = event['requestContext']['http']['path']
        
        # Check if this is the correct API route for database creation
        if http_method == 'POST' and path.endswith('/databases'):
            # Parse request body
            if isinstance(event['body'], str):
                event['body'] = json.loads(event['body'])
            
            # Validate required fields in the request body
            required_fields = ['databaseId', 'description', 'defaultBucketId']
            for field in required_fields:
                if field not in event['body']:
                    return validation_error(body={'message': f"Missing required field: {field}"})
            
            # Parse request model
            request_model = parse(event['body'], model=CreateDatabaseRequestModel)
            
            # Check authorization
            database = {
                "object__type": "database",
                "databaseId": request_model.databaseId
            }
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not (casbin_enforcer.enforce(database, "POST") and casbin_enforcer.enforceAPI(event)):
                    return authorization_error()
            
            # Process request
            response = create_database(request_model)
            return success(body=response.dict())
        else:
            # Not a route handled by this function
            logger.error(f"Unsupported route: {http_method} {path}")
            return validation_error(status_code=404, body={'message': 'Route not found'})
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)})
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.exception(f"Database already exists: {e}")
            return validation_error(body={'message': f"Database {event['body']['databaseId']} already exists."})
        logger.exception(f"AWS error: {e}")
        return internal_error()
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return validation_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
