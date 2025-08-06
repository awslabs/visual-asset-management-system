# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from customLogging.logger import safeLogger
from common.validators import validate, relative_file_path_pattern, id_pattern, object_name_pattern
from typing import Dict, List, Optional, Literal, Union, Any
from typing_extensions import Annotated
from pydantic import Json, EmailStr, PositiveInt, Field, Extra
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from aws_lambda_powertools.utilities.parser.models import (
    APIGatewayProxyEventV2Model
)

logger = safeLogger(service_name="AssetModelsV3")

########################Common Asset Models##########################

class AssetLocationModel(BaseModel, extra=Extra.ignore):
    """Model for asset location in S3"""
    Key: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)

class AssetPreviewLocationModel(BaseModel, extra=Extra.ignore):
    """Model for asset preview location in S3"""
    Key: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)

class CurrentVersionModel(BaseModel, extra=Extra.ignore):
    """Model for current version information"""
    Version: str
    DateModified: str
    Comment: str = ""
    description: str = ""
    specifiedPipelines: List[str] = []
    createdBy: str = "system"

class AssetVersionListItemModel(BaseModel, extra=Extra.ignore):
    """Model for individual version items in version lists"""
    Version: str
    DateModified: str
    Comment: str = ""
    description: str = ""
    specifiedPipelines: List[str] = []
    createdBy: str = "system"
    isCurrent: bool = False
    fileCount: int = 0  # Number of available files for this version

######################## Create Asset API Models ##########################
class CreateAssetRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for creating a new asset (metadata only)"""
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    assetId: Optional[str] = Field(None, min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    s3AssetBucket: Optional[str] = None
    assetName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    isDistributable: bool
    tags: Optional[list[str]] = []
    bucketExistingKey: Optional[str] = None  # Optional existing key in the S3 bucket

    @root_validator
    def validate_fields(cls, values):
        #Validate fields that require more scrutiny past the basic data type (str, bool, etc.) or custom validation logic
        logger.info("Validating custom parameters")
        (valid, message) = validate({
            'tags': {
                'value': values.get('tags'), 
                'validator': 'STRING_256_ARRAY',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)

        return values

class CreateAssetResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for creating a new asset"""
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    message: str

######################## Initialize Upload API Models ##########################
class UploadFileModel(BaseModel, extra=Extra.ignore):
    """Model for file to be uploaded"""
    relativeKey: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    file_size: Optional[PositiveInt] = None
    num_parts: Optional[PositiveInt] = None
    
    @root_validator
    def validate_size_or_parts(cls, values):
        """Ensure either file_size or num_parts is provided"""
        if values.get('file_size') is None and values.get('num_parts') is None:
            raise ValueError("Either file_size or num_parts must be provided")
        return values

class InitializeUploadRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for initializing a file upload"""
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    uploadType: Literal["assetFile", "assetPreview"]
    files: List[UploadFileModel]

    @root_validator
    def validate_fields(cls, values):
        # For asset file uploads, ensure we have at least one file
        if values.get('uploadType') == "assetFile" and (not values.get('files') or len(values.get('files')) == 0):
            message = "At least one file must be provided for asset file uploads"
            logger.error(message)
            raise ValueError(message)
            
        # For asset preview uploads, ensure we have exactly one file
        if values.get('uploadType') == "assetPreview" and (not values.get('files') or len(values.get('files')) != 1):
            message = "Exactly one file must be provided for asset preview uploads"
            logger.error(message)
            raise ValueError(message)
            
        # Check for duplicate keys
        keys = [file.relativeKey for file in values.get('files', [])]
        if len(keys) != len(set(keys)):
            message = "Duplicate relative keys are not allowed"
            logger.error(message)
            raise ValueError(message)
            
        # Validate file extensions
        for file in values.get('files', []):
            if not file.relativeKey or '.' not in file.relativeKey:
                message = f"File {file.relativeKey} must have a valid extension"
                logger.error(message)
                raise ValueError(message)
            
        return values

class UploadPartModel(BaseModel, extra=Extra.ignore):
    """Model for a part in a multipart upload"""
    PartNumber: int
    UploadUrl: str

class UploadFileResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for a file in an upload initialization"""
    relativeKey: str
    uploadIdS3: str
    numParts: int
    partUploadUrls: List[UploadPartModel]

class InitializeUploadResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for initializing a file upload"""
    uploadId: str
    files: List[UploadFileResponseModel]
    message: str

######################## Complete Upload API Models ##########################
class UploadPartCompletionModel(BaseModel, extra=Extra.ignore):
    """Model for a completed part in a multipart upload"""
    PartNumber: int
    ETag: str

class UploadFileCompletionModel(BaseModel, extra=Extra.ignore):
    """Model for a completed file in a multipart upload"""
    relativeKey: str
    uploadIdS3: str
    parts: List[UploadPartCompletionModel]

class CompleteUploadRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for completing a file upload"""
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    uploadType: Literal["assetFile", "assetPreview"]
    files: List[UploadFileCompletionModel]
    
    @root_validator
    def validate_fields(cls, values):
        # Ensure we have files to complete
        if not values.get('files') or len(values.get('files')) == 0:
            message = "At least one file must be provided to complete the upload"
            logger.error(message)
            raise ValueError(message)
            
        # For asset preview uploads, ensure we have exactly one file
        if values.get('uploadType') == "assetPreview" and len(values.get('files')) != 1:
            message = "Exactly one file must be provided for asset preview uploads"
            logger.error(message)
            raise ValueError(message)
            
        # Check for duplicate uploadIds
        upload_ids = [file.uploadIdS3 for file in values.get('files', [])]
        if len(upload_ids) != len(set(upload_ids)):
            message = "Duplicate uploadIdS3 values are not allowed"
            logger.error(message)
            raise ValueError(message)
            
        # Ensure each file has at least one part
        for file in values.get('files', []):
            if not file.parts or len(file.parts) == 0:
                message = f"File with uploadIdS3 {file.uploadIdS3} must have at least one part"
                logger.error(message)
                raise ValueError(message)
                
        return values

class FileCompletionResult(BaseModel, extra=Extra.ignore):
    """Result of a file completion operation"""
    relativeKey: str
    uploadIdS3: str
    success: bool
    error: Optional[str] = None

class ExternalFileModel(BaseModel, extra=Extra.ignore):
    """Model for an external file in a completion request"""
    relativeKey: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    tempKey: str = Field(min_length=1, strip_whitespace=True)  # Full temporary key in S3

class CompleteExternalUploadRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for completing an external file upload"""
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    uploadType: Literal["assetFile", "assetPreview"]
    files: List[ExternalFileModel]
    
    @root_validator
    def validate_fields(cls, values):
        # Ensure we have files to complete
        if not values.get('files') or len(values.get('files')) == 0:
            message = "At least one file must be provided to complete the upload"
            logger.error(message)
            raise ValueError(message)
            
        # For asset preview uploads, ensure we have exactly one file
        if values.get('uploadType') == "assetPreview" and len(values.get('files')) != 1:
            message = "Exactly one file must be provided for asset preview uploads"
            logger.error(message)
            raise ValueError(message)
            
        # Check for duplicate keys
        keys = [file.relativeKey for file in values.get('files', [])]
        if len(keys) != len(set(keys)):
            message = "Duplicate relative keys are not allowed"
            logger.error(message)
            raise ValueError(message)
            
        return values

class CompleteUploadResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for completing a file upload"""
    message: str
    uploadId: str
    assetId: str
    assetType: Optional[str] = None
    fileResults: List[FileCompletionResult] = []
    overallSuccess: bool = True

######################## Create Folder API Models ##########################
class CreateFolderRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for creating a folder in S3 for an asset"""
    relativeKey: str = Field(min_length=1, strip_whitespace=True)
    
    @validator('relativeKey')
    def validate_relative_key(cls, v):
        """Ensure the relative key ends with a slash (folder prefix)"""
        if not v.endswith('/'):
            raise ValueError("Relative key must end with a slash to represent a folder")
        return v

class CreateFolderResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for creating a folder in S3"""
    message: str
    relativeKey: str

######################## Asset Files API Models ##########################

