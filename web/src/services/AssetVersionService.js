/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";

/**
 * Fetches all asset versions for a given asset
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {number} params.maxItems - Maximum items per page (optional)
 * @param {string|null} params.startingToken - Pagination token (optional)
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAssetVersions = async (
    { databaseId, assetId, maxItems = 100, startingToken = null },
    api = API
) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        const queryParams = {
            maxItems: maxItems.toString(),
        };

        if (startingToken && startingToken != "") {
            queryParams.startingToken = startingToken;
        }

        const response = await api.get(
            "api",
            `database/${databaseId}/assets/${assetId}/getVersions`,
            {
                queryStringParameters: queryParams,
            }
        );

        console.log("Raw API response:", JSON.stringify(response, null, 2));

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
                        nextToken: response.nextToken || null,
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
                        nextToken: response.nextToken || null,
                    },
                ];
            } else {
                // For any other format, return as is
                console.log("Response.message is in an unknown format:", typeof response.message);
                return [true, response.message];
            }
        } else {
            console.error("No valid response received:", response);
            return [false, "No response received"];
        }
    } catch (error) {
        console.error("Error fetching asset versions:", error);
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
export const fetchAssetVersion = async ({ databaseId, assetId, assetVersionId }, api = API) => {
    try {
        if (!databaseId || !assetId || !assetVersionId) {
            return [false, "Database ID, Asset ID, and Asset Version ID are required"];
        }

        const response = await api.get(
            "api",
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
            console.error("No valid response received:", response);
            return [false, "No response received"];
        }
    } catch (error) {
        console.error("Error fetching asset version:", error);
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
export const createAssetVersion = async (
    { databaseId, assetId, useLatestFiles = true, files = [], comment },
    api = API
) => {
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

        if (!useLatestFiles && files.length > 0) {
            // Format files according to the new model structure
            body.files = files.map((file) => ({
                relativeKey: file.relativeKey || file.key,
                versionId: file.versionId,
            }));
        }

        const response = await api.post(
            "api",
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
                    console.error("Create version error:", response.message.message);
                    return [false, response.message.message || "Version creation failed"];
                }
            } else if (
                typeof response.message === "string" &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.error("Create version error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.error("Error creating asset version:", error);
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
 * @returns {Promise<boolean|{message}|any>}
 */
export const revertAssetVersion = async (
    { databaseId, assetId, assetVersionId, comment = "" },
    api = API
) => {
    try {
        if (!databaseId || !assetId || !assetVersionId) {
            return [false, "Database ID, Asset ID, and Asset Version ID are required"];
        }

        const body = {};

        if (comment) {
            body.comment = comment;
        }

        const response = await api.post(
            "api",
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
                    console.error("Revert version error:", response.message.message);
                    return [false, response.message.message || "Version revert failed"];
                }
            } else if (
                typeof response.message === "string" &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.error("Revert version error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
        console.error("Error reverting asset version:", error);
        return [false, error?.message || "Failed to revert asset version"];
    }
};

/**
 * Fetches all files in S3 for an asset (for version creation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.includeArchived - Whether to include archived files
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAssetS3Files = async (
    { databaseId, assetId, includeArchived = false },
    api = API
) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        const response = await api.get(
            "api",
            `database/${databaseId}/assets/${assetId}/listFiles`,
            {
                queryStringParameters: {
                    includeArchived: includeArchived.toString(),
                },
            }
        );

        console.log("fetchAssetS3Files raw response:", JSON.stringify(response, null, 2));

        // Handle direct response format (new API format)
        if (response && response.items) {
            let items = response.items;

            // Handle pagination if needed
            let nextToken = response.nextToken;
            while (nextToken) {
                const nextResponse = await api.get(
                    "api",
                    `database/${databaseId}/assets/${assetId}/listFiles`,
                    {
                        queryStringParameters: {
                            includeArchived: includeArchived.toString(),
                            startingToken: nextToken,
                        },
                    }
                );

                if (nextResponse && nextResponse.items) {
                    items = items.concat(nextResponse.items);
                    nextToken = nextResponse.nextToken;
                } else {
                    break;
                }
            }
            return [true, items];
        }
        // Handle legacy response format with message wrapper
        else if (response.message) {
            let items = [];
            if (response.message.Items) {
                items = response.message.Items;

                // Handle pagination if needed
                let nextToken = response.message.NextToken;
                while (nextToken) {
                    const nextResponse = await api.get(
                        "api",
                        `database/${databaseId}/assets/${assetId}/listFiles`,
                        {
                            queryStringParameters: {
                                includeArchived: includeArchived.toString(),
                                startingToken: nextToken,
                            },
                        }
                    );

                    if (nextResponse.message && nextResponse.message.Items) {
                        items = items.concat(nextResponse.message.Items);
                        nextToken = nextResponse.message.NextToken;
                    } else {
                        break;
                    }
                }
            }
            return [true, items];
        } else {
            console.error("Unexpected response format:", response);
            return [false, "No response received"];
        }
    } catch (error) {
        console.error("Error fetching asset S3 files:", error);
        return [false, error?.message || "Failed to fetch asset files"];
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
export const compareAssetVersions = async (
    { databaseId, assetId, version1Id, version2Id, compareWithCurrent = false },
    api = API
) => {
    try {
        if (!databaseId || !assetId || !version1Id) {
            return [false, "Required parameters missing"];
        }

        // Fetch first version
        const [success1, version1] = await fetchAssetVersion(
            {
                databaseId,
                assetId,
                assetVersionId: version1Id,
            },
            api
        );

        if (!success1 || !version1) {
            return [false, "Failed to fetch first version"];
        }

        let version2 = null;

        if (compareWithCurrent) {
            // Fetch current files
            const [successCurrent, currentFiles] = await fetchAssetS3Files(
                {
                    databaseId,
                    assetId,
                    includeArchived: false,
                },
                api
            );

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
            const [success2, v2] = await fetchAssetVersion(
                {
                    databaseId,
                    assetId,
                    assetVersionId: version2Id,
                },
                api
            );

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
        console.error("Error comparing versions:", error);
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
export const fetchFileVersions = async ({ databaseId, assetId, filePath }, api = API) => {
    try {
        if (!databaseId || !assetId || !filePath) {
            return [false, "Database ID, Asset ID, and File Path are required"];
        }

        const response = await api.get("api", `database/${databaseId}/assets/${assetId}/fileInfo`, {
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
        console.error("Error fetching file versions:", error);
        return [false, error?.message || "Failed to fetch file versions"];
    }
};
