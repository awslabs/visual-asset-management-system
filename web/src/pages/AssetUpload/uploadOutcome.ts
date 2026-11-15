/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { CompleteUploadResponse } from "../../services/AssetUploadService";

/** What the upload page reports when an upload finishes. */
export interface UploadOutcome extends CompleteUploadResponse {
    /** No file of the upload was stored, and at least one file failed. */
    noFilesStored?: boolean;
}

/**
 * Whether a finished upload stored none of its files.
 *
 * A file that was not cancelled was not stored when its row is Failed, or when the server refused
 * it at completion. The row reads Failed for a file that failed client-side, for one the server
 * refused (the completion handler marks it Failed with the server's reason), and for every file of
 * a completion sequence the user skipped (the skip handler marks them Failed). The refusal list is
 * also checked so a refused file counts before its row is updated.
 *
 * Cancelled files are the user's choice and are left out, matched by index because skipping a
 * sequence marks its cancelled files Failed too. An upload in which every file was cancelled, or
 * which had no files, did not fail.
 */
export function storedNoFile(
    items: { index: number; relativePath: string; status: string }[],
    cancelledFileIndices: Set<number>,
    serverRejectedKeys: Set<string>
): boolean {
    const attempted = items.filter(
        (item) => !cancelledFileIndices.has(item.index) && item.status !== "Cancelled"
    );
    return (
        attempted.length > 0 &&
        attempted.every(
            (item) => item.status === "Failed" || serverRejectedKeys.has(item.relativePath)
        )
    );
}
