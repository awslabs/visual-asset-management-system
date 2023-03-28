/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { EntityPropTypes } from "./EntityPropTypes";

export function VersionEntity(props) {
    const {
        Comment,
        S3Version,
        Version,
        description,
        specifiedPipelines,
        previewLocation,
        DateModified,
        FileSize,
    } = props;
    this.Comment = Comment;
    this.S3Version = S3Version;
    this.Version = Version;
    this.description = description;
    this.specifiedPipelines = specifiedPipelines;
    this.previewLocation = previewLocation;
    this.DateModified = DateModified;
    this.FileSize = FileSize;
}

VersionEntity.propTypes = {
    Comment: EntityPropTypes.STRING_64,
    S3Version: EntityPropTypes.STRING_32,
    Version: EntityPropTypes.STRING_32,
    description: EntityPropTypes.STRING_256,
    specifiedPipelines: EntityPropTypes.ENTITY_ID_ARRAY,
    previewLocation: EntityPropTypes.TYPED_OBJECT.bind(null, LocationEntity),
    DateModified: EntityPropTypes.STRING_64,
    FileSize: EntityPropTypes.STRING_32,
};

export function LocationEntity(props) {
    const { databaseId, description } = props;
    this.databaseId = databaseId;
    this.description = description;
}

LocationEntity.propTypes = {
    Bucket: EntityPropTypes.STRING_256,
    Key: EntityPropTypes.STRING_256,
};

export default function AssetEntity(props) {
    const {
        assetId,
        databaseId,
        description,
        bucket,
        key,
        assetType,
        specifiedPipelines,
        isDistributable,
        Comment,
        previewLocation,
        asset,
        preview,
    } = props;
    this.assetId = assetId;
    this.databaseId = databaseId;
    this.description = description;
    this.bucket = bucket;
    this.key = key;
    this.assetType = assetType;
    this.specifiedPipelines = specifiedPipelines;
    this.isDistributable = isDistributable;
    this.Comment = Comment;
    this.previewLocation = previewLocation;
    this.Asset = asset;
    this.Preview = preview;
}

AssetEntity.propTypes = {
    assetId: EntityPropTypes.ENTITY_ID,
    databaseId: EntityPropTypes.ENTITY_ID,
    description: EntityPropTypes.STRING_256,
    bucket: EntityPropTypes.STRING_256,
    key: EntityPropTypes.STRING_256,
    assetType: EntityPropTypes.FILE_TYPE,
    specifiedPipelines: EntityPropTypes.ENTITY_ID_ARRAY,
    isDistributable: EntityPropTypes.BOOL,
    Comment: EntityPropTypes.STRING_64,
    // DateModified: EntityPropTypes.STRING_64,
    // FileSize: EntityPropTypes.STRING_32,
    //@todo add explicit definitions
    previewLocation: EntityPropTypes.OBJECT,
    Asset: EntityPropTypes.OBJECT,
    Preview: EntityPropTypes.OBJECT,
};
