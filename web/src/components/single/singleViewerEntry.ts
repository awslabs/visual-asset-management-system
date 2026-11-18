/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** The fields of a file the viewer entry is built from. */
export interface ViewerEntrySource {
    key: string;
    versionId?: string;
    previewFile?: string;
}

/**
 * The single-file entry the file page hands to the viewer.
 *
 * The Preview tab swaps in the preview file's key. A preview file is its own S3 object with its own
 * versions, so the source file's S3 version id never applies to it and is dropped (the request then
 * resolves the preview object's current version, as the file-manager thumbnail does). A file viewed
 * through an ASSET version is fetched by that version rather than pinned to an S3 version id.
 */
export function buildSingleViewerEntry<T extends ViewerEntrySource>(
    file: T,
    viewType: string | null,
    assetVersionId?: string
): T {
    const showingPreview = viewType === "preview" && !!file.previewFile;
    return {
        ...file,
        versionId: assetVersionId || showingPreview ? undefined : file.versionId,
        key: showingPreview ? (file.previewFile as string) : file.key,
    };
}
