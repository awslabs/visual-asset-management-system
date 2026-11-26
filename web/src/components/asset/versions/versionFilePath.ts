/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The path the API is sent for a file of an asset version snapshot.
 *
 * A snapshot's `relativeKey` is the file's path in the asset, usually recorded without its leading "/".
 * The API reads a value with a leading "/" as a path in the asset, and a value without one as the file's
 * full S3 key when the asset holds such a key. So a snapshot path goes out with the slash: in an asset
 * stored at "A1/", the snapshot path "A1/cloud.las" names the file in the subfolder "A1/" (key
 * "A1/A1/cloud.las"), not the root file "A1/cloud.las" (#1943). The API decides what the path names.
 */
export const versionFilePath = (relativeKey: unknown): string => {
    const key = String(relativeKey ?? "");
    // Exactly one leading "/" is dropped first, as the API reads a path: "x" and a legacy "/x" are "/x", and
    // "//x" (the key with an empty segment after the location, "A1//x") stays "//x" (#1977)
    return "/" + (key.startsWith("/") ? key.slice(1) : key);
};

/**
 * The form a snapshot stores a file's key in, and the form a file of the asset is matched against a
 * snapshot in: exactly one leading "/" dropped, then "/" put back when the path still starts with one. So
 * "x", "/x" (a legacy entry, or a file the file list shows at "/x") are "x", and "//x" is "//x", another
 * file (#1977). Mirrors the backend's assetVersions.stored_version_entry_key.
 */
export const storedVersionEntryKey = (path: unknown): string => {
    const value = String(path ?? "");
    const key = value.startsWith("/") ? value.slice(1) : value;
    return key.startsWith("/") ? "/" + key : key;
};
