# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import base64
import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import validate_pagination_info
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, authorization_error, general_error, VAMSGeneralErrorResponse
from models.databases import GetDatabaseResponseModel, GetDatabasesRequestModel, GetDatabasesResponseModel, DeleteDatabaseResponseModel, UpdateDatabaseRequestModel, UpdateDatabaseResponseModel, BucketModel, GetBucketsRequestModel, GetBucketsResponseModel

# Configure AWS clients
dynamodb = boto3.resource('dynamodb')
dbClient = boto3.client('dynamodb')
deserializer = TypeDeserializer()
logger = safeLogger(service_name="DatabaseService")

# Load environment variables
try:
    db_database = os.environ.get("DATABASE_STORAGE_TABLE_NAME")
    workflow_database = os.environ.get("WORKFLOW_STORAGE_TABLE_NAME")
    pipeline_database = os.environ.get("PIPELINE_STORAGE_TABLE_NAME")
    asset_database = os.environ.get("ASSET_STORAGE_TABLE_NAME")
    s3_asset_buckets_table = os.environ.get("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME")
    
    if not all([db_database, workflow_database, pipeline_database, asset_database, s3_asset_buckets_table]):
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


#######################
# Utility Functions
#######################

def check_workflows(database_id):
    """Check if database has active workflows"""
    table = dynamodb.Table(workflow_database)
    db_response = table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id),
        ScanIndexForward=False,
        Limit=1
    )
    return db_response['Count'] > 0

def check_pipelines(database_id):
    """Check if database has active pipelines"""
    table = dynamodb.Table(pipeline_database)
    db_response = table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id),
        ScanIndexForward=False,
        Limit=1
    )
    return db_response['Count'] > 0

def check_assets(database_id):
    """Check if database has active assets"""
    table = dynamodb.Table(asset_database)
    db_response = table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id),
        ScanIndexForward=False,
        Limit=1
    )
    return db_response['Count'] > 0

def get_database(database_id, show_deleted=False, claims_and_roles=None):
    """Get a single database by ID"""
    try:
        table = dynamodb.Table(db_database)
        if show_deleted:
            database_id = database_id + "#deleted"

        db_response = table.get_item(
            Key={
                'databaseId': database_id
            }
        )

        database = db_response.get("Item", {})
        allowed = False

        if database:
            # Add Casbin Enforcer to check if the current user has permissions to GET the database
            database.update({
                "object__type": "database"
            })
            if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(database, "GET"):
                    allowed = True

            if allowed:
                # Get bucket information if defaultBucketId exists
                bucket_name = None
                base_assets_prefix = None
                if database.get('defaultBucketId'):
                    buckets_table = dynamodb.Table(s3_asset_buckets_table)
                    bucket_response = buckets_table.query(
                        KeyConditionExpression=Key('bucketId').eq(database.get('defaultBucketId')),
                        Limit=1
                    )
                    # Use the first item from the query results
                    bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
                    bucket_name = bucket.get('bucketName')
                    base_assets_prefix = bucket.get('baseAssetsPrefix')
                
                # Convert to model with bucket information and new config fields
                return GetDatabaseResponseModel(
                    databaseId=database.get('databaseId'),
                    description=database.get('description', ''),
                    dateCreated=database.get('dateCreated'),
                    assetCount=int(database.get('assetCount', 0)) if database.get('assetCount') else 0,
                    defaultBucketId=database.get('defaultBucketId'),
                    bucketName=bucket_name,
                    baseAssetsPrefix=base_assets_prefix,
                    restrictMetadataOutsideSchemas=database.get('restrictMetadataOutsideSchemas', False),
                    restrictFileUploadsToExtensions=database.get('restrictFileUploadsToExtensions', ''),
                )
            
        return None
    except Exception as e:
        logger.exception(f"Error getting database: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting database.")

