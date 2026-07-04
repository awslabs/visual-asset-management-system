/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { EntityPropTypes } from "./EntityPropTypes";

interface VersionEntity {
    Comment: any;
    S3Version: any;
    Version: any;
    description: any;
    specifiedPipelines: any;
    previewLocation: any;
    DateModified: any;
    FileSize: any;
}

export function VersionEntity(this: VersionEntity, props: any) {
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

(VersionEntity as any).propTypes = {
    Comment: EntityPropTypes.STRING_64,
    S3Version: EntityPropTypes.STRING_32,
    Version: EntityPropTypes.STRING_32,
    description: EntityPropTypes.STRING_256,
    specifiedPipelines: EntityPropTypes.ENTITY_ID_ARRAY,
    previewLocation: EntityPropTypes.TYPED_OBJECT.bind(null, LocationEntity),
    DateModified: EntityPropTypes.STRING_64,
    FileSize: EntityPropTypes.STRING_32,
};

interface LocationEntity {
    databaseId: any;
    description: any;
}

export function LocationEntity(this: LocationEntity, props: any) {
    const { databaseId, description } = props;
    this.databaseId = databaseId;
    this.description = description;
}

(LocationEntity as any).propTypes = {
    Key: EntityPropTypes.STRING_256,
};

interface AssetEntity {
    assetId: any;
    databaseId: any;
    description: any;
    key: any;
    assetType: any;
    specifiedPipelines: any;
    isDistributable: any;
    Comment: any;
    previewLocation: any;
    Asset: any;
    Preview: any;
}

export default function AssetEntity(this: AssetEntity, props: any) {
    const {
        assetId,
        databaseId,
        description,
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
    this.key = key;
    this.assetType = assetType;
    this.specifiedPipelines = specifiedPipelines;
    this.isDistributable = isDistributable;
    this.Comment = Comment;
    this.previewLocation = previewLocation;
    this.Asset = asset;
    this.Preview = preview;
}

(AssetEntity as any).propTypes = {
    assetId: EntityPropTypes.ENTITY_ID,
    databaseId: EntityPropTypes.ENTITY_ID,
    description: EntityPropTypes.STRING_256,
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
