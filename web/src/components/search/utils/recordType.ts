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
