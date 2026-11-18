/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Name of the record-type discriminator carried by every indexed OpenSearch document.
 * The indexers stamp it as "asset" or "file"; it mirrors the backend document models.
 */
export const RECORD_TYPE_FIELD = "str_rectype";

/**
 * Classify a search hit source as a file record.
 *
 * The "Files" / "Assets" toggle on the search page is authoritative while it is set, because the
 * visible column set is chosen from the same value. The per-document discriminator is the fallback
 * for a mixed result set, where the toggle cannot describe every hit.
 */
export function isFileHitSource(
    source: Record<string, any> | null | undefined,
    isFileSearchMode: boolean
): boolean {
    if (isFileSearchMode) return true;
    return source?.[RECORD_TYPE_FIELD] === "file";
}

/**
 * Whether a hit's `MD_` holds only its asset's location rather than the asset's metadata (D83). A
 * natural-language asset hit (one with `_vector`) carries `geo_MD_location` and the location keys of
 * the asset's `MD_` (location, latitude, lat, longitude, lon, long) so the Map view can plot it, and
 * nothing else of that metadata; listing those keys as the asset's metadata fields would misstate
 * it. A natural-language file hit carries its file's whole `MD_` from the file index, and a keyword
 * hit its record's, so their metadata is listed as it is.
 */
export function hasLocationOnlyMetadata(
    source: Record<string, any> | null | undefined,
    vector: unknown,
    isFileSearchMode: boolean
): boolean {
    return !!vector && !isFileHitSource(source, isFileSearchMode);
}
