/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "./apiClient";
import { fetchAssetS3Files } from "./APIService";

/**
 * Fetches all asset versions for a given asset (fetches all pages)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {number} [params.pageSize=100] - Page size for fetching (optional, default 100)
 * @param {boolean} [params.showArchived=false] - Whether to include archived versions
 * @returns {Promise<[boolean, any]>}
 */
export const fetchAllAssetVersions = async ({
    databaseId,
    assetId,
    pageSize = 100,
    showArchived = false,
}) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        let allVersions = [];
        let nextToken = null;

        do {
            const [success, response] = await fetchAssetVersions({
                databaseId,
                assetId,
                pageSize,
                startingToken: nextToken,
                showArchived,
            });

            if (!success || !response) {
                console.log("Failed to fetch page of versions");
                break;
            }

            const versions = response.versions || [];
            allVersions = [...allVersions, ...versions];
            nextToken = response.NextToken || null;

            console.log(
                `Fetched ${versions.length} versions, total so far: ${
                    allVersions.length
                }, nextToken: ${nextToken ? "exists" : "null"}`
            );
        } while (nextToken);

        console.log(`Finished fetching all versions, total: ${allVersions.length}`);
        return [true, { versions: allVersions, totalCount: allVersions.length }];
    } catch (error) {
        console.log("Error fetching all asset versions:", error);
        return [false, error?.message || "Failed to fetch all asset versions"];
    }
};

/**
 * Fetches a single page of asset versions for a given asset
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {number} params.pageSize - Page size (optional, default 100)
 * @param {string|null} params.startingToken - Pagination token (optional)
 * @param {boolean} [params.showArchived=false] - Whether to include archived versions
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAssetVersions = async ({
    databaseId,
    assetId,
    pageSize = 100,
    startingToken = null,
    showArchived = false,
}) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        const queryParams = {
            pageSize: pageSize.toString(),
        };

        if (showArchived) {
            queryParams.showArchived = "true";
        }

        if (startingToken && startingToken != "") {
            queryParams.startingToken = startingToken;
        }

        const response = await apiClient.get(
            `database/${databaseId}/assets/${assetId}/getVersions`,
            {
                queryStringParameters: queryParams,
            }
        );

        //console.log("Raw API response:", JSON.stringify(response, null, 2));

        // Direct response format (no message wrapper)
        if (response && response.versions !== undefined) {
            console.log("Direct response with versions property detected");
            return [true, response];
        }

        // Response with message wrapper
        if (response && response.message) {
            console.log("Response with message wrapper detected");

            // Handle different response formats consistently
            if (Array.isArray(response.message)) {
                // If the response is an array of versions
                console.log("Response.message is an array");
                return [
                    true,
                    {
                        versions: response.message,
                        nextToken: response.NextToken || null,
                    },
                ];
            } else if (typeof response.message === "object" && response.message.versions) {
                // If the response is an object with versions property
                console.log("Response.message is an object with versions property");
                return [true, response.message];
            } else if (typeof response.message === "object") {
                // If the response is an object but doesn't have versions property
                // Convert it to the expected format
                console.log("Response.message is an object without versions property");
                return [
                    true,
                    {
                        versions: [response.message],
                        nextToken: response.NextToken || null,
                    },
                ];
            } else {
                // For any other format, return as is
                console.log("Response.message is in an unknown format:", typeof response.message);
                return [true, response.message];
            }
        } else {
            console.log("No valid response received:", response);
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error fetching asset versions:", error);
        return [false, error?.message || "Failed to fetch asset versions"];
    }
};

/**
 * Fetches details for a specific asset version
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.assetVersionId - Asset version ID
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAssetVersion = async ({ databaseId, assetId, assetVersionId }) => {
    try {
        if (!databaseId || !assetId || !assetVersionId) {
            return [false, "Database ID, Asset ID, and Asset Version ID are required"];
        }

        const response = await apiClient.get(
            `database/${databaseId}/assets/${assetId}/getVersion/${assetVersionId}`,
            {}
        );

        console.log("fetchAssetVersion raw response:", JSON.stringify(response, null, 2));

        // Handle direct response format (no message wrapper)
        if (response && response.files !== undefined) {
            console.log("Direct response with files property detected");
            return [true, response];
        }
        // Handle response with message wrapper
        else if (response && response.message) {
            console.log("Response with message wrapper detected");
            return [true, response.message];
        }
        // Handle empty response
        else if (response && Object.keys(response).length > 0) {
            console.log("Response exists but doesn't match expected format, returning as-is");
            return [true, response];
        } else {
            console.log("No valid response received:", response);
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error fetching asset version:", error);
        return [false, error?.message || "Failed to fetch asset version"];
    }
};

/**
 * Creates a new asset version
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.useLatestFiles - Whether to use latest files
 * @param {Array} params.files - Array of file objects (optional)
 * @param {string} params.comment - Version comment (optional)
 * @returns {Promise<boolean|{message}|any>}
 */