def get_databases(query_params, show_deleted=False, claims_and_roles=None):
    """Get all databases with pagination"""
    try:
        # Parse query parameters
        request_model = GetDatabasesRequestModel(
            maxItems=int(query_params.get('maxItems', 10000)),
            pageSize=int(query_params.get('pageSize', 3000)),
            startingToken=query_params.get('startingToken'),
            showDeleted=show_deleted
        )
        
        # Build scan parameters
        operator = "NOT_CONTAINS"
        if show_deleted:
            operator = "CONTAINS"
            
        db_filter = {
            "databaseId": {
                "AttributeValueList": [{"S": "#deleted"}],
                "ComparisonOperator": f"{operator}"
            }
        }
        
        scan_params = {
            'TableName': db_database,
            'ScanFilter': db_filter,
            'Limit': request_model.pageSize
        }
        
        # Add ExclusiveStartKey if startingToken provided
        if request_model.startingToken:
            try:
                decoded_token = base64.b64decode(request_model.startingToken).decode('utf-8')
                scan_params['ExclusiveStartKey'] = json.loads(decoded_token)
            except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
                logger.exception(f"Invalid startingToken format: {e}")
                raise VAMSGeneralErrorResponse("Invalid pagination token")
        
        # Single scan call with pagination
        response = dbClient.scan(**scan_params)

        items = []
        for item in response.get('Items', []):
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}

            # Add Casbin Enforcer to check if the current user has permissions to GET the database
            deserialized_document.update({
                "object__type": "database"
            })
            
            if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_document, "GET"):
                    # Get bucket information if defaultBucketId exists
                    bucket_name = None
                    base_assets_prefix = None
                    if deserialized_document.get('defaultBucketId'):
                        buckets_table = dynamodb.Table(s3_asset_buckets_table)
                        bucket_response = buckets_table.query(
                            KeyConditionExpression=Key('bucketId').eq(deserialized_document.get('defaultBucketId')),
                            Limit=1
                        )
                        # Use the first item from the query results
                        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
                        bucket_name = bucket.get('bucketName')
                        base_assets_prefix = bucket.get('baseAssetsPrefix')
                    
                    # Convert to model with bucket information and new config fields
                    database_model = GetDatabaseResponseModel(
                        databaseId=deserialized_document.get('databaseId'),
                        description=deserialized_document.get('description', ''),
                        dateCreated=deserialized_document.get('dateCreated'),
                        assetCount=int(deserialized_document.get('assetCount', 0)) if deserialized_document.get('assetCount') else 0,
                        defaultBucketId=deserialized_document.get('defaultBucketId'),
                        bucketName=bucket_name,
                        baseAssetsPrefix=base_assets_prefix,
                        restrictMetadataOutsideSchemas=deserialized_document.get('restrictMetadataOutsideSchemas', False),
                        restrictFileUploadsToExtensions=deserialized_document.get('restrictFileUploadsToExtensions', ''),
                    )
                    items.append(database_model)

        # Build response with nextToken
        result = GetDatabasesResponseModel(Items=items)
        
        # Return LastEvaluatedKey as nextToken if present (base64 encoded)
        if 'LastEvaluatedKey' in response:
            json_str = json.dumps(response['LastEvaluatedKey'])
            result.NextToken = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        
        return result
    except Exception as e:
        logger.exception(f"Error getting databases: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting databases.")

def update_database(database_id, update_data, claims_and_roles=None):
    """Update an existing database with new data
    
    Args:
        database_id: The database ID
        update_data: Dictionary with fields to update
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        UpdateDatabaseResponseModel with operation result
    """
    try:
        # Get the existing database
        table = dynamodb.Table(db_database)
        db_response = table.get_item(Key={'databaseId': database_id})
        database = db_response.get("Item", {})
        
        if not database:
            raise VAMSGeneralErrorResponse("Database not found")
        
        # Check authorization
        database.update({"object__type": "database"})
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(database, "PUT"):
                raise VAMSGeneralErrorResponse("Access denied")
        
        # If defaultBucketId is being updated, verify it exists
        if 'defaultBucketId' in update_data and update_data['defaultBucketId'] is not None:
            buckets_table = dynamodb.Table(s3_asset_buckets_table)
            bucket_response = buckets_table.query(
                KeyConditionExpression=Key('bucketId').eq(update_data['defaultBucketId'])
            )
            if not bucket_response.get('Items') or len(bucket_response['Items']) == 0:
                raise VAMSGeneralErrorResponse("Bucket ID not found")
        
        # Update the fields
        logger.info(f"Updating database {database_id}")
        
        # Update only the provided fields
        if 'description' in update_data and update_data['description'] is not None:
            database['description'] = update_data['description']
        
        if 'defaultBucketId' in update_data and update_data['defaultBucketId'] is not None:
            database['defaultBucketId'] = update_data['defaultBucketId']
        
        if 'restrictMetadataOutsideSchemas' in update_data and update_data['restrictMetadataOutsideSchemas'] is not None:
            database['restrictMetadataOutsideSchemas'] = update_data['restrictMetadataOutsideSchemas']
        
        if 'restrictFileUploadsToExtensions' in update_data and update_data['restrictFileUploadsToExtensions'] is not None:
            database['restrictFileUploadsToExtensions'] = update_data['restrictFileUploadsToExtensions']
        
        # Save the updated database
        table.put_item(Item=database)
        
        # Create response
        from datetime import datetime
        timestamp = datetime.utcnow().isoformat()
        
        return UpdateDatabaseResponseModel(
            success=True,
            message=f"Database {database_id} updated successfully",
            databaseId=database_id,
            operation="update",
            timestamp=timestamp
        )
    except VAMSGeneralErrorResponse as e:
        raise e
    except Exception as e:
        logger.exception(f"Error updating database: {e}")
        raise VAMSGeneralErrorResponse("Error updating database")

