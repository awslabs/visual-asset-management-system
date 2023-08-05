/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createContext, useContext, useReducer } from "react";

import type { AssetDetail, AssetDetailAction } from "./types";

const DEFAULT_STATE: AssetDetail = {
    assetId: "",
    isMultiFile: false,
    isDistributable: true,
    pageValid: false,
};

const AssetDetailContext = createContext<AssetDetail>(DEFAULT_STATE);
const AssetDetailDispatchContext = createContext<React.Dispatch<AssetDetailAction> | null>(null);

export function AssetUploadProvider({ children }: { children: React.ReactNode }) {
    const [state, dispatch] = useReducer(assetDetailReducer, DEFAULT_STATE);
    return (
        <AssetDetailContext.Provider value={state}>
            <AssetDetailDispatchContext.Provider value={dispatch}>
                {children}
            </AssetDetailDispatchContext.Provider>
        </AssetDetailContext.Provider>
    );
}

export function useAssetUploadState(): [AssetDetail, React.Dispatch<AssetDetailAction>] {
    const state = useContext(AssetDetailContext);
    const dispatch = useContext(AssetDetailDispatchContext);
    if (!dispatch) {
        throw new Error("dispatch context has not been initialized.");
    }
    return [state, dispatch];
}

export const assetDetailReducer = (
    assetDetailState: AssetDetail,
    assetDetailAction: AssetDetailAction
): AssetDetail => {
    switch (assetDetailAction.type) {
        case "UPDATE_ASSET_ID":
            return {
                ...assetDetailState,
                assetId: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DATABASE":
            return {
                ...assetDetailState,
                databaseId: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DISTRIBUTABLE":
            return {
                ...assetDetailState,
                isDistributable: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DESCRIPTION":
            return {
                ...assetDetailState,
                description: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_COMMENT":
            return {
                ...assetDetailState,
                Comment: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_TYPE":
            return {
                ...assetDetailState,
                assetType: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_PIPELINES":
            return {
                ...assetDetailState,
                specifiedPipelines: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_PREVIEW_LOCATION":
            return {
                ...assetDetailState,
                previewLocation: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_PREVIEW":
            return {
                ...assetDetailState,
                Preview: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_DIRECTORY_HANDLE":
            return {
                ...assetDetailState,
                DirectoryHandle: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_FILES":
            return {
                ...assetDetailState,
                Asset: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_NAME":
            return {
                ...assetDetailState,
                assetName: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_BUCKET":
            return {
                ...assetDetailState,
                bucket: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_KEY":
            return {
                ...assetDetailState,
                key: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_IS_MULTI_FILE":
            return {
                ...assetDetailState,
                isMultiFile: assetDetailAction.payload,
            };
        case "UPDATE_PAGE_VALIDITY":
            return {
                ...assetDetailState,
                pageValid: assetDetailAction.payload,
            };
        default:
            return assetDetailState;
    }
};
