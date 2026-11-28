/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A file's path in the asset in the form the API reads: with exactly one leading "/" added when it has
 * none, and nothing else changed.
 *
 * The API reads one leading "/" as the path-in-the-asset marker, so "//x" names the key with an empty
 * segment right after the asset's location ("A1//x"), the path the file list shows for it, and "/x" names
 * "A1/x" (#1977). Collapsing "//x" to "/x" would name the other file, so repeated slashes are kept as given.
 * The API decides what the path names and refuses a path that names nothing in the asset.
 */
export const assetFilePath = (path?: string | null): string => {
    const value = path ?? "";
    return value.startsWith("/") ? value : `/${value}`;
};
