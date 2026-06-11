/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Maps the backend vams-changesource values (see backend common/s3MetadataKeys.py)
// to UX-friendly display labels shown in the file manager and S3 version history.

const CHANGE_SOURCE_LABELS: Record<string, string> = {
    direct: "Direct S3",
    upload: "Upload",
    workflowExecution: "Workflow Execution",
    fileCopy: "File Copy",
    fileMove: "File Move",
    fileRename: "File Rename",
    fileArchive: "File Archive",
    fileUnarchive: "File Unarchive",
    fileRevert: "File Revert",
};

/**
 * Return a friendly display label for a change source value.
 * Falls back to the raw value for unknown/future types.
 */
export function getChangeSourceLabel(changeSource?: string | null): string {
    if (!changeSource) {
        return "";
    }
    return CHANGE_SOURCE_LABELS[changeSource] || changeSource;
}
