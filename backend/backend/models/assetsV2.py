# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from customLogging.logger import safeLogger
from common.validators import validate, relative_file_path_pattern, id_pattern, object_name_pattern
from typing import Dict, List, Optional, Literal, Union
from typing_extensions import Annotated
from pydantic import  Json, JsonValue, EmailStr, PositiveInt, Field, Extra, Tag
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from aws_lambda_powertools.utilities.parser.models import (
    APIGatewayProxyEventV2Model
)

logger = safeLogger(service_name="AssetModelsV2")

########################Common Asset##########################
class AssetLinksModel(BaseModel, extra=Extra.ignore):
    parents: list[str]
    child: list[str]
    related: list[str]

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
        elif not values.get('parents') and not values.get('child') and not values.get('related'):
            message = "At least one of parents, child or related must be provided"
            logger.error(message)
            raise ValueError(message)
        return values

class UploadAssetDataDetailsNewModel(BaseModel, extra=Extra.ignore):
    assetName: str = Field(min_length=1, max_length=256, strip_whitespace = True, pattern=object_name_pattern)
    description: str = Field(min_length=4, max_length=256, strip_whitespace = True)
    isDistributable: bool
    tags: Optional[list[str]]
    assetLinks: Optional[AssetLinksModel] 

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

class UploadAssetsStage1FilesAssetModel(BaseModel, extra=Extra.ignore):
    key: str = Field(min_length=1, strip_whitespace = True, pattern=relative_file_path_pattern)
    partCount: Optional[PositiveInt]
    sizeInBytes: Optional[PositiveInt]
    isAssetPrimaryFile: bool

    @root_validator
    def validate_fields(cls, values):
        if not values.get('partCount') and not values.get('sizeInBytes'):
            message = "Either partCount or sizeInBytes must be provided"
            logger.error(message)
            raise ValueError(message)
        return values

class UploadAssetsStage1FilesPreviewModel(BaseModel, extra=Extra.ignore):
    key: str = Field(min_length=1, strip_whitespace = True, pattern=relative_file_path_pattern)
    partCount: Optional[PositiveInt]
    sizeInBytes: Optional[PositiveInt]

    @root_validator
    def validate_fields(cls, values):
        if not values.get('partCount') and not values.get('sizeInBytes'):
            message = "Either partCount or sizeInBytes must be provided"
            logger.error(message)
            raise ValueError(message)
        return values


class UploadAssetStage1NewRequestModel(BaseModel, extra=Extra.ignore):
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace = True, pattern=id_pattern)
    assetDataDetails: UploadAssetDataDetailsNewModel
    filesAsset: list[UploadAssetsStage1FilesAssetModel]
    filePreview: Optional[UploadAssetsStage1FilesPreviewModel]

    @root_validator
    def validate_fields(cls, values):
        #Loop through all filesAsset to make sure at least one asset has the isAssetPrimaryFile set as part of being a new asset upload
        #Also check to make sure we have unique keys throughout
        isAssetPrimaryFilePresent = False
        keySet = []
        for fileAsset in values.get('filesAsset', []):
            if fileAsset.key in keySet:
                message = f"Duplicate file key {fileAsset.key} found in asset file set"
                logger.error(message)
                raise ValueError(message)
            keySet.append(fileAsset.key)

            if fileAsset.isAssetPrimaryFile:
                isAssetPrimaryFilePresent = True
                break
        if not isAssetPrimaryFilePresent:
            message = "At least one file asset must have isAssetPrimaryFile set to true"
            logger.error(message)
            raise ValueError(message)
        return values

class UploadAssetStage1UpdateRequestModel(BaseModel, extra=Extra.ignore):
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace = True, pattern=id_pattern)
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace = True, pattern=id_pattern)
    filesAsset: Optional[List[UploadAssetsStage1FilesAssetModel]]
    filePreview: Optional[UploadAssetsStage1FilesPreviewModel]

    @root_validator
    def validate_fields(cls, values):
        #check to make sure we have unique keys throughout
        keySet = []
        for fileAsset in values.get('filesAsset', []):
            if fileAsset.key in keySet:
                message = f"Duplicate file key {fileAsset.key} found in asset file set"
                logger.error(message)
                raise ValueError(message)
            keySet.append(fileAsset.key)
        return values

class UploadAssetsStage1FilesPartsResponseModel(BaseModel, extra=Extra.ignore):
    PartNumber: PositiveInt
    UploadUrl: str = Field(min_length=1, strip_whitespace = True)

class UploadAssetsStage1FilesAssetPreviewResponseModel(BaseModel, extra=Extra.ignore):
    key: str = Field(min_length=1, strip_whitespace = True, pattern=relative_file_path_pattern)
    uploadId: str = Field(min_length=1, strip_whitespace = True)
    Parts: list[UploadAssetsStage1FilesPartsResponseModel]

