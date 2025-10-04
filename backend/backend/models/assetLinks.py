# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator
from enum import Enum
import json
import re
import uuid
from datetime import datetime
import geojson

class RelationshipType(str, Enum):
    RELATED = "related"
    PARENT_CHILD = "parentChild"

class MetadataValueType(str, Enum):
    STRING = "string"
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

def validate_metadata_value_common(value: str, value_type: MetadataValueType) -> str:
    """Common validation function for metadata values"""
    
    # First check if the type is valid
    valid_types = [e.value for e in MetadataValueType]
    if value_type not in valid_types:
        raise ValueError(f"Invalid metadata value type: {value_type}. Supported types are: {', '.join(valid_types)}")
    
    # STRING type requires no additional validation
    if value_type == MetadataValueType.STRING:
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
            
            # Check if it's a valid GeoJSON Point or lat/long object
            if json_obj.get('type') != 'Point':
                # Non-Valid GeoJSON Point
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
    
    return value

# Asset Link Models
class CreateAssetLinkRequestModel(BaseModel):
    fromAssetId: str = Field(..., description="Source asset ID")
    fromAssetDatabaseId: str = Field(..., description="Source asset database ID")
    toAssetId: str = Field(..., description="Target asset ID")
    toAssetDatabaseId: str = Field(..., description="Target asset database ID")
    relationshipType: RelationshipType = Field(..., description="Type of relationship")
    tags: Optional[List[str]] = Field(default=[], description="Tags associated with the asset link")

class CreateAssetLinkResponseModel(BaseModel):
    assetLinkId: str = Field(..., description="Generated asset link ID")
    message: str = Field(..., description="Success message")

class AssetLinkModel(BaseModel):
    assetLinkId: str = Field(..., description="Asset link ID")
    fromAssetId: str = Field(..., description="Source asset ID")
    fromAssetDatabaseId: str = Field(..., description="Source asset database ID")
    toAssetId: str = Field(..., description="Target asset ID")
    toAssetDatabaseId: str = Field(..., description="Target asset database ID")
    relationshipType: RelationshipType = Field(..., description="Type of relationship")
    tags: List[str] = Field(default=[], description="Tags associated with the asset link")

class GetSingleAssetLinkResponseModel(BaseModel):
    assetLink: AssetLinkModel = Field(..., description="Asset link details")
    message: str = Field(default="Success", description="Response message")

class UpdateAssetLinkRequestModel(BaseModel):
    tags: List[str] = Field(default=[], description="Updated tags for the asset link")

class UpdateAssetLinkResponseModel(BaseModel):
    message: str = Field(..., description="Success message")

class DeleteAssetLinkResponseModel(BaseModel):
    message: str = Field(..., description="Success message")

# Request Models for Path and Query Parameters
class GetAssetLinksRequestModel(BaseModel):
    assetId: str = Field(..., description="Asset ID to get links for")
    databaseId: str = Field(..., description="Database ID")
    childTreeView: bool = Field(default=False, description="Return tree view for children")

class GetSingleAssetLinkRequestModel(BaseModel):
    assetLinkId: str = Field(..., description="Asset link ID")

class DeleteAssetLinkRequestModel(BaseModel):
    relationId: str = Field(..., description="Asset link ID to delete")

class GetAssetLinkMetadataRequestModel(BaseModel):
    assetLinkId: str = Field(..., description="Asset link ID")

class UpdateAssetLinkMetadataPathRequestModel(BaseModel):
    assetLinkId: str = Field(..., description="Asset link ID")
    metadataKey: str = Field(..., description="Metadata key to update")

class DeleteAssetLinkMetadataPathRequestModel(BaseModel):
    assetLinkId: str = Field(..., description="Asset link ID")
    metadataKey: str = Field(..., description="Metadata key to delete")

