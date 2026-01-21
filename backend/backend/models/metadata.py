# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metadata models for VAMS - Centralized metadata handling across all entity types."""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, validator, root_validator
from enum import Enum
import json
import re
from datetime import datetime
import geojson
from customLogging.logger import safeLogger

logger = safeLogger(service_name="MetadataModels")

#######################
# Metadata Value Types and Validation
#######################

class MetadataValueType(str, Enum):
    """Supported metadata value types"""
    STRING = "string"
    MULTILINE_STRING = "multiline_string"
    INLINE_CONTROLLED_LIST = "inline_controlled_list"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    XYZ = "xyz"
    WXYZ = "wxyz"
    MATRIX4X4 = "matrix4x4"
    GEOPOINT = "geopoint"
    GEOJSON = "geojson"
    LLA = "lla"
    JSON = "json"


class UpdateType(str, Enum):
    """Supported update types for metadata operations"""
    UPDATE = "update"
    REPLACE_ALL = "replace_all"


def validate_metadata_value_common(value: str, value_type: MetadataValueType) -> str:
    """Common validation function for metadata values
    
    Args:
        value: The metadata value as a string
        value_type: The type of metadata value
        
    Returns:
        The validated value
        
    Raises:
        ValueError: If validation fails
    """
    
    # Allow empty strings - schema validation will check if field is required
    # This allows optional fields to have empty values without format validation errors
    if value == "" or value is None:
        return ""
    
    # First check if the type is valid
    valid_types = [e.value for e in MetadataValueType]
    if value_type not in valid_types:
        raise ValueError(f"Invalid metadata value type: {value_type}. Supported types are: {', '.join(valid_types)}")
    
    # STRING type requires no additional validation
    if value_type == MetadataValueType.STRING:
        return value
    
    # MULTILINE_STRING type requires no additional validation (same as STRING)
    elif value_type == MetadataValueType.MULTILINE_STRING:
        return value
    
    # INLINE_CONTROLLED_LIST type requires no additional validation (same as STRING)
    elif value_type == MetadataValueType.INLINE_CONTROLLED_LIST:
        return value
        
    elif value_type == MetadataValueType.NUMBER:
        try:
            float(value)
        except ValueError:
            raise ValueError(f"metadataValue must be a valid number for type 'number'")
            
    elif value_type == MetadataValueType.BOOLEAN:
        if value.lower() not in ['true', 'false']:
            raise ValueError(f"metadataValue must be 'true' or 'false' for type 'boolean'")
            
    elif value_type == MetadataValueType.DATE:
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"metadataValue must be a valid ISO date format for type 'date'")
            
    elif value_type == MetadataValueType.JSON:
        try:
            json.loads(value)
        except json.JSONDecodeError:
            raise ValueError(f"metadataValue must be valid JSON for type 'json'")
            
    elif value_type == MetadataValueType.XYZ:
        try:
            xyz_data = json.loads(value)
            if not isinstance(xyz_data, dict):
                raise ValueError("XYZ data must be a JSON object")
                
            required_keys = {'x', 'y', 'z'}
            if not required_keys.issubset(xyz_data.keys()):
                raise ValueError(f"XYZ data must contain 'x', 'y', and 'z' keys")
                
            for key in required_keys:
                if not isinstance(xyz_data[key], (int, float)):
                    raise ValueError(f"XYZ coordinate '{key}' must be a number")
                    
        except json.JSONDecodeError:
            raise ValueError(f"metadataValue must be valid JSON for type 'xyz'")
            
    elif value_type == MetadataValueType.WXYZ:
        try:
            wxyz_data = json.loads(value)
            if not isinstance(wxyz_data, dict):
                raise ValueError("WXYZ data must be a JSON object")
                
            required_keys = {'w', 'x', 'y', 'z'}
            if not required_keys.issubset(wxyz_data.keys()):
                raise ValueError(f"WXYZ data must contain 'w', 'x', 'y', and 'z' keys")
                
            for key in required_keys:
                if not isinstance(wxyz_data[key], (int, float)):
                    raise ValueError(f"WXYZ coordinate '{key}' must be a number")
                    
        except json.JSONDecodeError:
            raise ValueError(f"metadataValue must be valid JSON for type 'wxyz'")
            
    elif value_type == MetadataValueType.MATRIX4X4:
        try:
            matrix_data = json.loads(value)
            if not isinstance(matrix_data, list):
                raise ValueError("MATRIX4X4 data must be a JSON array")
                
            # Validate 4x4 matrix structure
            if len(matrix_data) != 4:
                raise ValueError("MATRIX4X4 must be a 4x4 matrix (4 rows)")
                
            for i, row in enumerate(matrix_data):
                if not isinstance(row, list):
                    raise ValueError(f"MATRIX4X4 row {i} must be an array")
                if len(row) != 4:
                    raise ValueError(f"MATRIX4X4 row {i} must contain exactly 4 elements")
                    
                for j, element in enumerate(row):
                    if not isinstance(element, (int, float)):
                        raise ValueError(f"MATRIX4X4 element at [{i}][{j}] must be a number")
                        
        except json.JSONDecodeError:
            raise ValueError(f"metadataValue must be valid JSON for type 'matrix4x4'")
            
    elif value_type == MetadataValueType.GEOPOINT:
        try:
            # Use GeoJSON library for validation
            geojson_obj = geojson.loads(value)
            json_obj = json.loads(value)
            
            # Check if it's a valid GeoJSON Point
            if json_obj.get('type') != 'Point':
                raise ValueError("GEOPOINT type must be 'Point'")
                
        except (json.JSONDecodeError, ValueError) as e:
            if "GEOPOINT" in str(e):
                raise e
            raise ValueError(f"GEOPOINT validation failed: {str(e)}")
            
    elif value_type == MetadataValueType.GEOJSON:
        try:
            # Use GeoJSON library for validation
            geojson_obj = geojson.loads(value)
            # geojson.loads() will raise an exception if it's not valid GeoJSON
            
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"GEOJSON validation failed: {str(e)}")
            
    elif value_type == MetadataValueType.LLA:
        try:
            lla_data = json.loads(value)
            if not isinstance(lla_data, dict):
                raise ValueError("LLA data must be a JSON object")
                
            # Check for required keys
            required_keys = {'lat', 'long', 'alt'}
            if not required_keys.issubset(lla_data.keys()):
                raise ValueError("LLA data must contain 'lat', 'long', and 'alt' keys")
                
            # Validate latitude
            lat = lla_data['lat']
            if not isinstance(lat, (int, float)):
                raise ValueError("LLA latitude must be a number")
            if lat < -90 or lat > 90:
                raise ValueError("LLA latitude must be between -90 and 90")
                
            # Validate longitude
            long_val = lla_data['long']
            if not isinstance(long_val, (int, float)):
                raise ValueError("LLA longitude must be a number")
            if long_val < -180 or long_val > 180:
                raise ValueError("LLA longitude must be between -180 and 180")
                
            # Validate altitude
            alt = lla_data['alt']
            if not isinstance(alt, (int, float)):
                raise ValueError("LLA altitude must be a number")
                
        except json.JSONDecodeError:
            raise ValueError(f"metadataValue must be valid JSON for type 'lla'")
    
    else:
        # This should never be reached due to the check at the beginning,
        # but included as a safety measure
        raise ValueError(f"Unsupported metadata value type: {value_type}")
    
    return value


