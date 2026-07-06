/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "./apiClient";

export interface MoveFileRequest {
    sourcePath: string;
    destinationPath: string;
}

export interface CopyFileRequest {
    sourcePath: string;
    destinationPath: string;
    destinationAssetId?: string;
    destinationDatabaseId?: string;
}

export interface UnarchiveFileRequest {
    filePath: string;
}

export interface ArchiveFileRequest {
    filePath: string;
    isPrefix?: boolean;
}

export interface DeleteAssetPreviewResponse {
    success: boolean;
    message: string;
    assetId: string;
}

export interface FileOperationResponse {
    success: boolean;
    message: string;
    affectedFiles: string[];
}

export interface FileOperationResult {
    filePath: string;
    success: boolean;
    error?: string;
}

export interface GeneratePresignedUrlsRequest {
    databaseId: string;
    assetId: string;
    assetVersionId?: string;
    files: {
        key: string;
        name: string;
        versionId?: string;
    }[];
}

export interface PresignedUrlResult {
    fileName: string;
    url: string;
    error?: string;
}

/**
 * Move a file within an asset
 */
export const moveFile = async (
    databaseId: string,
    assetId: string,
    request: MoveFileRequest
): Promise<FileOperationResponse> => {
    try {
        const response = await apiClient.post(`database/${databaseId}/assets/${assetId}/moveFile`, {
            body: request,
        });

        // The API returns the entire response object with success, message, and affectedFiles
        if (response) {
            return response;
        } else {
            throw new Error("Invalid response format");
        }
    } catch (error: any) {
        console.error("Error moving file:", error);
        throw new Error(error?.message || "Failed to move file");
    }
};

/**
 * Copy a file within an asset or between assets
 */
export const copyFile = async (
    databaseId: string,
    assetId: string,
    request: CopyFileRequest
): Promise<FileOperationResponse> => {
    try {
        const response = await apiClient.post(`database/${databaseId}/assets/${assetId}/copyFile`, {
            body: request,
        });

        // The API returns the entire response object with success, message, and affectedFiles
        if (response) {
            return response;
        } else {
            throw new Error("Invalid response format");
        }
    } catch (error: any) {
        console.error("Error copying file:", error);
        throw new Error(error?.message || "Failed to copy file");
    }
};

/**
 * Unarchive a file that was previously archived
 */
