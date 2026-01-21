/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    MetadataRecord,
    MetadataRowState,
    MetadataValueType,
    MetadataChanges,
} from "../types/metadata.types";

/**
 * Convert API metadata records to row state for UI
 */
export const convertToRowState = (records: MetadataRecord[]): MetadataRowState[] => {
    return records.map((record) => ({
        ...record,
        isEditing: false,
        hasChanges: false,
        isNew: false,
        isDeleted: false,
        editKey: record.metadataKey,
        editValue: record.metadataValue,
        editType: record.metadataValueType,
        originalValue: record.metadataValue,
        originalType: record.metadataValueType,
    }));
};

/**
 * Convert row state back to API metadata records
 */
export const convertToMetadataRecords = (rows: MetadataRowState[]): MetadataRecord[] => {
    return rows
        .filter((row) => !row.isDeleted)
        .map((row) => ({
            metadataKey: row.metadataKey,
            metadataValue: row.metadataValue,
            metadataValueType: row.metadataValueType,
            metadataSchemaName: row.metadataSchemaName,
            metadataSchemaField: row.metadataSchemaField,
            metadataSchemaRequired: row.metadataSchemaRequired,
            metadataSchemaSequence: row.metadataSchemaSequence,
            metadataSchemaDefaultValue: row.metadataSchemaDefaultValue,
            metadataSchemaDependsOn: row.metadataSchemaDependsOn,
            metadataSchemaMultiFieldConflict: row.metadataSchemaMultiFieldConflict,
            metadataSchemaControlledListKeys: row.metadataSchemaControlledListKeys,
        }));
};

/**
 * Calculate changes between original and current state
 * Note: Rows that are added then deleted before commit are excluded from changes
 */
export const calculateChanges = (
    originalRows: MetadataRowState[],
    currentRows: MetadataRowState[]
): MetadataChanges => {
    const changes: MetadataChanges = {
        added: [],
        updated: [],
        deleted: [],
    };

    // Find added and updated records
    currentRows.forEach((row) => {
        // Skip rows that are marked as deleted
        if (row.isDeleted) {
            // If it's a new row that was deleted, don't count it at all
            // (it was never committed, so it's not a real change)
            if (!row.isNew) {
                // Only count as deleted if it existed before
                changes.deleted.push(row.metadataKey);
            }
            return;
        }

        if (row.isNew) {
            // New record that hasn't been deleted - use edit values
            changes.added.push({
                metadataKey: row.editKey,
                metadataValue: row.editValue,
                metadataValueType: row.editType,
            });
        } else if (row.hasChanges) {
            // Updated record - use edit values
            changes.updated.push({
                metadataKey: row.editKey,
                metadataValue: row.editValue,
                metadataValueType: row.editType,
            });
        }
    });

    // Find deleted records (that existed in original but not in current or marked as deleted)
    originalRows.forEach((originalRow) => {
        const currentRow = currentRows.find((r) => r.metadataKey === originalRow.metadataKey);
        if (!currentRow) {
            // Row was completely removed from array
            changes.deleted.push(originalRow.metadataKey);
        }
        // Note: isDeleted case is already handled above in the currentRows loop
    });

    return changes;
};

/**
 * Sort rows by schema sequence, then alphabetically
 */
export const sortRows = (rows: MetadataRowState[]): MetadataRowState[] => {
    return [...rows].sort((a, b) => {
        // Schema fields first, sorted by sequence
        if (a.metadataSchemaField && b.metadataSchemaField) {
            const seqA = a.metadataSchemaSequence ?? 999999;
            const seqB = b.metadataSchemaSequence ?? 999999;
            if (seqA !== seqB) {
                return seqA - seqB;
            }
        }

        // Schema fields before non-schema fields
        if (a.metadataSchemaField && !b.metadataSchemaField) {
            return -1;
        }
        if (!a.metadataSchemaField && b.metadataSchemaField) {
            return 1;
        }

        // Alphabetically by key
        return a.metadataKey.localeCompare(b.metadataKey);
    });
};

/**
 * Format metadata value for display based on type
 */