#######################
# Common Metadata Models
#######################

class MetadataItemModel(BaseModel, extra='ignore'):
    """Single metadata item with key, value, and type"""
    metadataKey: str = Field(..., min_length=1, max_length=256, description="Metadata key")
    metadataValue: str = Field(..., description="Metadata value as string")
    metadataValueType: MetadataValueType = Field(default=MetadataValueType.STRING, description="Type of metadata value")

    @validator('metadataValueType', pre=True)
    def normalize_and_validate_metadata_value_type(cls, v):
        """Convert metadataValueType to lowercase and validate it's a valid enum value"""
        if isinstance(v, str):
            v_lower = v.lower()
            # Check if the lowercase value is a valid enum value
            valid_types = [e.value for e in MetadataValueType]
            if v_lower not in valid_types:
                raise ValueError(f"Invalid metadataValueType '{v}'. Supported types are: {', '.join(valid_types)}")
            return v_lower
        return v

    @root_validator
    def validate_metadata_value(cls, values):
        """Validate metadataValue based on metadataValueType"""
        # Only validate if metadataValueType is present (field validation passed)
        if 'metadataValueType' in values and 'metadataValue' in values:
            values['metadataValue'] = validate_metadata_value_common(values['metadataValue'], values['metadataValueType'])
        return values


