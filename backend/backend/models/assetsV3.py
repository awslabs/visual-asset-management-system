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
class AssetLinksModel(BaseModel, extra=Extra.ignore):
    """Model for asset relationships (parents, children, related assets)"""
    parents: list[str] = []
    child: list[str] = []
    related: list[str] = []

    @root_validator
    def validate_fields(cls, values):
        #Validate fields that require more scrutiny past the basic data type (str, bool, etc.) or custom validation logic
        logger.info("Validating custom parameters")
        (valid, message) = validate({
            'assetLinkParentsAssetId': {
                'value': values.get('parents'), 
                'validator': 'ID_ARRAY',
                'optional': True
            },
            'assetLinkChildAssetId': {
                'value': values.get('child'), 
                'validator': 'ID_ARRAY',
                'optional': True
            },
            'assetLinkRelatedAssetId': {
                'value': values.get('related'), 
                'validator': 'ID_ARRAY',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        return values

class AssetPreviewLocationModel(BaseModel, extra=Extra.ignore):
    """Model for asset preview location in S3"""
    Key: str = Field(min_length=1, strip_whitespace=True, pattern=relative_file_path_pattern)

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
    assetLinks: Optional[AssetLinksModel] = None
    s3Bucket: Optional[str] = None  # Optional override for default S3 bucket
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
    version: Optional[str] = None
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