export const formatValueForDisplay = (value: string, type: MetadataValueType): string => {
    if (!value) {
        return "";
    }

    try {
        switch (type) {
            case "xyz": {
                const parsed = JSON.parse(value);
                return `X: ${parsed.x}, Y: ${parsed.y}, Z: ${parsed.z}`;
            }
            case "wxyz": {
                const parsed = JSON.parse(value);
                return `W: ${parsed.w}, X: ${parsed.x}, Y: ${parsed.y}, Z: ${parsed.z}`;
            }
            case "matrix4x4": {
                return "4x4 Matrix";
            }
            case "lla": {
                const parsed = JSON.parse(value);
                return `Lat: ${parsed.lat}, Long: ${parsed.long}, Alt: ${parsed.alt}`;
            }
            case "geopoint": {
                const parsed = JSON.parse(value);
                if (parsed.type === "Point" && parsed.coordinates) {
                    return `Point: [${parsed.coordinates[0]}, ${parsed.coordinates[1]}]`;
                }
                return "GeoJSON Point";
            }
            case "geojson": {
                const parsed = JSON.parse(value);
                return `GeoJSON ${parsed.type || "Object"}`;
            }
            case "json": {
                return "JSON Object";
            }
            case "date": {
                const date = new Date(value);
                return date.toLocaleDateString() + " " + date.toLocaleTimeString();
            }
            case "boolean": {
                return value.toLowerCase() === "true" ? "True" : "False";
            }
            default:
                return value;
        }
    } catch {
        // If parsing fails, show raw value
        return value;
    }
};

/**
 * Get display label for metadata value type
 */
export const getValueTypeLabel = (type: MetadataValueType): string => {
    const labels: Record<MetadataValueType, string> = {
        string: "String",
        multiline_string: "Multiline String",
        inline_controlled_list: "Controlled List",
        number: "Number",
        boolean: "Boolean",
        date: "Date",
        xyz: "XYZ",
        wxyz: "WXYZ",
        matrix4x4: "Matrix 4x4",
        geopoint: "GeoPoint",
        geojson: "GeoJSON",
        lla: "LLA",
        json: "JSON",
    };
    return labels[type] || type;
};

/**
 * Get all available metadata value types
 * Note: inline_controlled_list is excluded as it's only for schema-defined fields
 */
export const getAvailableValueTypes = (
    isFileAttribute: boolean = false
): Array<{ label: string; value: MetadataValueType }> => {
    const allTypes: Array<{ label: string; value: MetadataValueType }> = [
        { label: "String", value: "string" },
        { label: "Multiline String", value: "multiline_string" },
        { label: "Number", value: "number" },
        { label: "Boolean", value: "boolean" },
        { label: "Date", value: "date" },
        { label: "XYZ (3D Coordinates)", value: "xyz" },
        { label: "WXYZ (Quaternion)", value: "wxyz" },
        { label: "Matrix 4x4", value: "matrix4x4" },
        { label: "LLA (Lat/Long/Alt)", value: "lla" },
        { label: "GeoPoint", value: "geopoint" },
        { label: "GeoJSON", value: "geojson" },
        { label: "JSON", value: "json" },
    ];

    // File attributes only support string type
    if (isFileAttribute) {
        return [{ label: "String", value: "string" }];
    }

    return allTypes;
};

/**
 * Check if a row has unsaved changes
 */
export const hasUnsavedChanges = (rows: MetadataRowState[]): boolean => {
    return rows.some((row) => row.hasChanges || row.isNew || row.isDeleted);
};

/**
 * Get count of changes
 */
export const getChangesCount = (
    rows: MetadataRowState[]
): {
    added: number;
    updated: number;
    deleted: number;
    total: number;
} => {
    const added = rows.filter((r) => r.isNew && !r.isDeleted).length;
    const updated = rows.filter((r) => r.hasChanges && !r.isNew && !r.isDeleted).length;
    const deleted = rows.filter((r) => r.isDeleted).length;

    return {
        added,
        updated,
        deleted,
        total: added + updated + deleted,
    };
};

/**
 * Create a new empty row
 */
export const createNewRow = (): MetadataRowState => {
    return {
        metadataKey: "",
        metadataValue: "",
        metadataValueType: "string",
        isEditing: true,
        hasChanges: true,
        isNew: true,
        isDeleted: false,
        editKey: "",
        editValue: "",
        editType: "string",
    };
};

/**
 * Check if a metadata key is a schema field
 */
export const isSchemaField = (row: MetadataRowState): boolean => {
    return row.metadataSchemaField === true;
};

/**
 * Check if a schema field can be deleted (only value can be cleared)
 */
export const canDeleteSchemaField = (row: MetadataRowState): boolean => {
    return isSchemaField(row) && !row.metadataSchemaRequired;
};

/**
 * Merge initial data with schema defaults
 */
export const mergeWithSchemaDefaults = (
    initialData: MetadataRecord[],
    schemaFields: MetadataRecord[]
): MetadataRecord[] => {
    const merged = [...initialData];
    const existingKeys = new Set(initialData.map((r) => r.metadataKey));

    // Add schema fields with default values if not present
    schemaFields.forEach((schemaField) => {
        if (!existingKeys.has(schemaField.metadataKey) && schemaField.metadataSchemaDefaultValue) {
            merged.push({
                ...schemaField,
                metadataValue: schemaField.metadataSchemaDefaultValue,
            });
        }
    });

    return merged;
};
