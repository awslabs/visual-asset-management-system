# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class AssetExportRequestModel(BaseModel):
    """Request model for asset export with filtering options"""
    generatePresignedUrls: bool = Field(default=False, description="Generate presigned URLs for included files")
    includeFolderFiles: bool = Field(default=False, description="Include folder files in export")
    includeOnlyPrimaryTypeFiles: bool = Field(default=False, description="Include only files with primaryType set")
    includeFileMetadata: bool = Field(default=True, description="Include file metadata")
    includeAssetLinkMetadata: bool = Field(default=True, description="Include asset link metadata")
    includeAssetMetadata: bool = Field(default=True, description="Include asset metadata")
    fetchAssetRelationships: bool = Field(default=True, description="Fetch asset relationships and linked asset details")
    fetchEntireChildrenSubtrees: bool = Field(default=False, description="Fetch entire children relationship sub-trees")
    includeParentRelationships: bool = Field(default=False, description="Include parent relationships in the relationship data")
    includeArchivedFiles: bool = Field(default=False, description="Include archived files in export")
    fileExtensions: Optional[List[str]] = Field(default=None, description="Filter files to only provided extensions")
    maxAssets: int = Field(default=100, description="Maximum assets per page", ge=1, le=1000)
    nextToken: Optional[str] = Field(default=None, description="Pagination token for subsequent requests")


class AssetExportMetadataItemModel(BaseModel):
    """Metadata item with value type"""
    valueType: str
    value: Any


class AssetExportFileModel(BaseModel):
    """File model for export"""
    fileName: str
    key: str
    relativePath: str
    isFolder: bool
    size: Optional[int] = None
    dateCreatedCurrentVersion: str
    versionId: str
    storageClass: str
    isArchived: bool
    currentAssetVersionFileVersionMismatch: bool
    primaryType: Optional[str] = None
    previewFile: str
    metadata: Optional[Dict[str, AssetExportMetadataItemModel]] = None
    presignedFileDownloadUrl: Optional[str] = None
    presignedFileDownloadExpiresIn: Optional[int] = None


class AssetExportAssetModel(BaseModel):
    """Asset model for export with all related data"""
    is_root_lookup_asset: bool
    id: str
    databaseid: str
    assetid: str
    bucketid: str
    assetname: str
    bucketname: str
    bucketprefix: str
    assettype: str
    description: str
    isdistributable: bool
    tags: List[str]
    asset_version_id: str
    asset_version_createdate: str
    asset_version_comment: str
    archived: bool
    metadata: Optional[Dict[str, AssetExportMetadataItemModel]] = None
    files: List[AssetExportFileModel]


class AssetExportUnauthorizedAssetModel(BaseModel):
    """Placeholder model for unauthorized assets"""
    assetId: str
    databaseId: str
    unauthorizedAsset: bool = Field(default=True, description="Indicates this asset was not accessible")


class AssetExportRelationshipModel(BaseModel):
    """Relationship model for export with metadata"""
    parentAssetId: str
    parentAssetDatabaseId: str
    childAssetId: str
    childAssetDatabaseId: str
    assetLinkType: str
    assetLinkId: str
    assetLinkAliasId: Optional[str] = None
    metadata: Optional[Dict[str, AssetExportMetadataItemModel]] = None


class AssetExportResponseModel(BaseModel):
    """Response model for asset export"""
    assets: List[AssetExportAssetModel]
    relationships: Optional[List[AssetExportRelationshipModel]] = None
    nextToken: Optional[str] = None
    totalAssetsInTree: int
    assetsInThisPage: int
