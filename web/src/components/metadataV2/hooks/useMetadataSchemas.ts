/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useCallback, useEffect } from "react";
import { fetchMetadataSchemas } from "../../../services/metadataSchemaAPI";
import { MetadataValueType } from "../types/metadata.types";

export interface MetadataSchemaField {
    metadataFieldKeyName: string;
    metadataFieldValueType: MetadataValueType;
    required?: boolean;
    sequence?: number;
    dependsOnFieldKeyName?: string;
    controlledListKeys?: string[];
    defaultMetadataFieldValue?: string;
    hasConflict?: boolean;
}

export interface AggregatedSchema {
    fields: MetadataSchemaField[];
    schemaNames: string[];
}

interface UseMetadataSchemasResult {
    schemas: AggregatedSchema | null;
    loading: boolean;
    error: string | null;
    refetch: () => Promise<void>;
}

/**
 * Custom hook for fetching and aggregating metadata schemas
 * Fetches schemas from both the selected database and GLOBAL database
 * Filters by entity type and aggregates all schema fields
 */
export const useMetadataSchemas = (
    databaseId?: string,
    entityType?: "asset" | "assetLink",
    enabled: boolean = true
): UseMetadataSchemasResult => {
    const [schemas, setSchemas] = useState<AggregatedSchema | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const refetch = useCallback(async () => {
        // Skip if not enabled or missing required params
        if (!enabled || !databaseId || !entityType) {
            setSchemas(null);
            return;
        }

        setLoading(true);
        setError(null);

        try {
            console.log("[useMetadataSchemas] Fetching schemas for:", {
                databaseId,
                entityType,
            });

            // Map entity type to backend format
            const backendEntityType =
                entityType === "asset" ? "assetMetadata" : "assetLinkMetadata";

            // Fetch schemas from both selected database and GLOBAL
            const databaseIdsToFetch =
                databaseId === "GLOBAL" ? ["GLOBAL"] : [databaseId, "GLOBAL"];

            const schemaPromises = databaseIdsToFetch.map((dbId) =>
                fetchMetadataSchemas(dbId, backendEntityType as any)
            );

            const results = await Promise.all(schemaPromises);

            console.log("[useMetadataSchemas] Fetched schema results:", results);

            // Aggregate all schemas
            const allSchemas: any[] = [];
            const schemaNames: string[] = [];

            results.forEach((result) => {
                if (result.Items && Array.isArray(result.Items)) {
                    result.Items.forEach((schema) => {
                        allSchemas.push(schema);
                        if (schema.schemaName) {
                            schemaNames.push(schema.schemaName);
                        }
                    });
                }
            });

            console.log("[useMetadataSchemas] All schemas:", allSchemas);

            // Aggregate all fields from all schemas with conflict detection
            const fieldMap = new Map<string, MetadataSchemaField>();
            const conflictMap = new Map<string, boolean>(); // Track which fields have conflicts

            allSchemas.forEach((schema) => {
                // Handle both nested fields structure and direct array
                let fieldsArray: any[] = [];

                if (schema.fields) {
                    if (Array.isArray(schema.fields)) {
                        // Direct array of fields
                        fieldsArray = schema.fields;
                    } else if (schema.fields.fields && Array.isArray(schema.fields.fields)) {
                        // Nested fields structure
                        fieldsArray = schema.fields.fields;
                    }
                }

                fieldsArray.forEach((field: any) => {
                    // Handle both field name formats
                    const fieldKey = field.metadataFieldKeyName || field.fieldName;
                    const fieldType = (
                        field.metadataFieldValueType ||
                        field.fieldType ||
                        "string"
                    ).toLowerCase();

                    if (!fieldKey) {
                        console.warn("[useMetadataSchemas] Skipping field without key:", field);
                        return;
                    }

                    // If field already exists, check for conflicts
                    if (fieldMap.has(fieldKey)) {
                        const existing = fieldMap.get(fieldKey)!;

                        // Check for conflicts in field properties
                        const hasConflict =
                            existing.metadataFieldValueType !== fieldType ||
                            existing.required !== (field.required || false) ||
                            existing.sequence !== field.sequence ||
                            existing.dependsOnFieldKeyName !==
                                (field.dependsOnFieldKeyName || field.dependsOn) ||
                            existing.defaultMetadataFieldValue !==
                                (field.defaultMetadataFieldValue || field.defaultValue);

                        if (hasConflict) {
                            console.warn(
                                `[useMetadataSchemas] Schema conflict detected for field "${fieldKey}":`,
                                {
                                    existing: existing,
                                    new: {
                                        type: fieldType,
                                        required: field.required,
                                        sequence: field.sequence,
                                        dependsOn: field.dependsOnFieldKeyName || field.dependsOn,
                                        defaultValue:
                                            field.defaultMetadataFieldValue || field.defaultValue,
                                    },
                                }
                            );

                            // Mark as conflict
                            conflictMap.set(fieldKey, true);

                            // Resort to safe defaults for conflicting fields
                            fieldMap.set(fieldKey, {
                                metadataFieldKeyName: fieldKey,
                                metadataFieldValueType: "string", // Safe default
                                required: false, // Not required
                                sequence: undefined, // No sequence
                                dependsOnFieldKeyName: undefined, // No dependencies
                                controlledListKeys: undefined,
                                defaultMetadataFieldValue: undefined, // No default value
                                hasConflict: true, // Mark the conflict
                            });
                        }
                    } else {
                        // First occurrence of this field
                        fieldMap.set(fieldKey, {
                            metadataFieldKeyName: fieldKey,
                            metadataFieldValueType: fieldType as MetadataValueType,
                            required: field.required || false,
                            sequence: field.sequence,
                            dependsOnFieldKeyName: field.dependsOnFieldKeyName || field.dependsOn,
                            controlledListKeys: field.controlledListKeys,
                            defaultMetadataFieldValue:
                                field.defaultMetadataFieldValue || field.defaultValue,
                            hasConflict: false,
                        });
                    }
                });
            });

            // Convert map to array and sort by sequence if available
            const aggregatedFields = Array.from(fieldMap.values()).sort((a, b) => {
                if (a.sequence !== undefined && b.sequence !== undefined) {
                    return a.sequence - b.sequence;
                }
                if (a.sequence !== undefined) return -1;
                if (b.sequence !== undefined) return 1;
                return a.metadataFieldKeyName.localeCompare(b.metadataFieldKeyName);
            });

            console.log("[useMetadataSchemas] Aggregated fields:", aggregatedFields);

            setSchemas({
                fields: aggregatedFields,
                schemaNames: schemaNames,
            });
        } catch (err) {
            console.error("[useMetadataSchemas] Error fetching schemas:", err);
            setError(err instanceof Error ? err.message : "Failed to fetch metadata schemas");
            setSchemas(null);
        } finally {
            setLoading(false);
        }
    }, [databaseId, entityType, enabled]);

    // Auto-fetch when dependencies change
    useEffect(() => {
        refetch();
    }, [refetch]);

    return {
        schemas,
        loading,
        error,
        refetch,
    };
};

export default useMetadataSchemas;
