/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";

export interface MoveFileRequest {
    sourcePath: string;
    destinationPath: string;
}

export interface CopyFileRequest {
    sourcePath: string;
    destinationPath: string;
    destinationAssetId?: string;
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

/**
 * Move a file within an asset
 */
export const moveFile = async (
    databaseId: string,
    assetId: string,
    request: MoveFileRequest,
    api = API
): Promise<FileOperationResponse> => {
    try {
        const response = await api.post(
            "api",
            `/database/${databaseId}/assets/${assetId}/moveFile`,
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
    request: CopyFileRequest,
    api = API
): Promise<FileOperationResponse> => {
    try {
        const response = await api.post(
            "api",
            `/database/${databaseId}/assets/${assetId}/copyFile`,
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
    request: UnarchiveFileRequest,
    api = API
): Promise<FileOperationResponse> => {
    try {
        const response = await api.post(
            "api",
            `/database/${databaseId}/assets/${assetId}/unarchiveFile`,
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
    request: ArchiveFileRequest,
    api = API
): Promise<FileOperationResponse> => {
    try {
        const response = await api.del(
            "api",
            `/database/${databaseId}/assets/${assetId}/archiveFile`,
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
    assetId: string,
    api = API
): Promise<DeleteAssetPreviewResponse> => {
    try {
        const response = await api.del(
            "api",
            `/database/${databaseId}/assets/${assetId}/deleteAssetPreview`,
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

/**
 * Process multiple file operations (move or copy)
 */
export const processMultipleFileOperations = async (
    databaseId: string,
    assetId: string,
    files: string[],
    destinationFolder: string,
    operation: "move" | "copy",
    destinationAssetId?: string
): Promise<FileOperationResult[]> => {
    const results: FileOperationResult[] = [];

    for (const filePath of files) {
        try {
            // Construct destination path
            const fileName = filePath.split("/").pop() || filePath;
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
