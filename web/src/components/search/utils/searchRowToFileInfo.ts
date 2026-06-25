/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { FileInfo } from "../../../visualizerPlugin/core/types";
import { PluginRegistry } from "../../../visualizerPlugin/core/PluginRegistry";

/**
 * Maps a flattened file-mode search row to a FileInfo.
 * Each row carries its own assetId/databaseId (Decision #3), so the FileInfo does too —
 * this is what lets a multi-file selection span assets in one viewer call.
 */
export function searchRowToFileInfo(row: Record<string, any>): FileInfo {
    const key = row.str_key as string;
    const filename = key?.split("/").pop() || key;
    return {
        filename,
        key,
        isDirectory: false,
        assetId: row.str_assetid,
        databaseId: row.str_databaseid,
        size: row.num_filesize || row.num_size,
        dateCreatedCurrentVersion: row.date_lastmodified,
        isArchived: row.bool_archived === true,
        primaryType: row.str_primarytype ?? null,
        // versionId omitted -> viewer resolves the current version
    };
}

/** True if ANY enabled plugin can render this extension. Source of truth: PluginRegistry. */
export function isViewableExtension(ext?: string): boolean {
    if (!ext) return false;
    const registry = PluginRegistry.getInstance();
    return registry.getCompatibleViewers([ext.toLowerCase()], false, false).length > 0;
}
