/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Where the Point Cloud Potree Viewer pipeline writes a file's octree, inside the file's viewer data. */
export const POTREE_VIEWER_METADATA = "PotreeViewer/metadata.json";

/**
 * The auxiliary preview stream URL of one file's viewer data, `{file key}/preview/{dataPath}`.
 *
 * The file is named by the key the host gives (`FileInfo.key`: the full S3 key the file list returns,
 * or the file's path in the asset, with its leading `/`, that a host without the full key has, such as
 * search results). The key is sent as given, never cut down to a guessed path in the asset: the API
 * decides which file of the asset the path names and refuses a path that names none. A leading `/`
 * marks a path in the asset and is kept: without it the API reads a path that starts with the asset's
 * location key (a subfolder named like the asset) as the full key of another file. Repeated leading
 * slashes are kept too: `//x` is the file whose key has an empty segment right after the location, and
 * `/x` another file (#1977). Each
 * segment is URL-encoded with the slashes kept, so a loader that resolves sibling files relative to
 * `metadata.json` (Potree) stays in the same folder.
 *
 * @param apiBase    the API base URL from the app config (`config.api`, ending in `/`)
 * @param databaseId the file's own database
 * @param assetId    the file's own asset
 * @param fileKey    the host's key for the file
 * @param dataPath   the path inside the file's viewer data, for example `POTREE_VIEWER_METADATA`
 */
export function auxiliaryPreviewStreamUrl(
    apiBase: string,
    databaseId: string,
    assetId: string,
    fileKey: string,
    dataPath: string
): string {
    const path = `${fileKey || ""}/preview/${dataPath.replace(/^\/+/, "")}`;
    const encodedPath = path.split("/").map(encodeURIComponent).join("/");
    return `${apiBase}database/${encodeURIComponent(databaseId)}/assets/${encodeURIComponent(
        assetId
    )}/auxiliaryPreviewAssets/stream/${encodedPath}`;
}
