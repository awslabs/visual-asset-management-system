# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import List, Optional, Dict, Any, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate, id_pattern, object_name_pattern, uuid_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="DatabaseModels")

######################## Create Database API Models ##########################
class CreateDatabaseRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new database"""
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    defaultBucketId: str = Field(pattern=uuid_pattern)
    restrictMetadataOutsideSchemas: Optional[bool] = False
    restrictFileUploadsToExtensions: Optional[str] = ""

    @root_validator
    def validate_fields(cls, values):
        """Validate fields that require more scrutiny"""
        logger.info("Validating custom parameters")
        validation_dict = {
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
            'defaultBucketId': {
                'value': values.get('defaultBucketId'),
                'validator': 'UUID'
            }
        }
        
        # Validate restrictFileUploadsToExtensions if provided
        if values.get('restrictFileUploadsToExtensions'):
            validation_dict['restrictFileUploadsToExtensions'] = {
                'value': values.get('restrictFileUploadsToExtensions'),
                'validator': 'STRING_256',
                'optional': True
            }
            
            # Additional validation for extension list format
            extensions_str = values.get('restrictFileUploadsToExtensions').strip()
            if extensions_str:  # Only validate if not empty
                # Split by comma and validate each extension
                extensions = [ext.strip() for ext in extensions_str.split(',')]
                for ext in extensions:
                    if not ext:
                        raise ValueError("Extension list contains empty values")
                    if ext.lower() == ".all":
                        continue  # .all is a special bypass value
                    if not ext.startswith('.'):
                        raise ValueError(f"Extension '{ext}' must start with a dot (e.g., '.pdf')")
                    if len(ext) < 2:
                        raise ValueError(f"Extension '{ext}' is too short")
                    # Check for valid characters (alphanumeric and dot only)
                    if not all(c.isalnum() or c == '.' for c in ext):
                        raise ValueError(f"Extension '{ext}' contains invalid characters")
            
        (valid, message) = validate(validation_dict)
        if not valid:
            logger.error(message)
            raise ValueError(message)
        return values

class CreateDatabaseResponseModel(BaseModel, extra='ignore'):
    """Response model for creating a new database"""
    databaseId: str
    message: str

######################## Database Service API Models ##########################
class GetDatabaseResponseModel(BaseModel, extra='ignore'):
    """Response model for getting a single database"""
    databaseId: str
    description: str
    dateCreated: Optional[str] = None
    assetCount: Optional[int] = None
    defaultBucketId: Optional[str] = None
    bucketName: Optional[str] = None  # Bucket name from S3 asset buckets table
    baseAssetsPrefix: Optional[str] = None  # Base prefix from S3 asset buckets table
    restrictMetadataOutsideSchemas: Optional[bool] = False
    restrictFileUploadsToExtensions: Optional[str] = ""

class GetDatabasesRequestModel(BaseModel, extra='ignore'):
    """Request model for listing databases"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None
    showDeleted: Optional[bool] = False

class GetDatabasesResponseModel(BaseModel, extra='ignore'):
    """Response model for listing databases"""
    Items: List[GetDatabaseResponseModel]
    NextToken: Optional[str] = None

class UpdateDatabaseRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a database"""
    description: Optional[str] = Field(None, min_length=4, max_length=256, strip_whitespace=True)
    defaultBucketId: Optional[str] = Field(None, pattern=uuid_pattern)
    restrictMetadataOutsideSchemas: Optional[bool] = None
    restrictFileUploadsToExtensions: Optional[str] = None

    @root_validator
    def validate_fields(cls, values):
        """Validate fields that require more scrutiny"""
        logger.info("Validating update parameters")
        
        # Ensure at least one field is provided for update
        if not any(values.get(field) is not None for field in ['description', 'defaultBucketId', 'restrictMetadataOutsideSchemas', 'restrictFileUploadsToExtensions']):
            raise ValueError("At least one field must be provided for update")
        
        validation_dict = {}
        
        # Validate description if provided
        if values.get('description') is not None:
            validation_dict['description'] = {
                'value': values.get('description'),
                'validator': 'STRING_256',
                'optional': True
            }
        
        # Validate defaultBucketId if provided
        if values.get('defaultBucketId') is not None:
            validation_dict['defaultBucketId'] = {
                'value': values.get('defaultBucketId'),
                'validator': 'UUID',
                'optional': True
            }
        
        # Validate restrictFileUploadsToExtensions if provided
        if values.get('restrictFileUploadsToExtensions') is not None:
            validation_dict['restrictFileUploadsToExtensions'] = {
                'value': values.get('restrictFileUploadsToExtensions'),
                'validator': 'STRING_256',
                'optional': True
            }
            
            # Additional validation for extension list format
            extensions_str = values.get('restrictFileUploadsToExtensions')
            if extensions_str and extensions_str.strip():  # Only validate if not empty
                # Split by comma and validate each extension
                extensions = [ext.strip() for ext in extensions_str.split(',')]
                for ext in extensions:
                    if not ext:
                        raise ValueError("Extension list contains empty values")
                    if ext.lower() == ".all":
                        continue  # .all is a special bypass value
                    if not ext.startswith('.'):
                        raise ValueError(f"Extension '{ext}' must start with a dot (e.g., '.pdf')")
                    if len(ext) < 2:
                        raise ValueError(f"Extension '{ext}' is too short")
                    # Check for valid characters (alphanumeric and dot only)
                    if not all(c.isalnum() or c == '.' for c in ext):
                        raise ValueError(f"Extension '{ext}' contains invalid characters")
        
        if validation_dict:
            (valid, message) = validate(validation_dict)
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        return values

class UpdateDatabaseResponseModel(BaseModel, extra='ignore'):
    """Response model for updating a database"""
    success: bool
    message: str
    databaseId: str
    operation: Literal["update"]
    timestamp: str

class DeleteDatabaseResponseModel(BaseModel, extra='ignore'):
    """Response model for deleting a database"""
    message: str
    statusCode: int

######################## Bucket API Models ##########################
class BucketModel(BaseModel, extra='ignore'):
    """Model for S3 bucket configuration"""
    bucketId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    bucketName: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    baseAssetsPrefix: str = Field(min_length=0, max_length=256, strip_whitespace=True)

class GetBucketsRequestModel(BaseModel, extra='ignore'):
    """Request model for listing buckets"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None

class GetBucketsResponseModel(BaseModel, extra='ignore'):
    """Response model for listing buckets"""
    Items: List[BucketModel]
    NextToken: Optional[str] = None
