/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    fetchDatabaseAssets,
    fetchAllAssets,
    fetchAssetS3Files,
} from "../../../services/APIService";
import { fetchAllAssetVersions } from "../../../services/AssetVersionService";

/**
 * Asset / file / version lookups for the execute wizard's selectors. These are core-entity reads,
 * so they delegate to the registered functions in `services/APIService.ts` /
 * `services/AssetVersionService.ts` (Rule 3 — only service files touch apiClient) and adapt the
 * various legacy return shapes into the orchestration module's `[ok, data]` tuple.
 */

export interface AssetSummary {
    databaseId: string;
    assetId: string;
    assetName?: string;
}

export interface AssetFileSummary {
    fileName: string;
    key: string;
    relativePath: string; // asset-relative, leading '/'
    isFolder: boolean;
    versionId?: string;
}

export interface AssetFileVersionSummary {
    versionId: string;
    relativeKey: string;
    // Version records carry additional fields (dateCreated, comment, ...) we surface opportunistically.
    [k: string]: any;
}

/** Assets in one database, or (when databaseId is empty) across all databases the caller can see. */
export async function listAssets(databaseId?: string): Promise<[boolean, AssetSummary[] | string]> {
    try {
        const result = databaseId
            ? await fetchDatabaseAssets({ databaseId })
            : await fetchAllAssets({});
        if (Array.isArray(result)) return [true, result as AssetSummary[]];
        return [false, "Failed to load assets."];
    } catch (e: any) {
        return [false, e?.message || "Failed to load assets."];
    }
}

/** Non-folder files for an asset (the wizard picks a file, not a folder). */
export async function listAssetFiles(
    databaseId: string,
    assetId: string
): Promise<[boolean, AssetFileSummary[] | string]> {
    try {
        const [ok, data] = await fetchAssetS3Files({ databaseId, assetId, includeArchived: false });
        if (ok && Array.isArray(data)) {
            return [true, (data as AssetFileSummary[]).filter((f) => !f.isFolder)];
        }
        return [false, typeof data === "string" ? data : "Failed to load files."];
    } catch (e: any) {
        return [false, e?.message || "Failed to load files."];
    }
}

/** All (VAMS) asset versions for an asset, for the optional version selector. */
export async function listAssetVersions(
    databaseId: string,
    assetId: string
): Promise<[boolean, AssetFileVersionSummary[] | string]> {
    try {
        const [ok, data] = await fetchAllAssetVersions({ databaseId, assetId });
        if (ok && data && Array.isArray((data as any).versions)) {
            return [true, (data as any).versions as AssetFileVersionSummary[]];
        }
        return [false, typeof data === "string" ? data : "Failed to load versions."];
    } catch (e: any) {
        return [false, e?.message || "Failed to load versions."];
    }
}
