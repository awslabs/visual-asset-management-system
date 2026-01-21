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

# Import metadata types and validation from centralized metadata module
from models.metadata import MetadataValueType, validate_metadata_value_common

class RelationshipType(str, Enum):
    RELATED = "related"
    PARENT_CHILD = "parentChild"

# Asset Link Models
class CreateAssetLinkRequestModel(BaseModel):
    fromAssetId: str = Field(..., description="Source asset ID")
    fromAssetDatabaseId: str = Field(..., description="Source asset database ID")
    toAssetId: str = Field(..., description="Target asset ID")
    toAssetDatabaseId: str = Field(..., description="Target asset database ID")
    relationshipType: RelationshipType = Field(..., description="Type of relationship")
    assetLinkAliasId: Optional[str] = Field(None, max_length=128, description="Optional alias ID for multiple parent-child relationships")
    tags: Optional[List[str]] = Field(default=[], description="Tags associated with the asset link")
    
    @validator('assetLinkAliasId')
    def validate_alias_for_relationship_type(cls, v, values):
        """Validate that aliasId is only used with parentChild relationships"""
        if v and values.get('relationshipType') == RelationshipType.RELATED:
            raise ValueError("assetLinkAliasId can only be used with parentChild relationships")
        return v

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
    assetLinkAliasId: Optional[str] = Field(None, description="Optional alias ID for multiple parent-child relationships")
    tags: List[str] = Field(default=[], description="Tags associated with the asset link")

class GetSingleAssetLinkResponseModel(BaseModel):
    assetLink: AssetLinkModel = Field(..., description="Asset link details")
    message: str = Field(default="Success", description="Response message")

class UpdateAssetLinkRequestModel(BaseModel):
    assetLinkAliasId: Optional[str] = Field(None, max_length=128, description="Optional alias ID for multiple parent-child relationships")
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

# Asset Links Tree View Models
class AssetNodeModel(BaseModel):
    assetId: str = Field(..., description="Asset ID")
    assetName: str = Field(..., description="Asset name")
    databaseId: str = Field(..., description="Database ID")
    assetLinkId: Optional[str] = Field(None, description="Asset link ID if applicable")
    assetLinkAliasId: Optional[str] = Field(None, description="Optional alias ID for this link")

# Simple tree node model without self-reference to avoid Pydantic issues
class AssetTreeNodeModel(BaseModel):
    assetId: str = Field(..., description="Asset ID")
    assetName: str = Field(..., description="Asset name")
    databaseId: str = Field(..., description="Database ID")
    assetLinkId: str = Field(..., description="Asset link ID")
    assetLinkAliasId: Optional[str] = Field(None, description="Optional alias ID for this link")
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
