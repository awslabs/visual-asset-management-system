# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator
from enum import Enum
import json
import re
import uuid
from datetime import datetime

class RelationshipType(str, Enum):
    RELATED = "related"
    PARENT_CHILD = "parentChild"

class MetadataValueType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    XYZ = "xyz"

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
            
        value_type = values['metadataValueType']
        
        if value_type == MetadataValueType.NUMBER:
            try:
                float(v)
            except ValueError:
                raise ValueError(f"metadataValue must be a valid number for type 'number', got: {v}")
                
        elif value_type == MetadataValueType.BOOLEAN:
            if v.lower() not in ['true', 'false']:
                raise ValueError(f"metadataValue must be 'true' or 'false' for type 'boolean', got: {v}")
                
        elif value_type == MetadataValueType.DATE:
            try:
                datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                raise ValueError(f"metadataValue must be a valid ISO date format for type 'date', got: {v}")
                
        elif value_type == MetadataValueType.XYZ:
            try:
                xyz_data = json.loads(v)
                if not isinstance(xyz_data, dict):
                    raise ValueError("XYZ data must be a JSON object")
                    
                required_keys = {'x', 'y', 'z'}
                if not required_keys.issubset(xyz_data.keys()):
                    raise ValueError(f"XYZ data must contain 'x', 'y', and 'z' keys, got: {list(xyz_data.keys())}")
                    
                for key in required_keys:
                    if not isinstance(xyz_data[key], (int, float)):
                        raise ValueError(f"XYZ coordinate '{key}' must be a number, got: {type(xyz_data[key]).__name__}")
                        
            except json.JSONDecodeError:
                raise ValueError(f"metadataValue must be valid JSON for type 'XYZ', got: {v}")
                
        return v

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
            
        value_type = values['metadataValueType']
        
        if value_type == MetadataValueType.NUMBER:
            try:
                float(v)
            except ValueError:
                raise ValueError(f"metadataValue must be a valid number for type 'number', got: {v}")
                
        elif value_type == MetadataValueType.BOOLEAN:
            if v.lower() not in ['true', 'false']:
                raise ValueError(f"metadataValue must be 'true' or 'false' for type 'boolean', got: {v}")
                
        elif value_type == MetadataValueType.DATE:
            try:
                datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                raise ValueError(f"metadataValue must be a valid ISO date format for type 'date', got: {v}")
                
        elif value_type == MetadataValueType.XYZ:
            try:
                xyz_data = json.loads(v)
                if not isinstance(xyz_data, dict):
                    raise ValueError("XYZ data must be a JSON object")
                    
                required_keys = {'x', 'y', 'z'}
                if not required_keys.issubset(xyz_data.keys()):
                    raise ValueError(f"XYZ data must contain 'x', 'y', and 'z' keys, got: {list(xyz_data.keys())}")
                    
                for key in required_keys:
                    if not isinstance(xyz_data[key], (int, float)):
                        raise ValueError(f"XYZ coordinate '{key}' must be a number, got: {type(xyz_data[key]).__name__}")
                        
            except json.JSONDecodeError:
                raise ValueError(f"metadataValue must be valid JSON for type 'XYZ', got: {v}")
                
        return v

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
