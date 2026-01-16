# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metadata Schema models for VAMS - V2 implementation with support for multiple entity types."""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field, validator, root_validator
from enum import Enum
import json
from customLogging.logger import safeLogger
from models.metadata import MetadataValueType, validate_metadata_value_common

logger = safeLogger(service_name="MetadataSchemaModels")

#######################
# Metadata Schema Entity Types
#######################

class MetadataSchemaEntityType(str, Enum):
    """Supported metadata schema entity types"""
    DATABASE_METADATA = "databaseMetadata"
    ASSET_METADATA = "assetMetadata"
    FILE_METADATA = "fileMetadata"
    FILE_ATTRIBUTE = "fileAttribute"
    ASSET_LINK_METADATA = "assetLinkMetadata"


#######################
# Metadata Schema Field Models
#######################

class MetadataSchemaFieldModel(BaseModel, extra='ignore'):
    """Single field definition within a metadata schema"""
    metadataFieldKeyName: str = Field(..., min_length=1, max_length=256, description="Field key name")
    metadataFieldValueType: MetadataValueType = Field(..., description="Type of metadata value")
    required: bool = Field(default=False, description="Whether this field is required")
    sequence: Optional[int] = Field(None, ge=0, description="Display order sequence (0-based, lower numbers appear first)")
    dependsOnFieldKeyName: Optional[List[str]] = Field(None, description="Field keys this field depends on")
    controlledListKeys: Optional[List[str]] = Field(None, description="Allowed values for INLINE_CONTROLLED_LIST type")
    defaultMetadataFieldValue: Optional[str] = Field(None, description="Default value for this field")

    @validator('metadataFieldValueType', pre=True)
    def normalize_metadata_value_type(cls, v):
        """Convert metadataFieldValueType to lowercase"""
        if isinstance(v, str):
            return v.lower()
        return v

    @root_validator
    def validate_field_definition(cls, values):
        """Validate field definition constraints"""
        
        # Validate controlledListKeys only for INLINE_CONTROLLED_LIST type
        if values.get('metadataFieldValueType') == MetadataValueType.INLINE_CONTROLLED_LIST:
            if not values.get('controlledListKeys') or len(values.get('controlledListKeys', [])) == 0:
                raise ValueError("controlledListKeys is required for INLINE_CONTROLLED_LIST type")
        else:
            # For non-controlled list types, controlledListKeys should not be set
            if values.get('controlledListKeys') is not None and len(values.get('controlledListKeys', [])) > 0:
                raise ValueError(f"controlledListKeys should only be set for INLINE_CONTROLLED_LIST type, not {values.get('metadataFieldValueType')}")
        
        # Validate defaultMetadataFieldValue if provided
        if values.get('defaultMetadataFieldValue') is not None:
            try:
                # Use the common validation function from metadata.py
                validate_metadata_value_common(values.get('defaultMetadataFieldValue'), values.get('metadataFieldValueType'))
                
                # Additional validation for controlled list
                if values.get('metadataFieldValueType') == MetadataValueType.INLINE_CONTROLLED_LIST:
                    if values.get('controlledListKeys') and values.get('defaultMetadataFieldValue') not in values.get('controlledListKeys'):
                        raise ValueError(f"defaultMetadataFieldValue '{values.get('defaultMetadataFieldValue')}' must be one of the controlledListKeys: {values.get('controlledListKeys')}")
            except ValueError as e:
                raise ValueError(f"Invalid defaultMetadataFieldValue: {str(e)}")
        
        return values


class MetadataSchemaFieldsModel(BaseModel, extra='ignore'):
    """Container for metadata schema fields"""
    fields: List[MetadataSchemaFieldModel] = Field(..., description="Array of field definitions")

    @root_validator
    def validate_unique_field_names(cls, values):
        """Ensure all field names are unique"""
        # Validate min_length constraint
        if not values.get('fields') or len(values.get('fields', [])) < 1:
            raise ValueError("fields must contain at least 1 item")
        
        field_names = [field.metadataFieldKeyName for field in values.get('fields', [])]
        if len(field_names) != len(set(field_names)):
            duplicates = [name for name in field_names if field_names.count(name) > 1]
            raise ValueError(f"Duplicate field names found: {list(set(duplicates))}")
        return values


#######################
# Request Models
#######################

class GetMetadataSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for getting a single metadata schema"""
    pass  # No query parameters needed for single schema retrieval


class GetMetadataSchemasRequestModel(BaseModel, extra='ignore'):
    """Request model for listing metadata schemas with filters"""
    databaseId: Optional[str] = Field(None, description="Filter by database ID")
    metadataEntityType: Optional[MetadataSchemaEntityType] = Field(None, description="Filter by entity type")
    maxItems: Optional[int] = Field(default=30000, ge=1, description="Maximum items to return")
    pageSize: Optional[int] = Field(default=3000, ge=1, description="Page size for pagination")
    startingToken: Optional[str] = Field(None, description="Token for pagination")

    @validator('metadataEntityType', pre=True)
    def normalize_entity_type(cls, v):
        """Accept case-insensitive input and map to correct camelCase enum value"""
        if v is None:
            return v
        if isinstance(v, str):
            # Map lowercase input to the correct camelCase enum value
            lowercase_map = {
                'databasemetadata': MetadataSchemaEntityType.DATABASE_METADATA,
                'assetmetadata': MetadataSchemaEntityType.ASSET_METADATA,
                'filemetadata': MetadataSchemaEntityType.FILE_METADATA,
                'fileattribute': MetadataSchemaEntityType.FILE_ATTRIBUTE,
                'assetlinkmetadata': MetadataSchemaEntityType.ASSET_LINK_METADATA,
            }
            # Try lowercase lookup first
            lower_v = v.lower()
            if lower_v in lowercase_map:
                return lowercase_map[lower_v].value
            # If not found in map, return as-is to let enum validation handle it
            return v
        return v

    @root_validator
    def validate_fields(cls, values):
        """Validate databaseId if provided"""
        if values.get('databaseId'):
            from common.validators import validate
            (valid, message) = validate({
                'databaseId': {
                    'value': values.get('databaseId'),
                    'validator': 'ID',
                    'allowGlobalKeyword': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        return values


class CreateMetadataSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a metadata schema"""
    databaseId: str = Field(..., min_length=1, max_length=256, description="Database ID (supports 'GLOBAL')")
    metadataSchemaEntityType: MetadataSchemaEntityType = Field(..., description="Entity type for this schema")
    schemaName: str = Field(..., min_length=1, max_length=256, description="Schema name")
    fileKeyTypeRestriction: Optional[str] = Field(None, description="Comma-delimited file extensions (only for fileMetadata/fileAttribute)")
    fields: MetadataSchemaFieldsModel = Field(..., description="Field definitions")
    enabled: bool = Field(default=True, description="Whether schema is enabled")

    @validator('metadataSchemaEntityType', pre=True)
    def normalize_entity_type(cls, v):
        """Accept case-insensitive input and map to correct camelCase enum value"""
        if isinstance(v, str):
            # Map lowercase input to the correct camelCase enum value
            lowercase_map = {
                'databasemetadata': MetadataSchemaEntityType.DATABASE_METADATA,
                'assetmetadata': MetadataSchemaEntityType.ASSET_METADATA,
                'filemetadata': MetadataSchemaEntityType.FILE_METADATA,
                'fileattribute': MetadataSchemaEntityType.FILE_ATTRIBUTE,
                'assetlinkmetadata': MetadataSchemaEntityType.ASSET_LINK_METADATA,
            }
            # Try lowercase lookup first
            lower_v = v.lower()
            if lower_v in lowercase_map:
                return lowercase_map[lower_v].value
            # If not found in map, return as-is to let enum validation handle it
            return v
        return v

    @root_validator
    def validate_fields(cls, values):
        """Validate schema constraints"""
        from common.validators import validate
        
        # Validate databaseId
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID',
                'allowGlobalKeyword': True
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate schemaName
        (valid, message) = validate({
            'schemaName': {
                'value': values.get('schemaName'),
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate fileKeyTypeRestriction only for fileMetadata and fileAttribute
        if values.get('fileKeyTypeRestriction'):
            if values.get('metadataSchemaEntityType') not in [MetadataSchemaEntityType.FILE_METADATA, MetadataSchemaEntityType.FILE_ATTRIBUTE]:
                raise ValueError("fileKeyTypeRestriction can only be set for fileMetadata or fileAttribute entity types")
            
            # Validate format (comma-delimited extensions)
            extensions = [ext.strip() for ext in values.get('fileKeyTypeRestriction').split(',')]
            for ext in extensions:
                if not ext or len(ext) > 10:
                    raise ValueError(f"Invalid file extension: {ext}")
        
        # For fileAttribute entity type, validate that all fields are STRING type
        if values.get('metadataSchemaEntityType') == MetadataSchemaEntityType.FILE_ATTRIBUTE:
            for field in values.get('fields').fields:
                if field.metadataFieldValueType != MetadataValueType.STRING:
                    raise ValueError(f"fileAttribute schemas only support 'string' metadataFieldValueType, found '{field.metadataFieldValueType}' for field '{field.metadataFieldKeyName}'")
        
        return values


class UpdateMetadataSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a metadata schema"""
    metadataSchemaId: str = Field(..., description="Metadata schema ID to update")
    schemaName: Optional[str] = Field(None, min_length=1, max_length=256, description="Schema name")
    fileKeyTypeRestriction: Optional[str] = Field(None, description="Comma-delimited file extensions")
    fields: Optional[MetadataSchemaFieldsModel] = Field(None, description="Field definitions")
    enabled: Optional[bool] = Field(None, description="Whether schema is enabled")

    @root_validator
    def validate_fields(cls, values):
        """Validate update constraints"""
        from common.validators import validate
        
        # Validate metadataSchemaId
        (valid, message) = validate({
            'metadataSchemaId': {
                'value': values.get('metadataSchemaId'),
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate schemaName if provided
        if values.get('schemaName'):
            (valid, message) = validate({
                'schemaName': {
                    'value': values.get('schemaName'),
                    'validator': 'OBJECT_NAME'
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        # Ensure at least one field is provided for update
        if not any([values.get('schemaName'), values.get('fileKeyTypeRestriction'), values.get('fields'), values.get('enabled') is not None]):
            raise ValueError("At least one field must be provided for update")
        
        return values


class DeleteMetadataSchemaRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting a metadata schema"""
    confirmDelete: bool = Field(default=False, description="Confirmation for deletion")

    @validator('confirmDelete')
    def validate_confirmation(cls, v):
        """Ensure confirmation is provided for deletion"""
        if not v:
            raise ValueError("confirmDelete must be true for deletion")
        return v


#######################
# Response Models
#######################

class MetadataSchemaResponseModel(BaseModel, extra='ignore'):
    """Response model for metadata schema data"""
    metadataSchemaId: str = Field(..., description="Metadata schema ID")
    databaseId: str = Field(..., description="Database ID")
    metadataSchemaEntityType: MetadataSchemaEntityType = Field(..., description="Entity type")
    schemaName: str = Field(..., description="Schema name")
    fileKeyTypeRestriction: Optional[str] = Field(None, description="File extension restrictions")
    fields: MetadataSchemaFieldsModel = Field(..., description="Field definitions")
    enabled: bool = Field(..., description="Whether schema is enabled")
    dateCreated: Optional[str] = Field(None, description="Creation timestamp")
    dateModified: Optional[str] = Field(None, description="Last modification timestamp")
    createdBy: Optional[str] = Field(None, description="Creator user ID")
    modifiedBy: Optional[str] = Field(None, description="Last modifier user ID")

    @validator('metadataSchemaEntityType', pre=True)
    def normalize_entity_type(cls, v):
        """Accept string values from DynamoDB and convert to enum"""
        if v is None:
            return v
        if isinstance(v, str):
            # Map string values to enum
            lowercase_map = {
                'databasemetadata': MetadataSchemaEntityType.DATABASE_METADATA,
                'assetmetadata': MetadataSchemaEntityType.ASSET_METADATA,
                'filemetadata': MetadataSchemaEntityType.FILE_METADATA,
                'fileattribute': MetadataSchemaEntityType.FILE_ATTRIBUTE,
                'assetlinkmetadata': MetadataSchemaEntityType.ASSET_LINK_METADATA,
            }
            # Try lowercase lookup first
            lower_v = v.lower()
            if lower_v in lowercase_map:
                return lowercase_map[lower_v]
            # If not found in map, return as-is to let enum validation handle it
            return v
        return v


class MetadataSchemaOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for metadata schema operations (create, update, delete)"""
    success: bool = Field(..., description="Operation success status")
    message: str = Field(..., description="Operation message")
    metadataSchemaId: str = Field(..., description="Metadata schema ID")
    operation: Literal["create", "update", "delete"] = Field(..., description="Operation type")
    timestamp: str = Field(..., description="Operation timestamp")


class GetMetadataSchemasResponseModel(BaseModel, extra='ignore'):
    """Response model for listing metadata schemas"""
    Items: List[MetadataSchemaResponseModel] = Field(default=[], description="List of metadata schemas")
    NextToken: Optional[str] = Field(None, description="Token for next page")
    message: str = Field(default="Success", description="Response message")