/**
 * @param {{ databaseId: string, assetId: string, useLatestFiles?: boolean, files?: any[], comment: string, versionAlias?: string }} params
 * @param {*} api
 */
export const createAssetVersion = async ({
    databaseId,
    assetId,
    useLatestFiles = true,
    files = [],
    comment,
    versionAlias,
}) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        if (!comment || !comment.trim()) {
            return [false, "Comment is required"];
        }

        const body = {
            useLatestFiles,
            comment: comment.trim(),
        };

        if (versionAlias && versionAlias.trim()) {
            body.versionAlias = versionAlias.trim();
        }

        if (!useLatestFiles && files.length > 0) {
            // Format files according to the new model structure
            body.files = files.map((file) => ({
                relativeKey: file.relativeKey || file.key,
                versionId: file.versionId,
            }));
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/createVersion`,
            {
                body: body,
            }
        );

        if (response.message) {
            // Check if response is an object with success property (new format)
            if (response.message.success !== undefined) {
                if (response.message.success) {
                    return [true, response.message];
                } else {
                    console.log("Create version error:", response.message.message);
                    return [false, response.message.message || "Version creation failed"];
                }
            } else if (
                typeof response.message === "string" &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Create version error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error creating asset version:", error);
        return [false, error?.message || "Failed to create asset version"];
    }
};

/**
 * Reverts an asset to a specific version
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.assetVersionId - Asset version ID to revert to
 * @param {string} params.comment - Revert comment (optional)
 * @param {boolean} params.revertMetadata - Whether to revert metadata/attributes (optional, default: false)
 * @returns {Promise<boolean|{message}|any>}
 */
export const revertAssetVersion = async ({
    databaseId,
    assetId,
    assetVersionId,
    comment = "",
    revertMetadata = false,
}) => {
    try {
        if (!databaseId || !assetId || !assetVersionId) {
            return [false, "Database ID, Asset ID, and Asset Version ID are required"];
        }

        const body = {
            revertMetadata: revertMetadata,
        };

        if (comment) {
            body.comment = comment;
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/revertAssetVersion/${assetVersionId}`,
            {
                body: body,
            }
        );

        if (response.message) {
            // Check if response is an object with success property (new format)
            if (response.message.success !== undefined) {
                if (response.message.success) {
                    return [true, response.message];
                } else {
                    console.log("Revert version error:", response.message.message);
                    return [false, response.message.message || "Version revert failed"];
                }
            } else if (
                typeof response.message === "string" &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Revert version error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error reverting asset version:", error);
        return [false, error?.message || "Failed to revert asset version"];
    }
};

/**
 * Compares two asset versions or an asset version with current files
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.version1Id - First version ID
 * @param {string} [params.version2Id] - Second version ID (optional)
 * @param {boolean} [params.compareWithCurrent=false] - Whether to compare with current files (optional)
 * @returns {Promise<boolean|{message}|any>}
 */
