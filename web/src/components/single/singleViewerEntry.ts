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

/** What the Preview tab shows when the file version being viewed is not the latest one. */
export const OLDER_VERSION_PREVIEW_NOTICE =
    "This preview belongs to the latest version of the file, not the version you are viewing.";

/**
 * Whether the Preview tab shows a preview that belongs to a different file version than the one
 * being viewed. A preview file is requested without a version ID, so it is always the preview of the
 * file's latest version. That is the right one for the latest file version and for a view through an
 * asset version (the API picks the preview for that asset version); for an older file version it is
 * not, and the page says so. An unknown latest version (not loaded yet, or the lookup failed) gives
 * no notice, because the page cannot tell.
 */
export function previewIsOfLatestVersionOnly(
    file: ViewerEntrySource,
    viewType: string | null,
    assetVersionId?: string,
    latestVersionId?: string
): boolean {
    return (
        viewType === "preview" &&
        !!file.previewFile &&
        !assetVersionId &&
        !!file.versionId &&
        !!latestVersionId &&
        file.versionId !== latestVersionId
    );
}
