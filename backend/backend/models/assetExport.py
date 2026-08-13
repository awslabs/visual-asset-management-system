# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, List, Dict, Any
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate

# Export request limits. maxAssets bounds the per-page asset fan-out: each asset in a
# page drives its own DynamoDB reads plus S3 listing/head calls, and each included file
# can carry a presigned URL, so this cap bounds both Lambda runtime and the response
# payload against the AWS Lambda synchronous response limit (6 MB). Matches the
# maximum already published in the OpenAPI spec and enforced by the CLI.
MAX_ASSETS_PER_EXPORT_PAGE = 1000
# Maximum extension filters accepted in one request. Each entry is compared against
# every file of every asset in the page.
MAX_FILE_EXTENSION_FILTERS = 100
# Maximum length of a pagination token. The token carries the serialized asset tree.
MAX_EXPORT_TOKEN_LENGTH = 400000


class AssetExportRequestModel(BaseModel, extra='ignore'):
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
    fileExtensions: Optional[List[str]] = Field(default=None, description="Filter files to only provided extensions",
                                                max_items=MAX_FILE_EXTENSION_FILTERS)
    maxAssets: int = Field(default=100, description="Maximum assets per page", ge=1, le=MAX_ASSETS_PER_EXPORT_PAGE)
    startingToken: Optional[str] = Field(default=None, description="Pagination token for subsequent requests",
                                         max_length=MAX_EXPORT_TOKEN_LENGTH)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'fileExtensions': {
                'value': values.get('fileExtensions'),
                'validator': 'STRING_256_ARRAY',
                'optional': True
            }
        })
        if not valid:
            raise ValueError(message)
        return values


class AssetExportMetadataItemModel(BaseModel, extra='ignore'):
    """Metadata item with value type"""
    valueType: str
    value: Any


class AssetExportFileModel(BaseModel, extra='ignore'):
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


class AssetExportAssetModel(BaseModel, extra='ignore'):
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


class AssetExportUnauthorizedAssetModel(BaseModel, extra='ignore'):
    """Placeholder model for unauthorized assets"""
    assetId: str
    databaseId: str
    unauthorizedAsset: bool = Field(default=True, description="Indicates this asset was not accessible")


class AssetExportRelationshipModel(BaseModel, extra='ignore'):
    """Relationship model for export with metadata"""
    parentAssetId: str
    parentAssetDatabaseId: str
    childAssetId: str
    childAssetDatabaseId: str
    assetLinkType: str
    assetLinkId: str
    assetLinkAliasId: Optional[str] = None
    metadata: Optional[Dict[str, AssetExportMetadataItemModel]] = None


class AssetExportResponseModel(BaseModel, extra='ignore'):
    """Response model for asset export"""
    assets: List[AssetExportAssetModel]
    relationships: Optional[List[AssetExportRelationshipModel]] = None
    NextToken: Optional[str] = None
    totalAssetsInTree: int
    assetsInThisPage: int
