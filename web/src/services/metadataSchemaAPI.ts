/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";
import {
    MetadataSchema,
    CreateMetadataSchemaRequest,
    UpdateMetadataSchemaRequest,
    DeleteMetadataSchemaRequest,
    MetadataSchemaOperationResponse,
    GetMetadataSchemasResponse,
    MetadataSchemaEntityType,
} from "../components/metadataSchema/types";

/**
 * Extract user-friendly error message from API response
 */
const extractErrorMessage = (error: any): string => {
    // Check for response data message (backend validation errors)
    if (error?.response?.data?.message) {
        return error.response.data.message;
    }

    // Check for response message
    if (error?.response?.message) {
        return error.response.message;
    }

    // Check for direct message
    if (error?.message) {
        return error.message;
    }

    // Default error message
    return "An unexpected error occurred";
};

/**
 * Normalize backend entity type to frontend format (for display)
 */
const normalizeEntityType = (backendType: string): string => {
    const mapping: Record<string, string> = {
        database: "databaseMetadata",
        asset: "assetMetadata",
        file: "fileMetadata",
        fileAttribute: "fileAttribute",
        assetLink: "assetLinkMetadata",
        // Also handle if already in correct format
        databaseMetadata: "databaseMetadata",
        assetMetadata: "assetMetadata",
        fileMetadata: "fileMetadata",
        assetLinkMetadata: "assetLinkMetadata",
    };

    return mapping[backendType] || backendType;
};

/**
 * Convert frontend entity type to backend format (for API calls)
 */
const denormalizeEntityType = (frontendType: string): string => {
    const mapping: Record<string, string> = {
        databaseMetadata: "databaseMetadata",
        assetMetadata: "assetMetadata",
        fileMetadata: "fileMetadata",
        fileAttribute: "fileAttribute",
        assetLinkMetadata: "assetLinkMetadata",
    };

    // Return as-is since backend now expects the full names
    return mapping[frontendType] || frontendType;
};

/**
 * Normalize backend field format to frontend format
 */
const normalizeFields = (fields: any): any => {
    if (!fields) return { fields: [] };

    // If already in correct format
    if (fields.fields && Array.isArray(fields.fields)) {
        return fields;
    }

    // If fields is an array, normalize each field
    if (Array.isArray(fields)) {
        return {
            fields: fields.map((field: any) => ({
                metadataFieldKeyName: field.fieldName || field.metadataFieldKeyName,
                metadataFieldValueType: (
                    field.fieldType ||
                    field.metadataFieldValueType ||
                    "string"
                ).toLowerCase(),
                required: field.required || false,
                sequence: field.sequence,
                dependsOnFieldKeyName: field.dependsOn || field.dependsOnFieldKeyName,
                controlledListKeys: field.controlledListKeys,
                defaultMetadataFieldValue: field.defaultValue || field.defaultMetadataFieldValue,
            })),
        };
    }

    return { fields: [] };
};

/**
 * Fetch all metadata schemas for a database with optional entity type filter
 */
export const fetchMetadataSchemas = async (
    databaseId: string,
    entityType?: MetadataSchemaEntityType,
    api = API
): Promise<GetMetadataSchemasResponse> => {
    try {
        const params: any = { databaseId };
        if (entityType) {
            params.metadataEntityType = entityType;
        }

        const response = await api.get("api", "metadataschema", {
            queryStringParameters: params,
        });

        console.log("[metadataSchemaAPI] fetchMetadataSchemas response:", response);

        // Handle different response formats
        let items = [];
        if (response && response.Items) {
            items = response.Items;
        } else if (response && response.message && response.message.Items) {
            items = response.message.Items;
        }

        // Normalize each schema's fields and entity type
        const normalizedItems = items.map((schema: any) => ({
            ...schema,
            metadataSchemaEntityType: normalizeEntityType(schema.metadataSchemaEntityType),
            fields: normalizeFields(schema.fields),
        }));

        return { Items: normalizedItems, message: response.message || "Success" };
    } catch (error: any) {
        console.error("[metadataSchemaAPI] Error fetching metadata schemas:", error);
        const errorMessage = extractErrorMessage(error);
        throw new Error(errorMessage);
    }
};

/**
 * Get a single metadata schema by ID
 */
export const getMetadataSchema = async (
    databaseId: string,
    metadataSchemaId: string,
    api = API
): Promise<MetadataSchema> => {
    try {
        const response = await api.get(
            "api",
            `database/${databaseId}/metadataSchema/${metadataSchemaId}`,
            {}
        );

        console.log("[metadataSchemaAPI] getMetadataSchema response:", response);

        // Handle response with message wrapper
        if (response && response.message && typeof response.message === "object") {
            return response.message;
        }

        return response;
    } catch (error: any) {
        console.error("[metadataSchemaAPI] Error getting metadata schema:", error);
        const errorMessage = extractErrorMessage(error);
        throw new Error(errorMessage);
    }
};

/**
 * Create a new metadata schema
 */
export const createMetadataSchema = async (
    schemaData: CreateMetadataSchemaRequest,
    api = API
): Promise<MetadataSchemaOperationResponse> => {
    try {
        const response = await api.post("api", "metadataschema", {
            body: schemaData,
        });

        console.log("[metadataSchemaAPI] createMetadataSchema response:", response);

        // Check for error in response message
        if (
            response.message &&
            (response.message.indexOf("error") !== -1 || response.message.indexOf("Error") !== -1)
        ) {
            throw new Error(response.message);
        }

        return response;
    } catch (error: any) {
        console.error("[metadataSchemaAPI] Error creating metadata schema:", error);
        const errorMessage = extractErrorMessage(error);
        throw new Error(errorMessage);
    }
};

/**
 * Update an existing metadata schema
 */
export const updateMetadataSchema = async (
    schemaData: UpdateMetadataSchemaRequest,
    api = API
): Promise<MetadataSchemaOperationResponse> => {
    try {
        const response = await api.put("api", "metadataschema", {
            body: schemaData,
        });

        console.log("[metadataSchemaAPI] updateMetadataSchema response:", response);

        // Check for error in response message
        if (
            response.message &&
            (response.message.indexOf("error") !== -1 || response.message.indexOf("Error") !== -1)
        ) {
            throw new Error(response.message);
        }

        return response;
    } catch (error: any) {
        console.error("[metadataSchemaAPI] Error updating metadata schema:", error);
        const errorMessage = extractErrorMessage(error);
        throw new Error(errorMessage);
    }
};

/**
 * Delete a metadata schema
 */
export const deleteMetadataSchema = async (
    databaseId: string,
    metadataSchemaId: string,
    deleteRequest: DeleteMetadataSchemaRequest,
    api = API
): Promise<MetadataSchemaOperationResponse> => {
    try {
        const response = await api.del(
            "api",
            `database/${databaseId}/metadataSchema/${metadataSchemaId}`,
            {
                body: deleteRequest,
            }
        );

        console.log("[metadataSchemaAPI] deleteMetadataSchema response:", response);

        // Check for error in response message
        if (
            response.message &&
            (response.message.indexOf("error") !== -1 || response.message.indexOf("Error") !== -1)
        ) {
            throw new Error(response.message);
        }

        return response;
    } catch (error: any) {
        console.error("[metadataSchemaAPI] Error deleting metadata schema:", error);
        const errorMessage = extractErrorMessage(error);
        throw new Error(errorMessage);
    }
};
