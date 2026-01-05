# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metadata Schema service handler for VAMS API - V2 implementation."""

import os
import boto3
import json
import base64
import uuid
from datetime import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.metadataSchema import (
    GetMetadataSchemaRequestModel, GetMetadataSchemasRequestModel,
    CreateMetadataSchemaRequestModel, UpdateMetadataSchemaRequestModel,
    DeleteMetadataSchemaRequestModel, MetadataSchemaResponseModel,
    MetadataSchemaOperationResponseModel, GetMetadataSchemasResponseModel,
    MetadataSchemaEntityType, MetadataSchemaFieldsModel
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="MetadataSchemaService")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables
try:
    metadata_schema_table_name = os.environ["METADATA_SCHEMA_STORAGE_TABLE_V2_NAME"]
    database_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
metadata_schema_table = dynamodb.Table(metadata_schema_table_name)
database_table = dynamodb.Table(database_table_name)

#######################
# Utility Functions
#######################

def verify_database_exists(databaseId):
    """Verify that a database exists before schema operations
    
    Args:
        databaseId: The database ID to verify
        
    Raises:
        VAMSGeneralErrorResponse: If database does not exist
    """
    # Skip verification for GLOBAL database
    if databaseId == "GLOBAL":
        return True
    
    try:
        response = database_table.get_item(Key={'databaseId': databaseId})
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Database does not exist")
        return True
    except Exception as e:
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        logger.exception(f"Error verifying database: {e}")
        raise VAMSGeneralErrorResponse("Error verifying database")

#######################
# Business Logic Functions
#######################

def get_metadata_schema_details(metadataSchemaId):
    """Get metadata schema details from DynamoDB
    
    Args:
        metadataSchemaId: The metadata schema ID
        
    Returns:
        The metadata schema details or None if not found
    """
    try:
        # Query using the primary key (metadataSchemaId)
        response = metadata_schema_table.query(
            KeyConditionExpression=Key('metadataSchemaId').eq(metadataSchemaId),
            Limit=1
        )
        
        items = response.get('Items', [])
        if items:
            return items[0]
        return None
    except Exception as e:
        logger.exception(f"Error getting metadata schema details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata schema")

