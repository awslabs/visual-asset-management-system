# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import List, Optional, Dict, Any
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from enum import Enum
import json
import re
import uuid
from datetime import datetime
import geojson

from common.validators import validate
# Import metadata types and validation from centralized metadata module
from models.metadata import MetadataValueType, validate_metadata_value_common

# Maximum tags accepted on one asset link. Tags are stored on the link record and
# echoed back on every read of it.
MAX_TAGS_PER_ASSET_LINK = 100

class RelationshipType(str, Enum):
    RELATED = "related"
    PARENT_CHILD = "parentChild"

# Asset Link Models
class CreateAssetLinkRequestModel(BaseModel, extra='ignore'):
    fromAssetId: str = Field(..., max_length=256, description="Source asset ID")
    fromAssetDatabaseId: str = Field(..., max_length=256, description="Source asset database ID")
    toAssetId: str = Field(..., max_length=256, description="Target asset ID")
    toAssetDatabaseId: str = Field(..., max_length=256, description="Target asset database ID")
    relationshipType: RelationshipType = Field(..., description="Type of relationship")
    assetLinkAliasId: Optional[str] = Field(None, max_length=128, description="Optional alias ID for multiple parent-child relationships")
    tags: Optional[List[str]] = Field(default=[], max_items=MAX_TAGS_PER_ASSET_LINK, description="Tags associated with the asset link")

    @validator('assetLinkAliasId')
    def validate_alias_for_relationship_type(cls, v, values):
        """Validate that aliasId is only used with parentChild relationships"""
        if v and values.get('relationshipType') == RelationshipType.RELATED:
            raise ValueError("assetLinkAliasId can only be used with parentChild relationships")
        return v

    @root_validator
    def validate_fields(cls, values):
        # The asset and database identifiers become DynamoDB composite keys
        # ("{databaseId}:{assetId}") and Casbin rule inputs, so they carry the same
        # rules the asset APIs enforce.
        (valid, message) = validate({
            'fromAssetId': {'value': values.get('fromAssetId'), 'validator': 'ASSET_ID'},
            'fromAssetDatabaseId': {'value': values.get('fromAssetDatabaseId'), 'validator': 'ID'},
            'toAssetId': {'value': values.get('toAssetId'), 'validator': 'ASSET_ID'},
            'toAssetDatabaseId': {'value': values.get('toAssetDatabaseId'), 'validator': 'ID'},
            'tags': {'value': values.get('tags'), 'validator': 'STRING_256_ARRAY', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        return values

class CreateAssetLinkResponseModel(BaseModel, extra='ignore'):
    assetLinkId: str = Field(..., description="Generated asset link ID")
    message: str = Field(..., description="Success message")

class AssetLinkModel(BaseModel, extra='ignore'):
    assetLinkId: str = Field(..., description="Asset link ID")
    fromAssetId: str = Field(..., description="Source asset ID")
    fromAssetDatabaseId: str = Field(..., description="Source asset database ID")
    toAssetId: str = Field(..., description="Target asset ID")
    toAssetDatabaseId: str = Field(..., description="Target asset database ID")
    relationshipType: RelationshipType = Field(..., description="Type of relationship")
    assetLinkAliasId: Optional[str] = Field(None, description="Optional alias ID for multiple parent-child relationships")
    tags: List[str] = Field(default=[], description="Tags associated with the asset link")

class GetSingleAssetLinkResponseModel(BaseModel, extra='ignore'):
    assetLink: AssetLinkModel = Field(..., description="Asset link details")
    message: str = Field(default="Success", description="Response message")

class UpdateAssetLinkRequestModel(BaseModel, extra='ignore'):
    assetLinkAliasId: Optional[str] = Field(None, max_length=128, description="Optional alias ID for multiple parent-child relationships")
    tags: List[str] = Field(default=[], max_items=MAX_TAGS_PER_ASSET_LINK, description="Updated tags for the asset link")

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'tags': {'value': values.get('tags'), 'validator': 'STRING_256_ARRAY', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        return values

class UpdateAssetLinkResponseModel(BaseModel, extra='ignore'):
    message: str = Field(..., description="Success message")

class DeleteAssetLinkResponseModel(BaseModel, extra='ignore'):
    message: str = Field(..., description="Success message")

# Request Models for Path and Query Parameters
class GetAssetLinksRequestModel(BaseModel, extra='ignore'):
    assetId: str = Field(..., max_length=256, description="Asset ID to get links for")
    databaseId: str = Field(..., max_length=256, description="Database ID")
    childTreeView: bool = Field(default=False, description="Return tree view for children")

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'assetId': {'value': values.get('assetId'), 'validator': 'ASSET_ID'},
            'databaseId': {'value': values.get('databaseId'), 'validator': 'ID'},
        })
        if not valid:
            raise ValueError(message)
        return values

class GetSingleAssetLinkRequestModel(BaseModel, extra='ignore'):
    assetLinkId: str = Field(..., max_length=256, description="Asset link ID")

    @root_validator
    def validate_fields(cls, values):
        # The link id is the table's partition key; the same rule the handler applies.
        (valid, message) = validate({
            'assetLinkId': {'value': values.get('assetLinkId'), 'validator': 'ID'},
        })
        if not valid:
            raise ValueError(message)
        return values

class DeleteAssetLinkRequestModel(BaseModel, extra='ignore'):
    assetLinkId: str = Field(..., max_length=256, description="Asset link ID to delete")

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'assetLinkId': {'value': values.get('assetLinkId'), 'validator': 'ID'},
        })
        if not valid:
            raise ValueError(message)
        return values