def delete_database(database_id, claims_and_roles=None):
    """Delete a database by ID"""
    try:
        if "#deleted" in database_id:
            return DeleteDatabaseResponseModel(
                message="Record not found",
                statusCode=404
            )

        # Check for active workflows, pipelines, and assets before accessing the table
        if check_workflows(database_id):
            return DeleteDatabaseResponseModel(
                message="Database contains active workflows",
                statusCode=400
            )
            
        if check_pipelines(database_id):
            return DeleteDatabaseResponseModel(
                message="Database contains active pipelines",
                statusCode=400
            )
            
        if check_assets(database_id):
            return DeleteDatabaseResponseModel(
                message="Database contains active assets",
                statusCode=400
            )

        # Only create the table reference if we've passed all the checks
        table = dynamodb.Table(db_database)

        db_response = table.get_item(
            Key={
                'databaseId': database_id
            }
        )
        database = db_response.get("Item", {})

        if database:
            allowed = False
            # Add Casbin Enforcer to check if the current user has permissions to DELETE the database
            database.update({
                "object__type": "database"
            })
            
            if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(database, "DELETE"):
                    allowed = True

            if allowed:
                logger.info(f"Deleting database: {database_id}")
                database['databaseId'] = database_id + "#deleted"
                table.put_item(Item=database)
                table.delete_item(Key={'databaseId': database_id})
                
                return DeleteDatabaseResponseModel(
                    message="Database deleted",
                    statusCode=200
                )
            else:
                return DeleteDatabaseResponseModel(
                    message="Action not allowed",
                    statusCode=403
                )
        else:
            return DeleteDatabaseResponseModel(
                message="Record not found",
                statusCode=404
            )
    except Exception as e:
        logger.exception(f"Error deleting database: {e}")
        raise VAMSGeneralErrorResponse(f"Error deleting database.")


#######################
# API Handlers
#######################