def get_metadata_schemas_by_database_and_type(databaseId, metadataEntityType, query_params):
    """Get metadata schemas for a specific database and entity type
    
    Args:
        databaseId: The database ID
        metadataEntityType: The entity type
        query_params: Query parameters for pagination
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    try:
        # Build composite key for query
        composite_key = f"{databaseId}:{metadataEntityType}"
        
        # Build query parameters
        query_params_dict = {
            'TableName': metadata_schema_table_name,
            'IndexName': 'DatabaseIdMetadataEntityTypeIndex',
            'KeyConditionExpression': '#pk = :pkValue',
            'ExpressionAttributeNames': {
                '#pk': 'databaseId:metadataEntityType'
            },
            'ExpressionAttributeValues': {
                ':pkValue': {'S': composite_key}
            },
            'ScanIndexForward': False,
            'Limit': int(query_params['pageSize'])
        }
        
        # Add ExclusiveStartKey if startingToken provided
        if query_params.get('startingToken'):
            try:
                decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
                query_params_dict['ExclusiveStartKey'] = json.loads(decoded_token)
            except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
                logger.exception(f"Invalid startingToken format: {e}")
                raise VAMSGeneralErrorResponse("Invalid pagination token")
        
        # Single query call with pagination
        response = dynamodb_client.query(**query_params_dict)
        
        # Process items with authorization filtering
        authorized_items = []
        deserializer = TypeDeserializer()
        for item in response.get('Items', []):
            # Deserialize the item
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            
            # Parse fields JSON if it's a string
            if 'fields' in deserialized_item and isinstance(deserialized_item['fields'], str):
                try:
                    deserialized_item['fields'] = json.loads(deserialized_item['fields'])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse fields JSON for schema {deserialized_item.get('metadataSchemaId')}")
            
            # Add object type for Casbin enforcement
            deserialized_item.update({
                "object__type": "metadataSchema",
                "metadataSchemaName": deserialized_item.get('schemaName', ''),
                "metadataSchemaEntityType": deserialized_item.get('metadataSchemaEntityType', '')
            })
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_item, "GET"):
                    authorized_items.append(deserialized_item)
        
        # Build response with nextToken
        result = {"Items": authorized_items}
        
        # Return LastEvaluatedKey as nextToken if present (base64 encoded)
        if 'LastEvaluatedKey' in response:
            json_str = json.dumps(response['LastEvaluatedKey'])
            result["NextToken"] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        
        return result
        
    except Exception as e:
        logger.exception(f"Error querying metadata schemas: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata schemas")

def get_metadata_schemas_by_database(databaseId, query_params):
    """Get all metadata schemas for a specific database
    
    Args:
        databaseId: The database ID
        query_params: Query parameters for pagination
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    try:
        # Build query parameters using DatabaseIdIndex GSI
        query_params_dict = {
            'TableName': metadata_schema_table_name,
            'IndexName': 'DatabaseIdIndex',
            'KeyConditionExpression': 'databaseId = :dbId',
            'ExpressionAttributeValues': {
                ':dbId': {'S': databaseId}
            },
            'ScanIndexForward': False,
            'Limit': int(query_params['pageSize'])
        }
        
        # Add ExclusiveStartKey if startingToken provided
        if query_params.get('startingToken'):
            try:
                decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
                query_params_dict['ExclusiveStartKey'] = json.loads(decoded_token)
            except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
                logger.exception(f"Invalid startingToken format: {e}")
                raise VAMSGeneralErrorResponse("Invalid pagination token")
        
        # Single query call with pagination
        response = dynamodb_client.query(**query_params_dict)
        
        # Process items with authorization filtering
        authorized_items = []
        deserializer = TypeDeserializer()
        for item in response.get('Items', []):
            # Deserialize the item
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            
            # Parse fields JSON if it's a string
            if 'fields' in deserialized_item and isinstance(deserialized_item['fields'], str):
                try:
                    deserialized_item['fields'] = json.loads(deserialized_item['fields'])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse fields JSON for schema {deserialized_item.get('metadataSchemaId')}")
            
            # Add object type for Casbin enforcement
            deserialized_item.update({
                "object__type": "metadataSchema",
                "metadataSchemaName": deserialized_item.get('schemaName', ''),
                "metadataSchemaEntityType": deserialized_item.get('metadataSchemaEntityType', '')
            })
            
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_item, "GET"):
                    authorized_items.append(deserialized_item)
        
        # Build response with nextToken
        result = {"Items": authorized_items}
        
        # Return LastEvaluatedKey as nextToken if present (base64 encoded)
        if 'LastEvaluatedKey' in response:
            json_str = json.dumps(response['LastEvaluatedKey'])
            result["NextToken"] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        
        return result
        
    except Exception as e:
        logger.exception(f"Error querying metadata schemas by database: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata schemas")

def get_all_metadata_schemas(query_params):
    """Get all metadata schemas across all databases
    
    Args:
        query_params: Query parameters for pagination
        
    Returns:
        Dictionary with Items and optional NextToken
    """
    deserializer = TypeDeserializer()
    
    try:
        # Build scan parameters
        scan_params = {
            'TableName': metadata_schema_table_name,
            'Limit': int(query_params['pageSize'])
        }
        
        # Add ExclusiveStartKey if startingToken provided
        if query_params.get('startingToken'):
            try:
                decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
                scan_params['ExclusiveStartKey'] = json.loads(decoded_token)
            except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
                logger.exception(f"Invalid startingToken format: {e}")
                raise VAMSGeneralErrorResponse("Invalid pagination token")
        
        # Single scan call with pagination
        response = dynamodb_client.scan(**scan_params)
        
        # Process results
        items = []
        
        for item in response.get('Items', []):
            # Deserialize the DynamoDB item
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
            
            # Parse fields JSON if it's a string
            if 'fields' in deserialized_document and isinstance(deserialized_document['fields'], str):
                try:
                    deserialized_document['fields'] = json.loads(deserialized_document['fields'])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse fields JSON for schema {deserialized_document.get('metadataSchemaId')}")
            
            # Add object type for Casbin enforcement
            deserialized_document.update({
                "object__type": "metadataSchema",
                "metadataSchemaName": deserialized_document.get('schemaName', ''),
                "metadataSchemaEntityType": deserialized_document.get('metadataSchemaEntityType', '')
            })
            
            # Check if user has permission to GET the schema
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(deserialized_document, "GET"):
                    items.append(deserialized_document)
        
        # Build response with nextToken
        result = {'Items': items}
        
        # Return LastEvaluatedKey as nextToken if present (base64 encoded)
        if 'LastEvaluatedKey' in response:
            json_str = json.dumps(response['LastEvaluatedKey'])
            result['NextToken'] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
            
        return result
        
    except Exception as e:
        logger.exception(f"Error scanning all metadata schemas: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving all metadata schemas")

