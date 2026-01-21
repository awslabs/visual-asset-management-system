/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";
import {
    EntityType,
    FileMetadataType,
    MetadataRecord,
    MetadataAPIResponse,
    BulkOperationResponse,
    UpdateType,
} from "../types/metadata.types";

/**
 * Fetch metadata for any entity type
 */
export const fetchMetadata = async (
    entityType: EntityType,
    entityId: string,
    databaseId?: string,
    filePath?: string,
    fileType?: FileMetadataType,
    api = API
): Promise<MetadataAPIResponse> => {
    try {
        let endpoint = "";

        switch (entityType) {
            case "assetLink":
                endpoint = `asset-links/${entityId}/metadata`;
                break;
            case "asset":
                if (!databaseId) throw new Error("databaseId required for asset metadata");
                endpoint = `database/${databaseId}/assets/${entityId}/metadata`;
                break;
            case "file":
                if (!databaseId || !filePath || !fileType) {
                    throw new Error(
                        "databaseId, filePath, and fileType required for file metadata"
                    );
                }
                endpoint = `database/${databaseId}/assets/${entityId}/metadata/file`;
                break;
            case "database":
                endpoint = `database/${entityId}/metadata`;
                break;
            default:
                throw new Error(`Unknown entity type: ${entityType}`);
        }

        const queryParams: any = {};
        if (entityType === "file" && filePath && fileType) {
            queryParams.filePath = filePath;
            queryParams.type = fileType;
        }

        const response = await api.get("api", endpoint, {
            queryStringParameters: Object.keys(queryParams).length > 0 ? queryParams : undefined,
        });

        console.log(`[apiHelpers] fetchMetadata response for ${entityType}:`, response);

        // Handle different response formats
        if (response && typeof response === "object") {
            if (Array.isArray(response.metadata)) {
                return response;
            } else if (
                response.message &&
                typeof response.message === "object" &&
                Array.isArray(response.message.metadata)
            ) {
                return response.message;
            } else if (response.message && typeof response.message === "string") {
                return { metadata: [], message: response.message };
            }
        } else if (typeof response === "string") {
            return { metadata: [], message: response };
        }

        return { metadata: [], message: "Unknown response format" };
    } catch (error: any) {
        console.error("[apiHelpers] Error fetching metadata:", error);
        // Extract error message from API response with status code
        if (error.response) {
            const statusCode = error.response.status || "Unknown";
            const message = error.response.data?.message || error.message || "Unknown error";
            throw new Error(`[${statusCode}] ${message}`);
        }
        throw error;
    }
};

/**
 * Create metadata (bulk operation)
 */
export const createMetadata = async (
    entityType: EntityType,
    entityId: string,
    metadata: MetadataRecord[],
    databaseId?: string,
    filePath?: string,
    fileType?: FileMetadataType,
    api = API
): Promise<BulkOperationResponse> => {
    try {
        let endpoint = "";
        const body: any = { metadata };

        switch (entityType) {
            case "assetLink":
                endpoint = `asset-links/${entityId}/metadata`;
                break;
            case "asset":
                if (!databaseId) throw new Error("databaseId required for asset metadata");
                endpoint = `database/${databaseId}/assets/${entityId}/metadata`;
                break;
            case "file":
                if (!databaseId || !filePath || !fileType) {
                    throw new Error(
                        "databaseId, filePath, and fileType required for file metadata"
                    );
                }
                endpoint = `database/${databaseId}/assets/${entityId}/metadata/file`;
                body.filePath = filePath;
                body.type = fileType;
                break;
            case "database":
                endpoint = `database/${entityId}/metadata`;
                break;
            default:
                throw new Error(`Unknown entity type: ${entityType}`);
        }

        const response = await api.post("api", endpoint, { body });

        console.log(`[apiHelpers] createMetadata response for ${entityType}:`, response);

        return response;
    } catch (error: any) {
        console.error("[apiHelpers] Error creating metadata:", error);
        // Extract error message from API response with status code
        if (error.response) {
            const statusCode = error.response.status || "Unknown";
            const message = error.response.data?.message || error.message || "Unknown error";
            throw new Error(`[${statusCode}] ${message}`);
        }
        throw error;
    }
};