def get_database_handler(event, path_parameters, query_parameters, claims_and_roles):
    """Handler for GET /databases/{databaseId}"""
    try:
        # Validate database ID
        database_id = path_parameters.get('databaseId')
        (valid, message) = validate({
            'databaseId': {
                'value': database_id,
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)

        # Get show_deleted parameter
        show_deleted = query_parameters.get('showDeleted', 'false').lower() == 'true'
        
        # Get database
        database = get_database(database_id, show_deleted, claims_and_roles)
        if database:
            return success(body=database.dict())
        else:
            return validation_error(status_code=404, body={'message': 'Database not found'}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in get_database_handler: {e}")
        return internal_error(event=event)

def get_databases_handler(event, query_parameters, claims_and_roles):
    """Handler for GET /databases"""
    try:
        # Validate pagination parameters
        validate_pagination_info(query_parameters)
        
        # Get show_deleted parameter
        show_deleted = query_parameters.get('showDeleted', 'false').lower() == 'true'
        
        # Get databases
        databases = get_databases(query_parameters, show_deleted, claims_and_roles)
        return success(body=databases.dict())
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in get_databases_handler: {e}")
        return internal_error(event=event)

def update_database_handler(event, path_parameters, body, claims_and_roles):
    """Handler for PUT /databases/{databaseId}"""
    try:
        # Validate database ID
        database_id = path_parameters.get('databaseId')
        if not database_id:
            return validation_error(body={'message': 'No database ID in API Call'}, event=event)
            
        (valid, message) = validate({
            'databaseId': {
                'value': database_id,
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)

        # Parse and validate request body
        try:
            request_model = parse(body, model=UpdateDatabaseRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Update database
        result = update_database(database_id, request_model.dict(exclude_unset=True), claims_and_roles)
        return success(body=result.dict())
        
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in update_database_handler: {e}")
        return internal_error(event=event)

def delete_database_handler(event, path_parameters, claims_and_roles):
    """Handler for DELETE /databases/{databaseId}"""
    try:
        # Validate database ID
        database_id = path_parameters.get('databaseId')
        if not database_id:
            return validation_error(body={'message': 'No database ID in API Call'}, event=event)
            
        (valid, message) = validate({
            'databaseId': {
                'value': database_id,
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)

        # Delete database
        result = delete_database(database_id, claims_and_roles)
        return APIGatewayProxyResponseV2(
            isBase64Encoded=False,
            statusCode=result.statusCode,
            headers={
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache, no-store',
                },
            body=json.dumps({'message': result.message})
        )
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in delete_database_handler: {e}")
        return internal_error(event=event)

def get_buckets(event, query_params, claims_and_roles=None):
    """Get all S3 bucket configurations with pagination"""
    try:
        # Parse query parameters
        request_model = GetBucketsRequestModel(
            maxItems=int(query_params.get('maxItems', 10000)),
            pageSize=int(query_params.get('pageSize', 3000)),
            startingToken=query_params.get('startingToken')
        )
        
        # Build scan parameters
        scan_params = {
            'TableName': s3_asset_buckets_table,
            'Limit': request_model.pageSize
        }
        
        # Add ExclusiveStartKey if startingToken provided
        if request_model.startingToken:
            try:
                decoded_token = base64.b64decode(request_model.startingToken).decode('utf-8')
                scan_params['ExclusiveStartKey'] = json.loads(decoded_token)
            except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
                logger.exception(f"Invalid startingToken format: {e}")
                raise VAMSGeneralErrorResponse("Invalid pagination token")
        
        # Single scan call with pagination
        response = dbClient.scan(**scan_params)

        items = []
        for item in response.get('Items', []):
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
            
            # # Add object type for authorization
            # deserialized_document.update({
            #     "object__type": "bucket"
            # })
            
            # if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            #     casbin_enforcer = CasbinEnforcer(claims_and_roles)
            #     if casbin_enforcer.enforce(deserialized_document, "GET"):
            # Convert to model
            bucket_model = BucketModel(
                bucketId=deserialized_document.get('bucketId'),
                bucketName=deserialized_document.get('bucketName', ''),
                baseAssetsPrefix=deserialized_document.get('baseAssetsPrefix', '')
            )
            items.append(bucket_model)

        # Build response with nextToken
        result = GetBucketsResponseModel(Items=items)
        
        # Return LastEvaluatedKey as nextToken if present (base64 encoded)
        if 'LastEvaluatedKey' in response:
            json_str = json.dumps(response['LastEvaluatedKey'])
            result.NextToken = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        
        return result
    except Exception as e:
        logger.exception(f"Error getting buckets: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting buckets.")

def get_buckets_handler(event, query_parameters, claims_and_roles):
    """Handler for GET /buckets"""
    try:
        # Validate pagination parameters
        validate_pagination_info(query_parameters)
        
        # Get buckets
        buckets = get_buckets(query_parameters, claims_and_roles)
        return success(body=buckets.dict())
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error in get_buckets_handler: {e}")
        return internal_error(event=event)

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for database service API"""
    logger.info(event)
    
    try:
        # Get path and query parameters
        path_parameters = event.get('pathParameters', {}) or {}
        query_parameters = event.get('queryStringParameters', {}) or {}
        
        # Get HTTP method and path
        http_method = event['requestContext']['http']['method']
        path = event['requestContext']['http']['path']
        
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check if method is allowed on API
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        
        if not method_allowed_on_api:
            return authorization_error()
        
        # Route request to appropriate handler based on path and method
        if path.endswith('/database'):
            # Route: /database
            if http_method == 'GET':
                return get_databases_handler(event, query_parameters, claims_and_roles)
            else:
                return authorization_error(body={'message': 'Method not allowed for this route'})
        elif '/database/' in path and path_parameters.get('databaseId'):
            # Route: /database/{databaseId}
            if http_method == 'GET':
                return get_database_handler(event, path_parameters, query_parameters, claims_and_roles)
            elif http_method == 'PUT':
                # Parse request body for PUT
                body = event.get('body')
                if not body:
                    return validation_error(body={'message': "Request body is required"}, event=event)
                
                # Parse JSON body safely
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except json.JSONDecodeError as e:
                        logger.exception(f"Invalid JSON in request body: {e}")
                        return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
                elif isinstance(body, dict):
                    body = body
                else:
                    logger.error("Request body is not a string")
                    return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
                
                return update_database_handler(event, path_parameters, body, claims_and_roles)
            elif http_method == 'DELETE':
                return delete_database_handler(event, path_parameters, claims_and_roles)
            else:
                return authorization_error(body={'message': 'Method not allowed for this route'})
        elif path.endswith('/buckets'):
            # Route: /buckets
            if http_method == 'GET':
                return get_buckets_handler(event, query_parameters, claims_and_roles)
            else:
                return authorization_error(body={'message': 'Method not allowed for this route'})
        else:
            # Not a route handled by this function
            logger.error(f"Unsupported route: {http_method} {path}")
            return validation_error(status_code=404, body={'message': 'Route not found'}, event=event)
            
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return internal_error(event=event)