# Note: BulkMetadataItemModel is now identical to MetadataItemModel since we use keys directly
# Keeping as alias for backward compatibility
BulkMetadataItemModel = MetadataItemModel


class BulkOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for bulk metadata operations"""
    success: bool = Field(..., description="True if at least one item succeeded")
    totalItems: int = Field(..., description="Total number of items in the request")
    successCount: int = Field(..., description="Number of items that succeeded")
    failureCount: int = Field(..., description="Number of items that failed")
    successfulItems: List[str] = Field(default=[], description="List of metadata IDs/keys that succeeded")
    failedItems: List[Dict[str, str]] = Field(default=[], description="List of failed items with error messages")
    message: str = Field(..., description="Overall operation message")
    timestamp: str = Field(..., description="Operation timestamp")


#######################
# Asset Link Metadata Models
#######################

class AssetLinkMetadataPathRequestModel(BaseModel, extra='ignore'):
    """Path parameters for asset link metadata requests"""
    assetLinkId: str = Field(..., description="Asset link ID")

    @root_validator
    def validate_fields(cls, values):
        """Validate assetLinkId format"""
        from common.validators import validate
        
        (valid, message) = validate({
            'assetLinkId': {
                'value': values.get('assetLinkId'),
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class GetAssetLinkMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for getting asset link metadata"""
    maxItems: Optional[int] = Field(default=30000, ge=1, description="Maximum items to return")
    pageSize: Optional[int] = Field(default=3000, ge=1, description="Page size for pagination")
    startingToken: Optional[str] = Field(None, description="Token for pagination")


class CreateAssetLinkMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for creating asset link metadata (single or bulk)"""
    metadata: List[MetadataItemModel] = Field(..., description="List of metadata items to create")

    @root_validator
    def validate_metadata_list(cls, values):
        """Validate metadata list has at least one item"""
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        return values


class UpdateAssetLinkMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for updating asset link metadata (single or bulk)"""
    metadata: List[BulkMetadataItemModel] = Field(..., description="List of metadata items to update")
    updateType: UpdateType = Field(default=UpdateType.UPDATE, description="Update type: 'update' (default) or 'replace_all'")

    @validator('updateType', pre=True)
    def normalize_update_type(cls, v):
        """Convert updateType to lowercase"""
        if isinstance(v, str):
            return v.lower()
        return v

    @root_validator
    def validate_replace_all_limits(cls, values):
        """Validate REPLACE_ALL operation limits"""
        # Validate metadata list has at least one item
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        
        if values.get('updateType') == UpdateType.REPLACE_ALL:
            if len(values.get('metadata', [])) > 500:
                raise ValueError("REPLACE_ALL operations are limited to 500 metadata items")
        return values


class DeleteAssetLinkMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting asset link metadata"""
    metadataKeys: List[str] = Field(..., description="List of metadata keys to delete")

    @root_validator
    def validate_metadata_keys_list(cls, values):
        """Validate metadataKeys list has at least one item"""
        if not values.get('metadataKeys') or len(values.get('metadataKeys', [])) < 1:
            raise ValueError("metadataKeys must contain at least 1 item")
        return values


class AssetLinkMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for a single asset link metadata item"""
    assetLinkId: str = Field(..., description="Asset link ID")
    metadataKey: str = Field(..., description="Metadata key")
    metadataValue: str = Field(..., description="Metadata value")
    metadataValueType: MetadataValueType = Field(..., description="Type of metadata value")
    # Schema enrichment fields
    metadataSchemaName: Optional[str] = Field(None, description="Comma-delimited schema names with databaseIds in brackets (e.g., 'Schema1 (db1), Schema2 (db2)')")
    metadataSchemaField: Optional[bool] = Field(None, description="Is this field defined in a schema?")
    metadataSchemaRequired: Optional[bool] = Field(None, description="Is this field required per schema?")
    metadataSchemaSequence: Optional[int] = Field(None, description="Display order sequence from schema")
    metadataSchemaDefaultValue: Optional[str] = Field(None, description="Default value from schema")
    metadataSchemaDependsOn: Optional[List[str]] = Field(None, description="List of field keys this field depends on")
    metadataSchemaMultiFieldConflict: Optional[bool] = Field(None, description="Multiple schemas define this field with conflicts")
    metadataSchemaControlledListKeys: Optional[List[str]] = Field(None, description="Controlled list values for INLINE_CONTROLLED_LIST type")


class GetAssetLinkMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for getting asset link metadata"""
    metadata: List[AssetLinkMetadataResponseModel] = Field(default=[], description="List of metadata items")
    restrictMetadataOutsideSchemas: bool = Field(..., description="True if database restricts metadata outside schemas AND schemas exist")
    NextToken: Optional[str] = Field(None, description="Token for next page")
    message: str = Field(default="Success", description="Response message")


#######################
# Asset Metadata Models
#######################

class AssetMetadataPathRequestModel(BaseModel, extra='ignore'):
    """Path parameters for asset metadata requests"""
    databaseId: str = Field(..., description="Database ID")
    assetId: str = Field(..., description="Asset ID")

    @root_validator
    def validate_fields(cls, values):
        """Validate databaseId and assetId format"""
        from common.validators import validate
        
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID'
            },
            'assetId': {
                'value': values.get('assetId'),
                'validator': 'ASSET_ID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class GetAssetMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for getting asset metadata"""
    maxItems: Optional[int] = Field(default=30000, ge=1, description="Maximum items to return")
    pageSize: Optional[int] = Field(default=3000, ge=1, description="Page size for pagination")
    startingToken: Optional[str] = Field(None, description="Token for pagination")


class CreateAssetMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for creating asset metadata (single or bulk)"""
    metadata: List[MetadataItemModel] = Field(..., description="List of metadata items to create")

    @root_validator
    def validate_metadata_list(cls, values):
        """Validate metadata list has at least one item"""
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        return values


class UpdateAssetMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for updating asset metadata (single or bulk)"""
    metadata: List[BulkMetadataItemModel] = Field(..., description="List of metadata items to update")
    updateType: UpdateType = Field(default=UpdateType.UPDATE, description="Update type: 'update' (default) or 'replace_all'")

    @validator('updateType', pre=True)
    def normalize_update_type(cls, v):
        """Convert updateType to lowercase"""
        if isinstance(v, str):
            return v.lower()
        return v

    @root_validator
    def validate_replace_all_limits(cls, values):
        """Validate REPLACE_ALL operation limits"""
        # Validate metadata list has at least one item
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        
        if values.get('updateType') == UpdateType.REPLACE_ALL:
            if len(values.get('metadata', [])) > 500:
                raise ValueError("REPLACE_ALL operations are limited to 500 metadata items")
        return values


class DeleteAssetMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting asset metadata"""
    metadataKeys: List[str] = Field(..., description="List of metadata keys to delete")

    @root_validator
    def validate_metadata_keys_list(cls, values):
        """Validate metadataKeys list has at least one item"""
        if not values.get('metadataKeys') or len(values.get('metadataKeys', [])) < 1:
            raise ValueError("metadataKeys must contain at least 1 item")
        return values


class AssetMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for a single asset metadata item"""
    databaseId: str = Field(..., description="Database ID")
    assetId: str = Field(..., description="Asset ID")
    metadataKey: str = Field(..., description="Metadata key")
    metadataValue: str = Field(..., description="Metadata value")
    metadataValueType: MetadataValueType = Field(..., description="Type of metadata value")
    # Schema enrichment fields
    metadataSchemaName: Optional[str] = Field(None, description="Comma-delimited schema names with databaseIds in brackets (e.g., 'Schema1 (db1), Schema2 (db2)')")
    metadataSchemaField: Optional[bool] = Field(None, description="Is this field defined in a schema?")
    metadataSchemaRequired: Optional[bool] = Field(None, description="Is this field required per schema?")
    metadataSchemaSequence: Optional[int] = Field(None, description="Display order sequence from schema")
    metadataSchemaDefaultValue: Optional[str] = Field(None, description="Default value from schema")
    metadataSchemaDependsOn: Optional[List[str]] = Field(None, description="List of field keys this field depends on")
    metadataSchemaMultiFieldConflict: Optional[bool] = Field(None, description="Multiple schemas define this field with conflicts")
    metadataSchemaControlledListKeys: Optional[List[str]] = Field(None, description="Controlled list values for INLINE_CONTROLLED_LIST type")


class GetAssetMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for getting asset metadata"""
    metadata: List[AssetMetadataResponseModel] = Field(default=[], description="List of metadata items")
    restrictMetadataOutsideSchemas: bool = Field(..., description="True if database restricts metadata outside schemas AND schemas exist")
    NextToken: Optional[str] = Field(None, description="Token for next page")
    message: str = Field(default="Success", description="Response message")


#######################
# File Metadata/Attribute Models
#######################

class FileMetadataPathRequestModel(BaseModel, extra='ignore'):
    """Path parameters for file metadata requests"""
    databaseId: str = Field(..., description="Database ID")
    assetId: str = Field(..., description="Asset ID")

    @root_validator
    def validate_fields(cls, values):
        """Validate databaseId and assetId format"""
        from common.validators import validate
        
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID'
            },
            'assetId': {
                'value': values.get('assetId'),
                'validator': 'ASSET_ID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class GetFileMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for getting file metadata or attributes"""
    filePath: str = Field(..., description="Relative file path")
    type: Literal["metadata", "attribute"] = Field(..., description="Type: metadata or attribute")
    maxItems: Optional[int] = Field(default=30000, ge=1, description="Maximum items to return")
    pageSize: Optional[int] = Field(default=3000, ge=1, description="Page size for pagination")
    startingToken: Optional[str] = Field(None, description="Token for pagination")

    @root_validator
    def validate_fields(cls, values):
        """Normalize and validate file path"""
        from common.validators import validate
        
        # Normalize: Add leading slash if missing
        file_path = values.get('filePath', '')
        if file_path and not file_path.startswith('/'):
            values['filePath'] = '/' + file_path
            logger.info(f"Normalized filePath by adding leading slash: {file_path} -> {values['filePath']}")
        
        # Validate filePath format
        (valid, message) = validate({
            'filePath': {
                'value': values.get('filePath'),
                'validator': 'RELATIVE_FILE_PATH'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate that filePath is not a folder (doesn't end with /)
        if values.get('filePath', '').endswith('/'):
            raise ValueError("File path cannot be a folder (must not end with /)")
        
        return values


class CreateFileMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for creating file metadata or attributes (single or bulk)"""
    filePath: str = Field(..., description="Relative file path")
    type: Literal["metadata", "attribute"] = Field(..., description="Type: metadata or attribute")
    metadata: List[MetadataItemModel] = Field(..., description="List of metadata items to create")

    @root_validator
    def validate_fields(cls, values):
        """Normalize and validate file path and attribute type constraints"""
        from common.validators import validate
        
        # Normalize: Add leading slash if missing
        file_path = values.get('filePath', '')
        if file_path and not file_path.startswith('/'):
            values['filePath'] = '/' + file_path
            logger.info(f"Normalized filePath by adding leading slash: {file_path} -> {values['filePath']}")
        
        # Validate filePath format
        (valid, message) = validate({
            'filePath': {
                'value': values.get('filePath'),
                'validator': 'RELATIVE_FILE_PATH'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate that filePath is not a folder (doesn't end with /)
        if values.get('filePath', '').endswith('/'):
            raise ValueError("File path cannot be a folder (must not end with /)")
        
        # Validate metadata list has at least one item
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        
        # Validate that attributes only use STRING type
        if values.get('type') == 'attribute':
            for item in values.get('metadata', []):
                if item.metadataValueType != MetadataValueType.STRING:
                    raise ValueError("File attributes only support 'string' metadataValueType")
        
        return values


class UpdateFileMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for updating file metadata or attributes (single or bulk)"""
    filePath: str = Field(..., description="Relative file path")
    type: Literal["metadata", "attribute"] = Field(..., description="Type: metadata or attribute")
    metadata: List[BulkMetadataItemModel] = Field(..., description="List of metadata items to update")
    updateType: UpdateType = Field(default=UpdateType.UPDATE, description="Update type: 'update' (default) or 'replace_all'")

    @validator('updateType', pre=True)
    def normalize_update_type(cls, v):
        """Convert updateType to lowercase"""
        if isinstance(v, str):
            return v.lower()
        return v

    @root_validator
    def validate_fields(cls, values):
        """Normalize and validate file path and attribute type constraints"""
        from common.validators import validate
        
        # Normalize: Add leading slash if missing
        file_path = values.get('filePath', '')
        if file_path and not file_path.startswith('/'):
            values['filePath'] = '/' + file_path
            logger.info(f"Normalized filePath by adding leading slash: {file_path} -> {values['filePath']}")
        
        # Validate filePath format
        (valid, message) = validate({
            'filePath': {
                'value': values.get('filePath'),
                'validator': 'RELATIVE_FILE_PATH'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate that filePath is not a folder (doesn't end with /)
        if values.get('filePath', '').endswith('/'):
            raise ValueError("File path cannot be a folder (must not end with /)")
        
        # Validate metadata list has at least one item
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        
        # Validate that attributes only use STRING type
        if values.get('type') == 'attribute':
            for item in values.get('metadata', []):
                if item.metadataValueType != MetadataValueType.STRING:
                    raise ValueError("File attributes only support 'string' metadataValueType")
        
        # Validate REPLACE_ALL limits
        if values.get('updateType') == UpdateType.REPLACE_ALL:
            if len(values.get('metadata', [])) > 500:
                raise ValueError("REPLACE_ALL operations are limited to 500 metadata items")
        
        return values


class DeleteFileMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting file metadata or attributes"""
    filePath: str = Field(..., description="Relative file path")
    type: Literal["metadata", "attribute"] = Field(..., description="Type: metadata or attribute")
    metadataKeys: List[str] = Field(..., description="List of metadata keys to delete")

    @root_validator
    def validate_fields(cls, values):
        """Normalize and validate file path"""
        from common.validators import validate
        
        # Normalize: Add leading slash if missing
        file_path = values.get('filePath', '')
        if file_path and not file_path.startswith('/'):
            values['filePath'] = '/' + file_path
            logger.info(f"Normalized filePath by adding leading slash: {file_path} -> {values['filePath']}")
        
        # Validate filePath format
        (valid, message) = validate({
            'filePath': {
                'value': values.get('filePath'),
                'validator': 'RELATIVE_FILE_PATH'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate that filePath is not a folder (doesn't end with /)
        if values.get('filePath', '').endswith('/'):
            raise ValueError("File path cannot be a folder (must not end with /)")
        
        # Validate metadataKeys list has at least one item
        if not values.get('metadataKeys') or len(values.get('metadataKeys', [])) < 1:
            raise ValueError("metadataKeys must contain at least 1 item")
        
        return values


class FileMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for a single file metadata or attribute item"""
    databaseId: str = Field(..., description="Database ID")
    assetId: str = Field(..., description="Asset ID")
    filePath: str = Field(..., description="Relative file path")
    metadataKey: str = Field(..., description="Metadata key (or attributeKey for attributes)")
    metadataValue: str = Field(..., description="Metadata value (or attributeValue for attributes)")
    metadataValueType: MetadataValueType = Field(..., description="Type of metadata value")
    # Schema enrichment fields
    metadataSchemaName: Optional[str] = Field(None, description="Comma-delimited schema names with databaseIds in brackets (e.g., 'Schema1 (db1), Schema2 (db2)')")
    metadataSchemaField: Optional[bool] = Field(None, description="Is this field defined in a schema?")
    metadataSchemaRequired: Optional[bool] = Field(None, description="Is this field required per schema?")
    metadataSchemaSequence: Optional[int] = Field(None, description="Display order sequence from schema")
    metadataSchemaDefaultValue: Optional[str] = Field(None, description="Default value from schema")
    metadataSchemaDependsOn: Optional[List[str]] = Field(None, description="List of field keys this field depends on")
    metadataSchemaMultiFieldConflict: Optional[bool] = Field(None, description="Multiple schemas define this field with conflicts")
    metadataSchemaControlledListKeys: Optional[List[str]] = Field(None, description="Controlled list values for INLINE_CONTROLLED_LIST type")


class GetFileMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for getting file metadata or attributes"""
    metadata: List[FileMetadataResponseModel] = Field(default=[], description="List of metadata items")
    restrictMetadataOutsideSchemas: bool = Field(..., description="True if database restricts metadata outside schemas AND schemas exist")
    NextToken: Optional[str] = Field(None, description="Token for next page")
    message: str = Field(default="Success", description="Response message")


#######################
# Database Metadata Models
#######################

class DatabaseMetadataPathRequestModel(BaseModel, extra='ignore'):
    """Path parameters for database metadata requests"""
    databaseId: str = Field(..., description="Database ID")

    @root_validator
    def validate_fields(cls, values):
        """Validate databaseId format"""
        from common.validators import validate
        
        (valid, message) = validate({
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class GetDatabaseMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for getting database metadata"""
    maxItems: Optional[int] = Field(default=30000, ge=1, description="Maximum items to return")
    pageSize: Optional[int] = Field(default=3000, ge=1, description="Page size for pagination")
    startingToken: Optional[str] = Field(None, description="Token for pagination")


class CreateDatabaseMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for creating database metadata (single or bulk)"""
    metadata: List[MetadataItemModel] = Field(..., description="List of metadata items to create")

    @root_validator
    def validate_metadata_list(cls, values):
        """Validate metadata list has at least one item"""
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        return values


class UpdateDatabaseMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for updating database metadata (single or bulk)"""
    metadata: List[BulkMetadataItemModel] = Field(..., description="List of metadata items to update")
    updateType: UpdateType = Field(default=UpdateType.UPDATE, description="Update type: 'update' (default) or 'replace_all'")

    @validator('updateType', pre=True)
    def normalize_update_type(cls, v):
        """Convert updateType to lowercase"""
        if isinstance(v, str):
            return v.lower()
        return v

    @root_validator
    def validate_replace_all_limits(cls, values):
        """Validate REPLACE_ALL operation limits"""
        # Validate metadata list has at least one item
        if not values.get('metadata') or len(values.get('metadata', [])) < 1:
            raise ValueError("metadata must contain at least 1 item")
        
        if values.get('updateType') == UpdateType.REPLACE_ALL:
            if len(values.get('metadata', [])) > 500:
                raise ValueError("REPLACE_ALL operations are limited to 500 metadata items")
        return values


class DeleteDatabaseMetadataRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting database metadata"""
    metadataKeys: List[str] = Field(..., description="List of metadata keys to delete")

    @root_validator
    def validate_metadata_keys_list(cls, values):
        """Validate metadataKeys list has at least one item"""
        if not values.get('metadataKeys') or len(values.get('metadataKeys', [])) < 1:
            raise ValueError("metadataKeys must contain at least 1 item")
        return values


class DatabaseMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for a single database metadata item"""
    databaseId: str = Field(..., description="Database ID")
    metadataKey: str = Field(..., description="Metadata key")
    metadataValue: str = Field(..., description="Metadata value")
    metadataValueType: MetadataValueType = Field(..., description="Type of metadata value")
    # Schema enrichment fields
    metadataSchemaName: Optional[str] = Field(None, description="Comma-delimited schema names with databaseIds in brackets (e.g., 'Schema1 (db1), Schema2 (db2)')")
    metadataSchemaField: Optional[bool] = Field(None, description="Is this field defined in a schema?")
    metadataSchemaRequired: Optional[bool] = Field(None, description="Is this field required per schema?")
    metadataSchemaSequence: Optional[int] = Field(None, description="Display order sequence from schema")
    metadataSchemaDefaultValue: Optional[str] = Field(None, description="Default value from schema")
    metadataSchemaDependsOn: Optional[List[str]] = Field(None, description="List of field keys this field depends on")
    metadataSchemaMultiFieldConflict: Optional[bool] = Field(None, description="Multiple schemas define this field with conflicts")
    metadataSchemaControlledListKeys: Optional[List[str]] = Field(None, description="Controlled list values for INLINE_CONTROLLED_LIST type")


class GetDatabaseMetadataResponseModel(BaseModel, extra='ignore'):
    """Response model for getting database metadata"""
    metadata: List[DatabaseMetadataResponseModel] = Field(default=[], description="List of metadata items")
    restrictMetadataOutsideSchemas: bool = Field(..., description="True if database restricts metadata outside schemas AND schemas exist")
    NextToken: Optional[str] = Field(None, description="Token for next page")
    message: str = Field(default="Success", description="Response message")