# Asset Link Metadata Models
class CreateAssetLinkMetadataRequestModel(BaseModel):
    metadataKey: str = Field(..., description="Metadata key")
    metadataValue: str = Field(..., description="Metadata value")
    metadataValueType: MetadataValueType = Field(default=MetadataValueType.STRING, description="Type of metadata value")

    @validator('metadataValueType', pre=True)
    def normalize_metadata_value_type(cls, v):
        """Convert metadataValueType to lowercase"""
        if isinstance(v, str):
            return v.lower()
        return v

    @validator('metadataValue')
    def validate_metadata_value(cls, v, values):
        """Validate metadataValue based on metadataValueType"""
        if 'metadataValueType' not in values:
            return v
        return validate_metadata_value_common(v, values['metadataValueType'])

class UpdateAssetLinkMetadataRequestModel(BaseModel):
    metadataValue: str = Field(..., description="Updated metadata value")
    metadataValueType: MetadataValueType = Field(default=MetadataValueType.STRING, description="Type of metadata value")

    @validator('metadataValueType', pre=True)
    def normalize_metadata_value_type(cls, v):
        """Convert metadataValueType to lowercase"""
        if isinstance(v, str):
            return v.lower()
        return v

    @validator('metadataValue')
    def validate_metadata_value(cls, v, values):
        """Validate metadataValue based on metadataValueType"""
        if 'metadataValueType' not in values:
            return v
        return validate_metadata_value_common(v, values['metadataValueType'])

class AssetLinkMetadataModel(BaseModel):
    assetLinkId: str = Field(..., description="Asset link ID")
    metadataKey: str = Field(..., description="Metadata key")
    metadataValue: str = Field(..., description="Metadata value")
    metadataValueType: MetadataValueType = Field(..., description="Type of metadata value")

class CreateAssetLinkMetadataResponseModel(BaseModel):
    message: str = Field(..., description="Success message")

class GetAssetLinkMetadataResponseModel(BaseModel):
    metadata: List[AssetLinkMetadataModel] = Field(..., description="List of metadata for the asset link")
    message: str = Field(default="Success", description="Response message")

class UpdateAssetLinkMetadataResponseModel(BaseModel):
    message: str = Field(..., description="Success message")

class DeleteAssetLinkMetadataResponseModel(BaseModel):
    message: str = Field(..., description="Success message")

# Asset Links Tree View Models
class AssetNodeModel(BaseModel):
    assetId: str = Field(..., description="Asset ID")
    assetName: str = Field(..., description="Asset name")
    databaseId: str = Field(..., description="Database ID")
    assetLinkId: Optional[str] = Field(None, description="Asset link ID if applicable")

# Simple tree node model without self-reference to avoid Pydantic issues
class AssetTreeNodeModel(BaseModel):
    assetId: str = Field(..., description="Asset ID")
    assetName: str = Field(..., description="Asset name")
    databaseId: str = Field(..., description="Database ID")
    assetLinkId: str = Field(..., description="Asset link ID")
    children: List[Dict[str, Any]] = Field(default_factory=list, description="Child nodes in the tree")

class UnauthorizedCountsModel(BaseModel):
    related: int = Field(default=0, description="Count of unauthorized related assets")
    parents: int = Field(default=0, description="Count of unauthorized parent assets")
    children: int = Field(default=0, description="Count of unauthorized child assets")

class GetAssetLinksResponseModel(BaseModel):
    related: List[AssetNodeModel] = Field(default=[], description="Related assets")
    parents: List[AssetNodeModel] = Field(default=[], description="Parent assets")
    children: List[AssetNodeModel] = Field(default=[], description="Child assets (flat list)")
    unauthorizedCounts: UnauthorizedCountsModel = Field(default_factory=UnauthorizedCountsModel, description="Counts of unauthorized assets")
    message: str = Field(default="Success", description="Response message")

class GetAssetLinksTreeViewResponseModel(BaseModel):
    related: List[AssetNodeModel] = Field(default=[], description="Related assets")
    parents: List[AssetNodeModel] = Field(default=[], description="Parent assets")
    children: List[AssetTreeNodeModel] = Field(default=[], description="Child assets (tree structure)")
    unauthorizedCounts: UnauthorizedCountsModel = Field(default_factory=UnauthorizedCountsModel, description="Counts of unauthorized assets")
    message: str = Field(default="Success", description="Response message")
