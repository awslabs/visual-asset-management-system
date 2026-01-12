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
    """Create a new database entry in DynamoDB
    
    This function uses put_item with a conditional expression to ensure
    the database is only created if it doesn't already exist. This prevents
    the POST endpoint from updating existing databases.
    """
    try:
        table = dynamodb.Table(db_database)
        dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
        
        # Validate bucket exists in S3_ASSET_BUCKETS_STORAGE_TABLE
        # Using query instead of get_item because the table has a sort key
        buckets_table = dynamodb.Table(s3_asset_buckets_table)
        from boto3.dynamodb.conditions import Key
        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(request_model.defaultBucketId)
        )
        
        if not bucket_response.get('Items') or len(bucket_response['Items']) == 0:
            raise VAMSGeneralErrorResponse("Bucket ID not found")
        
        # Create database with put_item and conditional expression
        # This will fail atomically if database already exists
        table.put_item(
            Item={
                'databaseId': request_model.databaseId,
                'description': request_model.description,
                'defaultBucketId': request_model.defaultBucketId,
                'restrictMetadataOutsideSchemas': request_model.restrictMetadataOutsideSchemas,
                'restrictFileUploadsToExtensions': request_model.restrictFileUploadsToExtensions,
                'assetCount': 0,
                'dateCreated': dtNow,
            },
            ConditionExpression='attribute_not_exists(databaseId)'
        )
        
        return CreateDatabaseResponseModel(
            databaseId=request_model.databaseId,
            message="Database created successfully"
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.error(f"Database already exists")
            raise VAMSGeneralErrorResponse("Database already exists")
        logger.exception(f"Error creating database: {e}")
        raise VAMSGeneralErrorResponse("Error creating database")
    except Exception as e:
        logger.exception(f"Error creating database: {e}")
        raise VAMSGeneralErrorResponse("Error creating database")

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
        if http_method == 'POST' and path.endswith('/database'):

            # Parse request body with enhanced error handling
            body = event.get('body')
            if not body:
                return validation_error(body={'message': "Request body is required"})
            
            # Parse JSON body safely
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in request body: {e}")
                    return validation_error(body={'message': "Invalid JSON in request body"})
            elif isinstance(body, dict):
                body = body
            else:
                logger.error("Request body is not a string")
                return validation_error(body={'message': "Request body must be a string"})
            
            
            # Validate required fields in the request body
            required_fields = ['databaseId', 'description', 'defaultBucketId']
            for field in required_fields:
                if field not in body:
                    return validation_error(body={'message': f"Missing required field: {field}"})
            
            # Parse request model
            request_model = parse(body, model=CreateDatabaseRequestModel)
            
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
            return validation_error(body={'message': "Database already exists."})
        logger.exception(f"AWS error: {e}")
        return internal_error()
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return validation_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
