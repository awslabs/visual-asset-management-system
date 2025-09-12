# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import List, Optional, Dict, Any
from pydantic import Field, Extra
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate, id_pattern, object_name_pattern, uuid_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="DatabaseModels")

######################## Create Database API Models ##########################
class CreateDatabaseRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for creating a new database"""
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    defaultBucketId: str = Field(pattern=uuid_pattern)

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
            
        (valid, message) = validate(validation_dict)
        if not valid:
            logger.error(message)
            raise ValueError(message)
        return values

class CreateDatabaseResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for creating a new database"""
    databaseId: str
    message: str

######################## Database Service API Models ##########################
class GetDatabaseResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for getting a single database"""
    databaseId: str
    description: str
    dateCreated: Optional[str] = None
    assetCount: Optional[int] = None
    defaultBucketId: Optional[str] = None
    bucketName: Optional[str] = None  # Bucket name from S3 asset buckets table
    baseAssetsPrefix: Optional[str] = None  # Base prefix from S3 asset buckets table

class GetDatabasesRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for listing databases"""
    maxItems: Optional[int] = Field(default=1000, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=1000, ge=1, le=1000)
    startingToken: Optional[str] = None
    showDeleted: Optional[bool] = False

class GetDatabasesResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for listing databases"""
    Items: List[GetDatabaseResponseModel]
    NextToken: Optional[str] = None

class DeleteDatabaseResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for deleting a database"""
    message: str
    statusCode: int

######################## Bucket API Models ##########################
class BucketModel(BaseModel, extra=Extra.ignore):
    """Model for S3 bucket configuration"""
    bucketId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    bucketName: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    baseAssetsPrefix: str = Field(min_length=0, max_length=256, strip_whitespace=True)

class GetBucketsRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for listing buckets"""
    maxItems: Optional[int] = Field(default=1000, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=1000, ge=1, le=1000)
    startingToken: Optional[str] = None

class GetBucketsResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for listing buckets"""
    Items: List[BucketModel]
    NextToken: Optional[str] = None