class UploadAssetStage1ResponseModel(BaseModel, extra=Extra.ignore):
    uploadRequestId: str = Field(min_length=1, max_length=256, strip_whitespace = True)
    assetId: str = Field(min_length=4, max_length=256, strip_whitespace = True, pattern=id_pattern)
    databaseId: str = Field(min_length=4, max_length=256, strip_whitespace = True, pattern=id_pattern)
    filesAsset: Optional[List[UploadAssetsStage1FilesAssetPreviewResponseModel]]
    filePreview: Optional[UploadAssetsStage1FilesAssetPreviewResponseModel]


class UploadAssetsStage2FilesPartsModel(BaseModel, extra=Extra.ignore):
    PartNumber: PositiveInt
    ETag: str = Field(min_length=1, strip_whitespace = True)

class UploadAssetsStage2FilesAssetPreviewRequestModel(BaseModel, extra=Extra.ignore):
    key: str = Field(min_length=1, strip_whitespace = True, pattern=relative_file_path_pattern)
    uploadId: str = Field(min_length=1, strip_whitespace = True)
    Parts: list[UploadAssetsStage2FilesPartsModel]

    # @root_validator
    # def validate_fields(cls, values):
    #     #Validate fields that require more scrutiny past the basic data type (str, bool, etc.) or custom validation logic
    #     logger.info("Validating custom parameters")
    #     (valid, message) = validate({
    #         'fileKey': {
    #             'value': values.get('key'), 
    #             'validator': 'RELATIVE_FILE_PATH'
    #         }
    #     })
    #     if not valid:
    #         logger.error(message)
    #         raise ValueError(message)
    #     return values

class UploadAssetStage2RequestModel(BaseModel, extra=Extra.ignore):
    uploadRequestId: str = Field(min_length=1, max_length=256, strip_whitespace = True)
    filesAsset: Optional[List[UploadAssetsStage2FilesAssetPreviewRequestModel]]
    filePreview: Optional[UploadAssetsStage2FilesAssetPreviewRequestModel]

    @root_validator
    def validate_fields(cls, values):
        if not values.get('filesAsset') and not values.get('filePreview'):
            message = "Either filesAsset or filePreview must be provided"
            logger.error(message)
            raise ValueError(message)
        return values
    

########################DynamoDB Table Model##########################

#####AssetUploadTable#####
    # - uploadId (PK)
    # - httpMethodType (i.e. is this a update or create based on PUT/POST)
    # - assetId
    # - databaseId
    # - description
    # - isDistributable
    # - tags
    # - filesAssets
    # - - key
    # - - sizeInBytes
    # - - isAssetPrimaryFile
    # - - Parts (Dict Array)
    # - - - UploadUrl
    # - - - PartNumber
    # - filePreview 
    # - - key
    # - - sizeInBytes
    # - - Parts (Dict Array)
    # - - - UploadUrl
    # - - - PartNumber

class AssetUploadTablePartsResponseModel(BaseModel, extra=Extra.ignore):
    PartNumber: PositiveInt
    UploadUrl: str = Field(min_length=1, strip_whitespace = True)

class AssetUploadTableFilesAssetModel(BaseModel, extra=Extra.ignore):
    key: str = Field(min_length=1, strip_whitespace = True, pattern=relative_file_path_pattern)
    sizeInBytes: Optional[PositiveInt]
    isAssetPrimaryFile: bool
    uploadId: str = Field(min_length=1, strip_whitespace = True)
    StorageKey: str = Field(min_length=1, strip_whitespace = True)
    Parts: list[AssetUploadTablePartsResponseModel]

class AssetUploadTableFilePreviewModel(BaseModel, extra=Extra.ignore):
    key: str = Field(min_length=1, strip_whitespace = True, pattern=relative_file_path_pattern)
    sizeInBytes: Optional[PositiveInt]
    uploadId: str = Field(min_length=1, strip_whitespace = True)
    StorageKey: str = Field(min_length=1, strip_whitespace = True)
    Parts: list[AssetUploadTablePartsResponseModel]

class AssetDataDetailsModel(BaseModel, extra=Extra.ignore):
    assetName: Optional[str]
    description: Optional[str]
    isDistributable: Optional[bool]
    assetType: Optional[str]
    tags: Optional[list[str]]
    assetLinks: Optional[AssetLinksModel]

class AssetUploadTableModel(BaseModel, extra=Extra.ignore):
    uploadRequestId: str
    httpMethodType: str
    assetId: str
    databaseId: str
    assetDataDetails: AssetDataDetailsModel
    filesAssets: Optional[list[AssetUploadTableFilesAssetModel]]
    filePreview: Optional[AssetUploadTableFilePreviewModel]