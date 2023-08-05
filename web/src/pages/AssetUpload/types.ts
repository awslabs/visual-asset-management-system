/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Dispatch } from "react";

type UpdateAssetIdAction = {
    type: "UPDATE_ASSET_ID";
    payload: string;
};

type UpdateAssetDatabaseAction = {
    type: "UPDATE_ASSET_DATABASE";
    payload: string;
};

type UpdateAssetDistributableAction = {
    type: "UPDATE_ASSET_DISTRIBUTABLE";
    payload: boolean;
};

type UpdateAssetDescription = {
    type: "UPDATE_ASSET_DESCRIPTION";
    payload: string;
};

type UpdateAssetComment = {
    type: "UPDATE_ASSET_COMMENT";
    payload: string;
};

type UpdateAssetType = {
    type: "UPDATE_ASSET_TYPE";
    payload: string;
};

type UpdateAssetPipelines = {
    type: "UPDATE_ASSET_PIPELINES";
    payload: string[];
};

type UpdateAssetPreviewLocation = {
    type: "UPDATE_ASSET_PREVIEW_LOCATION";
    payload: {
        Bucket?: string;
        Key?: string;
    };
};

type UpdateAssetPreview = {
    type: "UPDATE_ASSET_PREVIEW";
    payload: File;
};

type UpdateAssetDirectoryHandle = {
    type: "UPDATE_ASSET_DIRECTORY_HANDLE";
    payload: any;
};

type UpdateAssetFiles = {
    type: "UPDATE_ASSET_FILES";
    payload: FileUploadTableItem[];
};

type UpdateAssetName = {
    type: "UPDATE_ASSET_NAME";
    payload: string;
};

type UpdateAssetBucket = {
    type: "UPDATE_ASSET_BUCKET";
    payload: string;
};

type UpdateAssetKey = {
    type: "UPDATE_ASSET_KEY";
    payload: string;
};

type UpdateAssetIsMultiFile = {
    type: "UPDATE_ASSET_IS_MULTI_FILE";
    payload: boolean;
};

type UpdatePageValidity = {
    type: "UPDATE_PAGE_VALIDITY";
    payload: boolean;
};

export type AssetDetailAction =
    | UpdateAssetIdAction
    | UpdateAssetDatabaseAction
    | UpdateAssetDistributableAction
    | UpdateAssetDescription
    | UpdateAssetComment
    | UpdateAssetType
    | UpdateAssetPipelines
    | UpdateAssetPreviewLocation
    | UpdateAssetPreview
    | UpdateAssetDirectoryHandle
    | UpdateAssetFiles
    | UpdateAssetName
    | UpdateAssetBucket
    | UpdateAssetKey
    | UpdateAssetIsMultiFile
    | UpdatePageValidity;

export type AssetDetailContextType = {
    assetDetailState: AssetDetail;
    assetDetailDispatch: Dispatch<AssetDetailAction>;
};

export interface AssetDetail {
    assetId: string;
    assetName?: string;
    assetType?: string;
    bucket?: string;
    databaseId?: string;
    description?: string;
    isDistributable: boolean;
    isMultiFile: boolean;
    key?: string;
    pageValid: boolean;
    previewLocation?: {
        Bucket?: string;
        Key?: string;
    };
    specifiedPipelines?: string[];
    Asset?: FileUploadTableItem[];
    Comment?: string;
    DirectoryHandle?: any;
    Preview?: File;
}

export interface FileUploadTableItem {
    name: string;
    handle?: any;
    index: number;
    size: number;
    relativePath: string;
    status: "Queued" | "In Progress" | "Completed" | "Failed";
    progress: number;
    startedAt?: number;
    loaded: number;
    total: number;
}
