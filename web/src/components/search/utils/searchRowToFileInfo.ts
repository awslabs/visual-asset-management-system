/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { FileInfo } from "../../../visualizerPlugin/core/types";
import { isViewableExtension } from "../../../visualizerPlugin/core/viewableExtensions";
import { fileIdentity } from "../../../visualizerPlugin/core/fileIdentity";

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

/**
 * Rebuild the viewer selection after a checkbox change so it spans searches.
 *
 * The checkboxes are authoritative for the result set on screen, but a file picked from an EARLIER
 * search is not represented by any checkbox once new results replace the table — a new search clears
 * the checkboxes while the selection itself is deliberately preserved. Mirroring the checkboxes alone
 * would therefore discard every earlier pick the moment the first row of a new search is checked.
 *
 * The reconciled set is "everything selected previously that this result set does not contain" plus
 * "the viewable rows checked right now", so unchecking a visible row still removes it while
 * off-screen picks from previous searches survive.
 *
 * @param previous       the running selection
 * @param currentRows    every row in the result set now on screen (checked or not)
 * @param checkedRows    the rows currently checked
 */
export function reconcileViewerSelection(
    previous: FileInfo[],
    currentRows: Record<string, any>[],
    checkedRows: Record<string, any>[]
): FileInfo[] {
    const viewable = (rows: Record<string, any>[]) =>
        rows.filter((r) => isViewableExtension(r?.str_fileext)).map((r) => searchRowToFileInfo(r));

    // Identity is database + asset + key, never the key alone: the same path exists in many assets,
    // and treating those as one file made a second selection appear to do nothing.
    const onScreen = new Set(viewable(currentRows).map(fileIdentity));
    const checkedNow = viewable(checkedRows);
    const checkedIdentities = new Set(checkedNow.map(fileIdentity));

    const carriedOver = (previous || []).filter((f) => {
        const identity = fileIdentity(f);
        return !onScreen.has(identity) && !checkedIdentities.has(identity);
    });
    return [...carriedOver, ...checkedNow];
}

/**
 * Viewer-availability lookups now live beside the registry so the asset file manager can use the
 * same memoized answers without importing from the search feature. Re-exported here because the
 * search table and its tests already import them from this module.
 */
export {
    isViewableExtension,
    clearViewableExtensionCache,
} from "../../../visualizerPlugin/core/viewableExtensions";
