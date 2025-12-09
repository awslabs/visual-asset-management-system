# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tag API models for VAMS."""

from typing import List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="TagModels")

######################## Tag API Models ##########################

class GetTagsRequestModel(BaseModel, extra='ignore'):
    """Request model for listing tags"""
    maxItems: Optional[int] = Field(default=1000, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=1000, ge=1, le=1000)
    startingToken: Optional[str] = None

class CreateTagRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new tag"""
    tagName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    tagTypeName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    
    @root_validator
    def validate_fields(cls, values):
        """Validate tag fields using common validators"""
        logger.info("Validating tag creation parameters")
        
        (valid, message) = validate({
            'tagName': {
                'value': values.get('tagName'),
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
            'tagTypeName': {
                'value': values.get('tagTypeName'),
                'validator': 'OBJECT_NAME'
            }
        })
        
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values

class UpdateTagRequestModel(BaseModel, extra='ignore'):
    """Request model for updating an existing tag"""
    tagName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    tagTypeName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    
    @root_validator
    def validate_fields(cls, values):
        """Validate tag update fields using common validators"""
        logger.info("Validating tag update parameters")
        
        (valid, message) = validate({
            'tagName': {
                'value': values.get('tagName'),
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
            'tagTypeName': {
                'value': values.get('tagTypeName'),
                'validator': 'OBJECT_NAME'
            }
        })
        
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values

class DeleteTagRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting a tag"""
    confirmDelete: Optional[bool] = Field(default=False)
    
class TagResponseModel(BaseModel, extra='ignore'):
    """Response model for tag data"""
    tagName: str
    description: str
    tagTypeName: str
    required: Optional[str] = "False"  # From tag type, indicates if tag is required

class TagOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for tag operations (create, update, delete)"""
    success: bool
    message: str
    tagName: str
    operation: Literal["create", "update", "delete"]
    timestamp: str

######################## Tag Type API Models ##########################

class GetTagTypesRequestModel(BaseModel, extra='ignore'):
    """Request model for listing tag types"""
    maxItems: Optional[int] = Field(default=1000, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=1000, ge=1, le=1000)
    startingToken: Optional[str] = None

class CreateTagTypeRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new tag type"""
    tagTypeName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    required: Optional[str] = Field(default="False", pattern="^(True|False)$")
    
    @root_validator
    def validate_fields(cls, values):
        """Validate tag type fields using common validators"""
        logger.info("Validating tag type creation parameters")
        
        (valid, message) = validate({
            'tagTypeName': {
                'value': values.get('tagTypeName'),
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
            'required': {
                'value': values.get('required', 'False'),
                'validator': 'BOOL',
                'optional': True
            }
        })
        
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values

class UpdateTagTypeRequestModel(BaseModel, extra='ignore'):
    """Request model for updating an existing tag type"""
    tagTypeName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    required: Optional[str] = Field(default="False", pattern="^(True|False)$")
    
    @root_validator
    def validate_fields(cls, values):
        """Validate tag type update fields using common validators"""
        logger.info("Validating tag type update parameters")
        
        (valid, message) = validate({
            'tagTypeName': {
                'value': values.get('tagTypeName'),
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
            'required': {
                'value': values.get('required', 'False'),
                'validator': 'BOOL',
                'optional': True
            }
        })
        
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values

class DeleteTagTypeRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting a tag type"""
    confirmDelete: Optional[bool] = Field(default=False)

class TagTypeResponseModel(BaseModel, extra='ignore'):
    """Response model for tag type data"""
    tagTypeName: str
    description: str
    required: str = "False"
    tags: Optional[List[str]] = []  # Associated tags

class TagTypeOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for tag type operations (create, update, delete)"""
    success: bool
    message: str
    tagTypeName: str
    operation: Literal["create", "update", "delete"]
    timestamp: str