export const unarchiveFile = async (
    databaseId: string,
    assetId: string,
    request: UnarchiveFileRequest
): Promise<FileOperationResponse> => {
    try {
        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/unarchiveFile`,
            {
                body: request,
            }
        );

        // The API returns the entire response object with success, message, and affectedFiles
        if (response) {
            return response;
        } else {
            throw new Error("Invalid response format");
        }
    } catch (error: any) {
        console.error("Error unarchiving file:", error);
        throw new Error(error?.message || "Failed to unarchive file");
    }
};

/**
 * Archive a file (soft delete)
 */
export const archiveFile = async (
    databaseId: string,
    assetId: string,
    request: ArchiveFileRequest
): Promise<FileOperationResponse> => {
    try {
        const response = await apiClient.del(
            `database/${databaseId}/assets/${assetId}/archiveFile`,
            {
                body: request,
            }
        );

        // The API returns the entire response object with success, message, and affectedFiles
        if (response) {
            return response;
        } else {
            throw new Error("Invalid response format");
        }
    } catch (error: any) {
        console.error("Error archiving file:", error);
        throw new Error(error?.message || "Failed to archive file");
    }
};

/**
 * Delete an asset preview file
 */
export const deleteAssetPreview = async (
    databaseId: string,
    assetId: string
): Promise<DeleteAssetPreviewResponse> => {
    try {
        const response = await apiClient.del(
            `database/${databaseId}/assets/${assetId}/deleteAssetPreview`,
            {}
        );

        // The API returns the entire response object with success, message, and assetId
        if (response) {
            return response;
        } else {
            throw new Error("Invalid response format");
        }
    } catch (error: any) {
        console.error("Error deleting asset preview:", error);
        throw new Error(error?.message || "Failed to delete asset preview");
    }
};

// Backend cap on file keys per bulk download request; larger sets page locally
export const MAX_DOWNLOAD_KEYS_PER_REQUEST = 1500;

/**
 * Generate presigned download URLs for a set of file keys, returning a map
 * keyed by file key.
 *
 * Uses the bulk download API, paging locally in chunks of the backend's
 * per-request key limit. Keys that cannot be signed are omitted from the map.
 */
export const generateBulkDownloadUrlMap = async (
    databaseId: string,
    assetId: string,
    keys: string[],
    assetVersionId?: string
): Promise<Map<string, string>> => {
    const urlByKey = new Map<string, string>();

    for (let start = 0; start < keys.length; start += MAX_DOWNLOAD_KEYS_PER_REQUEST) {
        const chunk = keys.slice(start, start + MAX_DOWNLOAD_KEYS_PER_REQUEST);
        try {
            const downloadBody: any = {
                downloadType: "assetFile",
                keys: chunk,
            };
            if (assetVersionId) {
                downloadBody.assetVersionId = assetVersionId;
            }

            const response = await apiClient.post(
                `database/${databaseId}/assets/${assetId}/download`,
                {
                    body: downloadBody,
                }
            );

            for (const entry of response?.files || []) {
                if (entry.success && entry.downloadUrl) {
                    urlByKey.set(entry.key, entry.downloadUrl);
                }
            }
        } catch (error) {
            console.log("Bulk URL generation failed for chunk:", error);
        }
    }

    return urlByKey;
};

/**
 * Generate presigned URLs for sharing files
 *
 * Uses the bulk download API (keys array, paged at the backend's per-request
 * limit). When assetVersionId is set, all files resolve to that asset version
 * snapshot. Otherwise each file resolves to its own S3 versionId when one is
 * provided (e.g. sharing a specific file version), or to the latest version.
 */
export const generatePresignedUrls = async (
    request: GeneratePresignedUrlsRequest
): Promise<[boolean, PresignedUrlResult[] | string]> => {
    try {
        const { databaseId, assetId, assetVersionId, files } = request;

        if (!files || files.length === 0) {
            return [false, "No files specified"];
        }

        const results: PresignedUrlResult[] = [];
        const nameByKey = new Map(files.map((f) => [f.key, f.name]));

        for (let start = 0; start < files.length; start += MAX_DOWNLOAD_KEYS_PER_REQUEST) {
            const chunk = files.slice(start, start + MAX_DOWNLOAD_KEYS_PER_REQUEST);
            try {
                const downloadBody: any = {
                    downloadType: "assetFile",
                    // An asset-version pin covers all files; otherwise send each
                    // file's own versionId when present (bulk accepts {key, versionId})
                    keys: chunk.map((f) =>
                        !assetVersionId && f.versionId
                            ? { key: f.key, versionId: f.versionId }
                            : f.key
                    ),
                };
                if (assetVersionId) {
                    downloadBody.assetVersionId = assetVersionId;
                }

                const response = await apiClient.post(
                    `database/${databaseId}/assets/${assetId}/download`,
                    {
                        body: downloadBody,
                    }
                );

                for (const entry of response?.files || []) {
                    const fileName = nameByKey.get(entry.key) || entry.key;
                    if (entry.success && entry.downloadUrl) {
                        results.push({ fileName, url: entry.downloadUrl });
                    } else {
                        results.push({
                            fileName,
                            url: "",
                            error: entry.error || "Failed to generate URL",
                        });
                    }
                }
            } catch (bulkError: any) {
                // Request-level failure marks every file in the chunk failed
                for (const file of chunk) {
                    results.push({
                        fileName: file.name,
                        url: "",
                        error: bulkError?.message || "Failed to generate URL",
                    });
                }
            }
        }

        return [true, results];
    } catch (error: any) {
        console.error("Error generating presigned URLs:", error);
        return [false, error?.message || "Failed to generate presigned URLs"];
    }
};

/**
 * Process multiple file operations (move or copy)
 */
export const processMultipleFileOperations = async (
    databaseId: string,
    assetId: string,
    files: string[],
    destinationFolder: string,
    operation: "move" | "copy",
    destinationAssetId?: string,
    destFileNames?: Record<string, string>,
    destinationDatabaseId?: string
): Promise<FileOperationResult[]> => {
    const results: FileOperationResult[] = [];

    for (const filePath of files) {
        try {
            // Use custom filename if provided, otherwise extract from source path
            const fileName = destFileNames?.[filePath] || filePath.split("/").pop() || filePath;
            const destinationPath = destinationFolder.endsWith("/")
                ? `${destinationFolder}${fileName}`
                : `${destinationFolder}/${fileName}`;

            if (operation === "move") {
                const response = await moveFile(databaseId, assetId, {
                    sourcePath: filePath,
                    destinationPath: destinationPath,
                });

                results.push({
                    filePath,
                    success: response.success,
                    error: response.success ? undefined : response.message,
                });
            } else {
                const response = await copyFile(databaseId, assetId, {
                    sourcePath: filePath,
                    destinationPath: destinationPath,
                    destinationAssetId: destinationAssetId,
                    ...(destinationDatabaseId && { destinationDatabaseId }),
                });

                results.push({
                    filePath,
                    success: response.success,
                    error: response.success ? undefined : response.message,
                });
            }
        } catch (error: any) {
            results.push({
                filePath,
                success: false,
                error: error.message || `Failed to ${operation} file`,
            });
        }
    }

    return results;
};
