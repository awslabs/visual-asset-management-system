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
export const versionFilePath = (relativeKey: unknown): string =>
    "/" + String(relativeKey ?? "").replace(/^\/+/, "");
