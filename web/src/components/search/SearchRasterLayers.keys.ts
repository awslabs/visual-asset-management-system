/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { RasterFileRef } from "../../common/cogRaster/presignedUrl";

/**
 * Identifies a hit's file in the set of shown rasters. Kept apart from SearchRasterLayers so the Search
 * page can use it without loading that lazy chunk (and the COG reader behind it).
 */
export function shownRasterKey(
    file: Pick<RasterFileRef, "databaseId" | "assetId" | "key">
): string {
    return `${file.databaseId}\u0000${file.assetId}\u0000${file.key}`;
}