/**
 * Update metadata (bulk operation)
 */
export const updateMetadata = async (
    entityType: EntityType,
    entityId: string,
    metadata: MetadataRecord[],
    updateType: UpdateType = "update",
    databaseId?: string,
    filePath?: string,
    fileType?: FileMetadataType,
    api = API
): Promise<BulkOperationResponse> => {
    try {
        let endpoint = "";
        const body: any = { metadata, updateType };

        switch (entityType) {
            case "assetLink":
                endpoint = `asset-links/${entityId}/metadata`;
                break;
            case "asset":
                if (!databaseId) throw new Error("databaseId required for asset metadata");
                endpoint = `database/${databaseId}/assets/${entityId}/metadata`;
                break;
            case "file":
                if (!databaseId || !filePath || !fileType) {
                    throw new Error(
                        "databaseId, filePath, and fileType required for file metadata"
                    );
                }
                endpoint = `database/${databaseId}/assets/${entityId}/metadata/file`;
                body.filePath = filePath;
                body.type = fileType;
                break;
            case "database":
                endpoint = `database/${entityId}/metadata`;
                break;
            default:
                throw new Error(`Unknown entity type: ${entityType}`);
        }

        const response = await api.put("api", endpoint, { body });

        console.log(`[apiHelpers] updateMetadata response for ${entityType}:`, response);

        return response;
    } catch (error: any) {
        console.error("[apiHelpers] Error updating metadata:", error);
        // Extract error message from API response with status code
        if (error.response) {
            const statusCode = error.response.status || "Unknown";
            const message = error.response.data?.message || error.message || "Unknown error";
            throw new Error(`[${statusCode}] ${message}`);
        }
        throw error;
    }
};

/**
 * Delete metadata (bulk operation)
 */
export const deleteMetadata = async (
    entityType: EntityType,
    entityId: string,
    metadataKeys: string[],
    databaseId?: string,
    filePath?: string,
    fileType?: FileMetadataType,
    api = API
): Promise<BulkOperationResponse> => {
    try {
        let endpoint = "";
        const body: any = { metadataKeys };

        switch (entityType) {
            case "assetLink":
                endpoint = `asset-links/${entityId}/metadata`;
                break;
            case "asset":
                if (!databaseId) throw new Error("databaseId required for asset metadata");
                endpoint = `database/${databaseId}/assets/${entityId}/metadata`;
                break;
            case "file":
                if (!databaseId || !filePath || !fileType) {
                    throw new Error(
                        "databaseId, filePath, and fileType required for file metadata"
                    );
                }
                endpoint = `database/${databaseId}/assets/${entityId}/metadata/file`;
                body.filePath = filePath;
                body.type = fileType;
                break;
            case "database":
                endpoint = `database/${entityId}/metadata`;
                break;
            default:
                throw new Error(`Unknown entity type: ${entityType}`);
        }

        const response = await api.del("api", endpoint, { body });

        console.log(`[apiHelpers] deleteMetadata response for ${entityType}:`, response);

        return response;
    } catch (error: any) {
        console.error("[apiHelpers] Error deleting metadata:", error);
        // Extract error message from API response with status code
        if (error.response) {
            const statusCode = error.response.status || "Unknown";
            const message = error.response.data?.message || error.message || "Unknown error";
            throw new Error(`[${statusCode}] ${message}`);
        }
        throw error;
    }
};

/**
 * Fetch metadata schema
 */
export const fetchMetadataSchema = async (
    databaseIds: string[],
    entityType: string,
    filePath?: string,
    api = API
): Promise<any> => {
    try {
        // For now, we'll use the existing fetchAllMetadataSchema or fetchDatabaseMetadataSchema
        // The backend enriches metadata with schema info, so we may not need a separate call
        // This is a placeholder for future schema-specific fetching if needed

        console.log("[apiHelpers] fetchMetadataSchema called with:", {
            databaseIds,
            entityType,
            filePath,
        });

        // TODO: Implement schema fetching if needed
        // For now, schema info comes enriched with metadata from the backend
        return { schemas: [] };
    } catch (error) {
        console.error("[apiHelpers] Error fetching metadata schema:", error);
        throw error;
    }
};