# Asset Links Tree View Models
class AssetNodeModel(BaseModel, extra='ignore'):
    assetId: str = Field(..., description="Asset ID")
    assetName: str = Field(..., description="Asset name")
    databaseId: str = Field(..., description="Database ID")
    assetLinkId: Optional[str] = Field(None, description="Asset link ID if applicable")
    assetLinkAliasId: Optional[str] = Field(None, description="Optional alias ID for this link")

# Simple tree node model without self-reference to avoid Pydantic issues
class AssetTreeNodeModel(BaseModel, extra='ignore'):
    assetId: str = Field(..., description="Asset ID")
    assetName: str = Field(..., description="Asset name")
    databaseId: str = Field(..., description="Database ID")
    assetLinkId: str = Field(..., description="Asset link ID")
    assetLinkAliasId: Optional[str] = Field(None, description="Optional alias ID for this link")
    children: List[Dict[str, Any]] = Field(default_factory=list, description="Child nodes in the tree")

class UnauthorizedCountsModel(BaseModel, extra='ignore'):
    related: int = Field(default=0, description="Count of unauthorized related assets")
    parents: int = Field(default=0, description="Count of unauthorized parent assets")
    children: int = Field(default=0, description="Count of unauthorized child assets")

class GetAssetLinksResponseModel(BaseModel, extra='ignore'):
    related: List[AssetNodeModel] = Field(default=[], description="Related assets")
    parents: List[AssetNodeModel] = Field(default=[], description="Parent assets")
    children: List[AssetNodeModel] = Field(default=[], description="Child assets (flat list)")
    unauthorizedCounts: UnauthorizedCountsModel = Field(default_factory=UnauthorizedCountsModel, description="Counts of unauthorized assets")
    message: str = Field(default="Success", description="Response message")

class GetAssetLinksTreeViewResponseModel(BaseModel, extra='ignore'):
    related: List[AssetNodeModel] = Field(default=[], description="Related assets")
    parents: List[AssetNodeModel] = Field(default=[], description="Parent assets")
    children: List[AssetTreeNodeModel] = Field(default=[], description="Child assets (tree structure)")
    unauthorizedCounts: UnauthorizedCountsModel = Field(default_factory=UnauthorizedCountsModel, description="Counts of unauthorized assets")
    message: str = Field(default="Success", description="Response message")