class AssetFileItemModel(BaseModel, extra=Extra.ignore):
    """Base model for file/folder items"""
    fileName: str
    key: str
    relativePath: str
    isFolder: bool
    size: Optional[int] = None
    dateCreatedCurrentVersion: str
    versionId: str
    storageClass: Optional[str] = None  # To identify archived files
    isArchived: bool = False  # Computed field based on metadata
    currentAssetVersionFileVersionMismatch: bool = False  # Indicates if file version doesn't match asset version
    primaryType: Optional[str] = None  # Primary type metadata from S3
    previewFile: Optional[str] = ""  # Path to preview file for this file

class ListAssetFilesRequestModel(BaseModel, extra=Extra.ignore):
    """Query parameters for listing asset files"""
    maxItems: Optional[int] = Field(default=1000, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=1000, ge=1, le=1000)
    startingToken: Optional[str] = None
    prefix: Optional[str] = None
    includeArchived: Optional[bool] = Field(default=False)  # Show archived files

class ListAssetFilesResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for listing asset files"""
    items: List[AssetFileItemModel]
    nextToken: Optional[str] = None

class FileInfoRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for getting detailed file information"""
    filePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    includeVersions: Optional[bool] = Field(default=False)

class FileVersionModel(BaseModel, extra=Extra.ignore):
    """Model for individual file version information"""
    versionId: str
    lastModified: str
    size: int
    isLatest: bool
    storageClass: str = 'STANDARD'
    etag: Optional[str] = None
    isArchived: bool = False
    currentAssetVersionFileVersionMismatch: Optional[bool] = None  # Indicates if file version doesn't match asset version

class FileInfoResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for detailed file information"""
    fileName: str
    key: str
    relativePath: str
    isFolder: bool
    size: Optional[int] = None
    contentType: Optional[str] = None
    lastModified: str
    etag: Optional[str] = None
    storageClass: Optional[str] = None
    isArchived: bool = False
    primaryType: Optional[str] = None  # Primary type metadata from S3
    previewFile: Optional[str] = ""  # Path to preview file for this file
    versions: Optional[List[FileVersionModel]] = None

class MoveFileRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for moving/renaming files"""
    sourcePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    destinationPath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)

class CopyFileRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for copying files within same database"""
    sourcePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    destinationPath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    destinationAssetId: Optional[str] = Field(None, min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)

class ArchiveFileRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for archiving files (soft delete)"""
    filePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    isPrefix: Optional[bool] = Field(default=False)  # Archive all files under prefix

class UnarchiveFileRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for unarchiving files (restore from soft delete)"""
    filePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)

class DeleteFileRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for permanently deleting files"""
    filePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    isPrefix: Optional[bool] = Field(default=False)  # Delete all files under prefix
    confirmPermanentDelete: bool = Field(default=False)  # Safety confirmation

class FileOperationResponseModel(BaseModel, extra=Extra.ignore):
    """Generic response model for file operations"""
    success: bool
    message: str
    affectedFiles: List[str] = []

class RevertFileVersionRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for reverting a file to a previous version"""
    filePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)

class RevertFileVersionResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for reverting a file version"""
    success: bool
    message: str
    filePath: str
    revertedFromVersionId: str
    newVersionId: str

class SetPrimaryFileRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for setting a file's primary type metadata"""
    filePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    primaryType: str = Field(strip_whitespace=True)  # Empty string or one of the allowed values
    primaryTypeOther: Optional[str] = Field(None, strip_whitespace=True)  # Required when primaryType is 'other'
    
    @root_validator
    def validate_fields(cls, values):
        # Convert primaryType to lowercase
        primary_type = values.get('primaryType', '').lower()
        values['primaryType'] = primary_type
        
        # Convert primaryTypeOther to lowercase if provided
        if values.get('primaryTypeOther'):
            values['primaryTypeOther'] = values['primaryTypeOther'].lower()
        
        # Validate primaryType value
        allowed_values = ['', 'primary', 'lod1', 'lod2', 'lod3', 'lod4', 'lod5', 'other']
        if primary_type not in allowed_values:
            raise ValueError(f"primaryType must be one of: {', '.join(allowed_values)}")
        
        # Validate primaryTypeOther is provided when primaryType is 'other'
        if primary_type == 'other' and not values.get('primaryTypeOther'):
            raise ValueError("primaryTypeOther is required when primaryType is 'other'")
        
        # Validate primaryTypeOther is not provided when primaryType is not 'other'
        if primary_type != 'other' and values.get('primaryTypeOther'):
            raise ValueError("primaryTypeOther should only be provided when primaryType is 'other'")
        
        # Validate primaryTypeOther length
        if values.get('primaryTypeOther'):
            (valid, message) = validate({
                'primaryTypeOther': {
                    'value': values.get('primaryTypeOther'),
                    'validator': 'STRING_30'
                }
            })
            if not valid:
                raise ValueError(message)
        
        return values

class SetPrimaryFileResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for setting a file's primary type metadata"""
    success: bool
    message: str
    filePath: str
    primaryType: Optional[str] = None  # The primary type that was set

class DeleteAssetPreviewResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for deleting an asset preview"""
    success: bool
    message: str
    assetId: str

class DeleteAuxiliaryPreviewAssetFilesRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for deleting auxiliary preview asset files"""
    filePath: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)

class DeleteAuxiliaryPreviewAssetFilesResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for deleting auxiliary preview asset files"""
    success: bool
    message: str
    filePath: str
    deletedCount: int  # Number of auxiliary files deleted

######################## Ingest Asset API Models ##########################
class IngestAssetInitializeRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for initializing an asset ingest operation"""
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    assetName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    isDistributable: bool = True
    tags: Optional[list[str]] = []
    files: List[UploadFileModel]
    
    @root_validator
    def validate_fields(cls, values):
        # Validate tags
        logger.info("Validating custom parameters")
        (valid, message) = validate({
            'tags': {
                'value': values.get('tags'), 
                'validator': 'STRING_256_ARRAY',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
            
        # Ensure we have at least one file
        if not values.get('files') or len(values.get('files')) == 0:
            message = "At least one file must be provided for asset ingest"
            logger.error(message)
            raise ValueError(message)
            
        # Check for duplicate keys
        keys = [file.relativeKey for file in values.get('files', [])]
        if len(keys) != len(set(keys)):
            message = "Duplicate relative keys are not allowed"
            logger.error(message)
            raise ValueError(message)
            
        # Validate file keys start with assetId
        assetId = values.get('assetId')
        for file in values.get('files', []):
            if not file.relativeKey.startswith(f"{assetId}/"):
                message = f"File relative key {file.relativeKey} must start with assetId/{assetId}/"
                logger.error(message)
                raise ValueError(message)
            
        return values

class IngestAssetInitializeResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for initializing an asset ingest operation"""
    message: str
    uploadId: str
    files: List[UploadFileResponseModel]

class IngestAssetCompleteRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for completing an asset ingest operation"""
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace=True, pattern=id_pattern)
    assetName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace=True)
    isDistributable: bool = True
    tags: Optional[list[str]] = []
    uploadId: str
    files: List[UploadFileCompletionModel]
    
    @root_validator
    def validate_fields(cls, values):
        # Validate tags
        logger.info("Validating custom parameters")
        (valid, message) = validate({
            'tags': {
                'value': values.get('tags'), 
                'validator': 'STRING_256_ARRAY',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
            
        # Ensure we have files to complete
        if not values.get('files') or len(values.get('files')) == 0:
            message = "At least one file must be provided to complete the ingest"
            logger.error(message)
            raise ValueError(message)
            
        # Check for duplicate uploadIds
        upload_ids = [file.uploadIdS3 for file in values.get('files', [])]
        if len(upload_ids) != len(set(upload_ids)):
            message = "Duplicate uploadIdS3 values are not allowed"
            logger.error(message)
            raise ValueError(message)
            
        # Ensure each file has at least one part
        for file in values.get('files', []):
            if not file.parts or len(file.parts) == 0:
                message = f"File with uploadIdS3 {file.uploadIdS3} must have at least one part"
                logger.error(message)
                raise ValueError(message)
                
        return values

class IngestAssetCompleteResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for completing an asset ingest operation"""
    message: str
    uploadId: str
    assetId: str
    fileResults: List[FileCompletionResult] = []
    overallSuccess: bool = True

######################## Asset Service API Models ##########################
class GetAssetRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for getting a single asset"""
    showArchived: Optional[bool] = False

class GetAssetsRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for listing assets"""
    maxItems: Optional[int] = Field(default=1000, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=1000, ge=1, le=1000) 
    startingToken: Optional[str] = None
    showArchived: Optional[bool] = False

class UpdateAssetRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for updating an asset"""
    assetName: Optional[str] = Field(None, min_length=1, max_length=256, pattern=object_name_pattern)
    description: Optional[str] = Field(None, min_length=4, max_length=256)
    isDistributable: Optional[bool] = None
    tags: Optional[List[str]] = None
    
    @root_validator
    def validate_fields(cls, values):
        # Validate tags if provided
        if values.get('tags') is not None:
            logger.info("Validating tags")
            (valid, message) = validate({
                'tags': {
                    'value': values.get('tags'), 
                    'validator': 'STRING_256_ARRAY',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        # Ensure at least one field is provided for update
        if not any(values.get(field) is not None for field in ['assetName', 'description', 'isDistributable', 'tags']):
            raise ValueError("At least one field must be provided for update")
            
        return values

class ArchiveAssetRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for archiving an asset (soft delete)"""
    confirmArchive: bool = Field(default=False)
    reason: Optional[str] = Field(None, max_length=256)  # Optional reason for archiving

class DeleteAssetRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for permanently deleting an asset"""
    confirmPermanentDelete: bool = Field(default=False)  # Stronger confirmation required
    reason: Optional[str] = Field(None, max_length=256)  # Optional reason for deletion
    
    @validator('confirmPermanentDelete')
    def validate_confirmation(cls, v):
        """Ensure confirmation is provided for permanent deletion"""
        if not v:
            raise ValueError("confirmPermanentDelete must be true for permanent deletion")
        return v

class AssetResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for asset data"""
    databaseId: str
    assetId: str
    assetName: str
    description: str
    isDistributable: bool
    tags: Optional[List[str]] = []
    assetType: Optional[str] = None
    status: Optional[str] = "active" #Used for determining archived vs non-archived (active)
    bucketId: str = None
    bucketName: str = None
    currentVersion: Optional[CurrentVersionModel] = None
    assetLocation: Optional[AssetLocationModel] = None
    previewLocation: Optional[AssetPreviewLocationModel] = None
    archivedAt: Optional[str] = None
    archivedBy: Optional[str] = None
    archivedReason: Optional[str] = None

class AssetOperationResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for asset operations (update, archive, delete)"""
    success: bool
    message: str
    assetId: str
    operation: Literal["archive", "delete", "update"]
    timestamp: str

######################## Asset Versions API Models ##########################
class AssetFileVersionItemModel(BaseModel, extra=Extra.ignore):
    """Model for a file in an asset version"""
    relativeKey: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    versionId: str  # S3 version ID
    isArchived: bool = False

class CreateAssetVersionRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for creating a new asset version"""
    useLatestFiles: bool = False  # If true, use latest files in S3 bucket
    files: Optional[List[AssetFileVersionItemModel]] = None  # List of files and their S3 versions
    comment: str = Field(min_length=1, max_length=256, strip_whitespace=True)  # Required comment for the version
    
    @root_validator
    def validate_fields(cls, values):
        # If not using latest files, ensure file list is provided
        if not values.get('useLatestFiles') and (not values.get('files') or len(values.get('files')) == 0):
            message = "Either useLatestFiles must be true or a list of files must be provided"
            logger.error(message)
            raise ValueError(message)
            
        # If using latest files, file list should be empty
        if values.get('useLatestFiles') and values.get('files') and len(values.get('files')) > 0:
            message = "When useLatestFiles is true, files list should not be provided"
            logger.error(message)
            raise ValueError(message)
            
        # Check for duplicate keys if files provided
        if values.get('files'):
            keys = [file.relativeKey for file in values.get('files')]
            if len(keys) != len(set(keys)):
                message = "Duplicate relative keys are not allowed"
                logger.error(message)
                raise ValueError(message)
                
        return values

class RevertAssetVersionRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for reverting to a previous asset version"""
    assetVersionId: str = Field(min_length=1, strip_whitespace=True)  # The version ID to revert to
    comment: str = Field(min_length=1, max_length=256, strip_whitespace=True)  # comment for the new version

class GetAssetVersionRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for getting a specific asset version"""
    assetVersionId: str = Field(min_length=1, strip_whitespace=True)  # The version ID to get

class GetAssetVersionsRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for listing asset versions"""
    maxItems: Optional[int] = Field(default=100, ge=1, le=1000)
    pageSize: Optional[int] = Field(default=100, ge=1, le=1000)
    startingToken: Optional[str] = None

class AssetVersionFileModel(BaseModel, extra=Extra.ignore):
    """Model for a file in an asset version response"""
    relativeKey: str
    versionId: str
    isPermanentlyDeleted: bool = False  # Whether the file version was permanently deleted
    isLatestVersionArchived: bool = False  # Whether the latest version of this file is archived
    size: Optional[int] = None
    lastModified: Optional[str] = None
    etag: Optional[str] = None

class AssetVersionResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for a specific asset version"""
    assetId: str
    assetVersionId: str
    dateCreated: str
    comment: Optional[str] = None
    files: List[AssetVersionFileModel] = []
    createdBy: Optional[str] = None

class AssetVersionsListResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for listing asset versions"""
    versions: List[AssetVersionListItemModel] = []  # List of properly typed version items
    nextToken: Optional[str] = None

class AssetVersionOperationResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for asset version operations (create, revert)"""
    success: bool
    message: str
    assetId: str
    assetVersionId: str
    operation: Literal["create", "revert"]
    timestamp: str
    skippedFiles: Optional[List[str]] = None  # Files that couldn't be processed

######################## Download Asset API Models ##########################
class DownloadAssetRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for downloading asset files or previews"""
    downloadType: Literal["assetFile", "assetPreview"]
    key: Optional[str] = Field(None, min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)
    versionId: Optional[str] = None  # For assetFile only, get specific version
    
    @root_validator
    def validate_fields(cls, values):
        download_type = values.get('downloadType')
        version_id = values.get('versionId')
        
        # Version ID only allowed for assetFile downloads
        if download_type == "assetPreview" and version_id:
            raise ValueError("versionId is not allowed for assetPreview downloads")
            
        return values

class DownloadAssetResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for asset download"""
    downloadUrl: str
    expiresIn: int = 86400  # URL expiration in seconds (24 hours)
    downloadType: Literal["assetFile", "assetPreview"]
    versionId: Optional[str] = None
    message: str = "Download URL generated successfully"

######################## DynamoDB Table Models ##########################
class AssetUploadTableModel(BaseModel, extra=Extra.ignore):
    """Model for the asset upload tracking table"""
    uploadId: str
    assetId: str
    databaseId: str
    uploadType: Literal["assetFile", "assetPreview"]
    createdAt: str  # ISO format timestamp
    expiresAt: int  # TTL for DynamoDB record
    totalFiles: int  # Total number of files in the upload
    totalParts: int  # Total number of parts across all files
    status: str = "initialized"  # Upload status (initialized, completed, failed)
    isExternalUpload: bool = False  # Flag for external uploads
    temporaryPrefix: Optional[str] = None  # Base temporary prefix for external uploads
    
    def to_dict(self):
        """Convert model to dictionary for DynamoDB storage"""
        result = {
            "uploadId": self.uploadId,
            "assetId": self.assetId,
            "databaseId": self.databaseId,
            "uploadType": self.uploadType,
            "createdAt": self.createdAt,
            "expiresAt": self.expiresAt,
            "totalFiles": self.totalFiles,
            "totalParts": self.totalParts,
            "status": self.status,
            "isExternalUpload": self.isExternalUpload
        }
        
        # Add temporaryPrefix only if it's provided
        if self.temporaryPrefix:
            result["temporaryPrefix"] = self.temporaryPrefix
            
        return result