export const compareAssetVersions = async ({
    databaseId,
    assetId,
    version1Id,
    version2Id,
    compareWithCurrent = false,
}) => {
    try {
        if (!databaseId || !assetId || !version1Id) {
            return [false, "Required parameters missing"];
        }

        // Fetch first version
        const [success1, version1] = await fetchAssetVersion({
            databaseId,
            assetId,
            assetVersionId: version1Id,
        });

        if (!success1 || !version1) {
            return [false, "Failed to fetch first version"];
        }

        let version2 = null;

        if (compareWithCurrent) {
            // Fetch current files
            const [successCurrent, currentFiles] = await fetchAssetS3Files({
                databaseId,
                assetId,
                includeArchived: false,
                basic: false,
            });

            if (!successCurrent) {
                return [false, "Failed to fetch current files"];
            }

            // Convert S3Files to FileVersion format for comparison
            version2 = {
                files: currentFiles.map((file) => ({
                    relativeKey: file.relativePath || file.key,
                    versionId: file.versionId,
                    size: file.size,
                    lastModified: file.dateCreatedCurrentVersion,
                    etag: file.etag,
                    isArchived: file.isArchived,
                    isPermanentlyDeleted: false,
                })),
            };
        } else if (version2Id) {
            // Fetch second version
            const [success2, v2] = await fetchAssetVersion({
                databaseId,
                assetId,
                assetVersionId: version2Id,
            });

            if (!success2 || !v2) {
                return [false, "Failed to fetch second version"];
            }

            version2 = v2;
        } else {
            return [false, "Either version2Id or compareWithCurrent must be provided"];
        }

        // Generate comparison
        const comparison = generateComparison(version1, version2);
        return [true, comparison];
    } catch (error) {
        console.log("Error comparing versions:", error);
        return [false, error?.message || "Failed to compare versions"];
    }
};

/**
 * Helper function to generate comparison between two versions
 * @param {Object} version1 - First version
 * @param {Object} version2 - Second version
 * @returns {Object} Comparison result
 */
function generateComparison(version1, version2) {
    // Map files by relative key for easier comparison
    const files1Map = new Map(version1.files.map((f) => [f.relativeKey, f]));
    const files2Map = new Map(version2.files.map((f) => [f.relativeKey, f]));

    // Get all unique keys
    const allKeys = new Set([...files1Map.keys(), ...files2Map.keys()]);

    // Generate comparison for each file
    const fileComparisons = [];
    for (const key of allKeys) {
        const file1 = files1Map.get(key);
        const file2 = files2Map.get(key);

        let status;
        if (!file1 && file2) {
            status = "added";
        } else if (file1 && !file2) {
            status = "removed";
        } else if (file1 && file2) {
            if (
                file1.versionId !== file2.versionId ||
                file1.size !== file2.size ||
                file1.etag !== file2.etag
            ) {
                status = "modified";
            } else {
                status = "unchanged";
            }
        }

        fileComparisons.push({
            relativeKey: key,
            status,
            version1File: file1,
            version2File: file2,
        });
    }

    // Sort by status and name
    const statusOrder = { added: 1, removed: 2, modified: 3, unchanged: 4 };
    fileComparisons.sort((a, b) => {
        const statusDiff = statusOrder[a.status] - statusOrder[b.status];
        if (statusDiff !== 0) return statusDiff;
        return a.relativeKey.localeCompare(b.relativeKey);
    });

    // Generate summary
    const summary = {
        added: fileComparisons.filter((f) => f.status === "added").length,
        removed: fileComparisons.filter((f) => f.status === "removed").length,
        modified: fileComparisons.filter((f) => f.status === "modified").length,
        unchanged: fileComparisons.filter((f) => f.status === "unchanged").length,
        total: fileComparisons.length,
    };

    return {
        version1: version1,
        version2: version2,
        fileComparisons,
        summary,
    };
}