def create_metadata_schema(schema_data, claims_and_roles):
    """Create a new metadata schema
    
    Args:
        schema_data: Dictionary with schema creation data
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        Created schema operation response
    """
    try:
        # Verify database exists
        verify_database_exists(schema_data['databaseId'])
        
        # Check authorization
        auth_object = {
            "databaseId": schema_data['databaseId'],
            "metadataSchemaEntityType": schema_data['metadataSchemaEntityType'],
            "metadataSchemaName": schema_data['schemaName'],
            "object__type": "metadataSchema"
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(auth_object, "POST"):
                raise authorization_error()
        
        # Generate unique ID
        metadata_schema_id = str(uuid.uuid4())
        
        # Create composite sort key - use .value to get the enum's string value
        composite_key = f"{schema_data['databaseId']}:{schema_data['metadataSchemaEntityType'].value}"
        
        # Add metadata
        now = datetime.utcnow().isoformat()
        username = claims_and_roles.get("tokens", ["system"])[0]
        
        # Convert fields to JSON string for storage
        fields_json = json.dumps(schema_data['fields'])
        
        # Build schema item
        schema_item = {
            'metadataSchemaId': metadata_schema_id,
            'databaseId:metadataEntityType': composite_key,
            'databaseId': schema_data['databaseId'],
            'metadataSchemaEntityType': schema_data['metadataSchemaEntityType'].value,  # Store enum value as string
            'schemaName': schema_data['schemaName'],
            'fields': fields_json,
            'enabled': schema_data.get('enabled', True),
            'dateCreated': now,
            'dateModified': now,
            'createdBy': username,
            'modifiedBy': username
        }
        
        # Add optional fileKeyTypeRestriction
        if 'fileKeyTypeRestriction' in schema_data and schema_data['fileKeyTypeRestriction']:
            schema_item['fileKeyTypeRestriction'] = schema_data['fileKeyTypeRestriction']
        
        # Save to database
        metadata_schema_table.put_item(Item=schema_item)
        
        logger.info(f"Created metadata schema {metadata_schema_id} for database {schema_data['databaseId']}")
        
        # Return success response
        return MetadataSchemaOperationResponseModel(
            success=True,
            message=f"Metadata schema '{schema_data['schemaName']}' created successfully",
            metadataSchemaId=metadata_schema_id,
            operation="create",
            timestamp=now
        )
    except Exception as e:
        if isinstance(e, (VAMSGeneralErrorResponse, ValidationError)):
            raise e
        logger.exception(f"Error creating metadata schema: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata schema")

def update_metadata_schema(metadataSchemaId, update_data, claims_and_roles):
    """Update an existing metadata schema
    
    Args:
        metadataSchemaId: The metadata schema ID
        update_data: Dictionary with fields to update
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        Updated schema operation response
    """
    try:
        # Get the existing schema
        schema = get_metadata_schema_details(metadataSchemaId)
        if not schema:
            raise VAMSGeneralErrorResponse("Metadata schema not found")
        
        # Check authorization
        auth_object = {
            "databaseId": schema['databaseId'],
            "metadataSchemaEntityType": schema['metadataSchemaEntityType'],
            "metadataSchemaName": schema.get('schemaName', ''),
            "object__type": "metadataSchema"
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(auth_object, "POST"):  # Uses POST permission for updates
                raise authorization_error()
        
        # Update the fields
        logger.info(f"Updating metadata schema {metadataSchemaId}")
        
        # Update only the editable fields
        if 'schemaName' in update_data:
            schema['schemaName'] = update_data['schemaName']
        
        if 'fileKeyTypeRestriction' in update_data:
            if update_data['fileKeyTypeRestriction']:
                schema['fileKeyTypeRestriction'] = update_data['fileKeyTypeRestriction']
            else:
                # Remove the field if set to None or empty
                schema.pop('fileKeyTypeRestriction', None)
        
        if 'fields' in update_data:
            # Convert fields to JSON string for storage
            schema['fields'] = json.dumps(update_data['fields'])
        
        if 'enabled' in update_data:
            schema['enabled'] = update_data['enabled']
        
        # Update metadata
        now = datetime.utcnow().isoformat()
        username = claims_and_roles.get("tokens", ["system"])[0]
        schema['dateModified'] = now
        schema['modifiedBy'] = username
        
        # Save the updated schema
        metadata_schema_table.put_item(Item=schema)
        
        # Return success response
        return MetadataSchemaOperationResponseModel(
            success=True,
            message="Metadata schema updated successfully",
            metadataSchemaId=metadataSchemaId,
            operation="update",
            timestamp=now
        )
    except Exception as e:
        if isinstance(e, (VAMSGeneralErrorResponse, ValidationError)):
            raise e
        logger.exception(f"Error updating metadata schema: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata schema")

def delete_metadata_schema(metadataSchemaId, claims_and_roles):
    """Delete a metadata schema
    
    Args:
        metadataSchemaId: The metadata schema ID
        claims_and_roles: User claims and roles for authorization
        
    Returns:
        Delete operation response
    """
    try:
        # Get the existing schema
        schema = get_metadata_schema_details(metadataSchemaId)
        if not schema:
            raise VAMSGeneralErrorResponse("Metadata schema not found")
        
        # Check authorization
        auth_object = {
            "databaseId": schema['databaseId'],
            "metadataSchemaEntityType": schema['metadataSchemaEntityType'],
            "metadataSchemaName": schema.get('schemaName', ''),
            "object__type": "metadataSchema"
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(auth_object, "DELETE"):
                raise authorization_error()
        
        # Delete the schema
        logger.info(f"Deleting metadata schema {metadataSchemaId}")
        
        # Get the composite key for deletion
        composite_key = schema.get('databaseId:metadataEntityType')
        
        metadata_schema_table.delete_item(
            Key={
                'metadataSchemaId': metadataSchemaId,
                'databaseId:metadataEntityType': composite_key
            }
        )
        
        # Return success response
        now = datetime.utcnow().isoformat()
        return MetadataSchemaOperationResponseModel(
            success=True,
            message=f"Metadata schema deleted successfully",
            metadataSchemaId=metadataSchemaId,
            operation="delete",
            timestamp=now
        )
    except Exception as e:
        if isinstance(e, (VAMSGeneralErrorResponse, ValidationError)):
            raise e
        logger.exception(f"Error deleting metadata schema: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata schema")

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for metadata schemas
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Case 1: Get a specific metadata schema by ID
        if 'metadataSchemaId' in path_parameters and 'databaseId' in path_parameters:
            logger.info(f"Getting metadata schema {path_parameters['metadataSchemaId']}")
            
            # Validate parameters
            (valid, message) = validate({
                'databaseId': {
                    'value': path_parameters['databaseId'],
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                },
                'metadataSchemaId': {
                    'value': path_parameters['metadataSchemaId'],
                    'validator': 'ID'
                },
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message})
            
            # Get the schema
            schema = get_metadata_schema_details(path_parameters['metadataSchemaId'])
            
            # Check if schema exists and user has permission
            if schema:
                # Parse fields JSON if it's a string
                if 'fields' in schema and isinstance(schema['fields'], str):
                    try:
                        schema['fields'] = json.loads(schema['fields'])
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse fields JSON for schema {schema.get('metadataSchemaId')}")
                
                # Create a copy for authorization check (don't modify original schema)
                auth_schema = {
                    "databaseId": schema.get('databaseId'),
                    "object__type": "metadataSchema",
                    "metadataSchemaName": schema.get('schemaName', ''),
                    "metadataSchemaEntityType": schema.get('metadataSchemaEntityType', '')
                }
                
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if not casbin_enforcer.enforce(auth_schema, "GET"):
                        return authorization_error()
                
                # Convert to response model
                try:
                    response_model = MetadataSchemaResponseModel(**schema)
                    return success(body=response_model.dict())
                except ValidationError as v:
                    logger.exception(f"Error converting schema to response model: {v}")
                    return success(body={"message": schema})
            else:
                return general_error(body={"message": "Metadata schema not found"}, status_code=404)
        
        # Case 2: List metadata schemas with filters
        else:
            logger.info("Listing metadata schemas")
            
            # Parse and validate query parameters
            try:
                request_model = parse(query_parameters, model=GetMetadataSchemasRequestModel)
                query_params = {
                    'maxItems': request_model.maxItems,
                    'pageSize': request_model.pageSize,
                    'startingToken': request_model.startingToken
                }
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                return validation_error(body={'message': str(v)})
            
            # Determine which query to use based on filters
            if request_model.databaseId and request_model.metadataEntityType:
                # Query by database and entity type
                schemas_result = get_metadata_schemas_by_database_and_type(
                    request_model.databaseId,
                    request_model.metadataEntityType.value,
                    query_params
                )
            elif request_model.databaseId:
                # Query by database only
                schemas_result = get_metadata_schemas_by_database(
                    request_model.databaseId,
                    query_params
                )
            else:
                # Get all schemas
                schemas_result = get_all_metadata_schemas(query_params)
            
            # Convert items to response models
            formatted_items = []
            for item in schemas_result.get('Items', []):
                try:
                    schema_model = MetadataSchemaResponseModel(**item)
                    formatted_items.append(schema_model.dict())
                except ValidationError:
                    # Fall back to raw item if conversion fails
                    formatted_items.append(item)
            
            # Build response
            response = {
                "Items": formatted_items
            }
            if 'NextToken' in schemas_result:
                response['NextToken'] = schemas_result['NextToken']
            
            return success(body=response)
            
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()

def handle_post_request(event):
    """Handle POST requests to create metadata schema
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
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
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateMetadataSchemaRequestModel)
        
        # Create the schema
        result = create_metadata_schema(
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error()

def handle_put_request(event):
    """Handle PUT requests to update metadata schema
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
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
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse and validate the request model
        update_model = parse(body, model=UpdateMetadataSchemaRequestModel)
        
        # Update the schema
        result = update_metadata_schema(
            update_model.metadataSchemaId,
            update_model.dict(exclude_unset=True),
            claims_and_roles
        )
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error()

def handle_delete_request(event):
    """Handle DELETE requests for metadata schemas
    
    Args:
        event: API Gateway event
        
    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    
    # Validate required path parameters
    if 'databaseId' not in path_parameters:
        return validation_error(body={'message': "No database ID in API Call"})
    
    if 'metadataSchemaId' not in path_parameters:
        return validation_error(body={'message': "No metadata schema ID in API Call"})
    
    # Validate path parameters
    (valid, message) = validate({
        'databaseId': {
            'value': path_parameters['databaseId'],
            'validator': 'ID',
            'allowGlobalKeyword': True
        },
        'metadataSchemaId': {
            'value': path_parameters['metadataSchemaId'],
            'validator': 'ID'
        },
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message})
    
    try:
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
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse and validate the request model
        delete_model = parse(body, model=DeleteMetadataSchemaRequestModel)
        
        # Delete the schema
        result = delete_metadata_schema(
            path_parameters['metadataSchemaId'],
            claims_and_roles
        )
        
        # Return success response
        return success(body=result.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error()

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for metadata schema service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        
        if not method_allowed_on_api:
            return authorization_error()
        
        # Route to appropriate handler
        if method == 'GET':
            return handle_get_request(event)
        elif method == 'POST':
            return handle_post_request(event)
        elif method == 'PUT':
            return handle_put_request(event)
        elif method == 'DELETE':
            return handle_delete_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"})
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()