/**
 * Fetches file versions for a specific file
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.filePath - File path
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchFileVersions = async ({ databaseId, assetId, filePath }) => {
    try {
        if (!databaseId || !assetId || !filePath) {
            return [false, "Database ID, Asset ID, and File Path are required"];
        }

        const response = await apiClient.get(`database/${databaseId}/assets/${assetId}/fileInfo`, {
            queryStringParameters: {
                filePath: filePath,
                includeVersions: "true",
            },
        });

        if (response && response.versions) {
            return [true, response];
        } else if (response && response.message) {
            // Handle message wrapper
            if (response.message.versions) {
                return [true, response.message];
            } else {
                return [false, "No versions found in response"];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error fetching file versions:", error);
        return [false, error?.message || "Failed to fetch file versions"];
    }
};

/**
 * Updates an asset version's comment and/or alias
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.assetVersionId - Asset version ID
 * @param {Object} params.body - Update body with optional comment and versionAlias
 * @returns {Promise<[boolean, any]>}
 */
export const updateAssetVersion = async ({ databaseId, assetId, assetVersionId, body }) => {
    try {
        if (!databaseId || !assetId || !assetVersionId) {
            return [false, "Database ID, Asset ID, and Asset Version ID are required"];
        }

        if (
            !body ||
            (!body.comment && body.comment !== "" && !body.versionAlias && body.versionAlias !== "")
        ) {
            return [false, "At least one of comment or versionAlias is required"];
        }

        const response = await apiClient.put(
            `database/${databaseId}/assets/${assetId}/assetversions/${assetVersionId}`,
            {
                body: body,
            }
        );

        if (response.message) {
            if (response.message.success !== undefined) {
                if (response.message.success) {
                    return [true, response.message];
                } else {
                    console.log("Update version error:", response.message.message);
                    return [false, response.message.message || "Version update failed"];
                }
            } else if (
                typeof response.message === "string" &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Update version error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else if (response && Object.keys(response).length > 0) {
            return [true, response];
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error updating asset version:", error);
        return [false, error?.message || "Failed to update asset version"];
    }
};

/**
 * Archives an asset version
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.assetVersionId - Asset version ID
 * @returns {Promise<[boolean, any]>}
 */
export const archiveAssetVersion = async ({ databaseId, assetId, assetVersionId }) => {
    try {
        if (!databaseId || !assetId || !assetVersionId) {
            return [false, "Database ID, Asset ID, and Asset Version ID are required"];
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/assetversions/${assetVersionId}/archive`,
            {
                body: {},
            }
        );

        if (response.message) {
            if (response.message.success !== undefined) {
                if (response.message.success) {
                    return [true, response.message];
                } else {
                    console.log("Archive version error:", response.message.message);
                    return [false, response.message.message || "Version archive failed"];
                }
            } else if (
                typeof response.message === "string" &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Archive version error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else if (response && Object.keys(response).length > 0) {
            return [true, response];
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error archiving asset version:", error);
        return [false, error?.message || "Failed to archive asset version"];
    }
};

/**
 * Unarchives an asset version
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.assetVersionId - Asset version ID
 * @returns {Promise<[boolean, any]>}
 */
export const unarchiveAssetVersion = async ({ databaseId, assetId, assetVersionId }) => {
    try {
        if (!databaseId || !assetId || !assetVersionId) {
            return [false, "Database ID, Asset ID, and Asset Version ID are required"];
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/assetversions/${assetVersionId}/unarchive`,
            {
                body: {},
            }
        );

        if (response.message) {
            if (response.message.success !== undefined) {
                if (response.message.success) {
                    return [true, response.message];
                } else {
                    console.log("Unarchive version error:", response.message.message);
                    return [false, response.message.message || "Version unarchive failed"];
                }
            } else if (
                typeof response.message === "string" &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Unarchive version error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else if (response && Object.keys(response).length > 0) {
            return [true, response];
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.log("Error unarchiving asset version:", error);
        return [false, error?.message || "Failed to unarchive asset version"];
    }
};
