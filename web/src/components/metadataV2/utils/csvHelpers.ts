/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { MetadataRowState, MetadataValueType } from "../types/metadata.types";

/**
 * Convert metadata rows to CSV format
 * Exports: Metadata Key, Value Type, Metadata Value, plus all schema fields
 */
export const exportToCSV = (rows: MetadataRowState[]): string => {
    // CSV header - 13 columns (3 main + 10 schema fields)
    const header = [
        "Metadata Key",
        "Value Type",
        "Metadata Value",
        "Schema Name",
        "Schema Conflict",
        "Required",
        "Default Value",
        "Sequence",
        "Schema Value Type",
        "Inline Controlled List",
        "Depends On",
    ].join(",");

    // CSV rows
    const csvRows = rows
        .filter((row) => !row.isDeleted)
        .map((row) => {
            // Escape values that contain commas, quotes, or newlines
            const escapeCSV = (value: string | number | boolean | null | undefined): string => {
                if (value === null || value === undefined) return "";
                const strValue = String(value);
                if (!strValue) return "";
                // If value contains comma, quote, or newline, wrap in quotes and escape internal quotes
                if (strValue.includes(",") || strValue.includes('"') || strValue.includes("\n")) {
                    return `"${strValue.replace(/"/g, '""')}"`;
                }
                return strValue;
            };

            // Format array fields as semicolon-delimited strings
            const formatArray = (arr: string[] | null | undefined): string => {
                if (!arr || arr.length === 0) return "";
                return arr.join("; ");
            };

            return [
                escapeCSV(row.metadataKey),
                escapeCSV(row.metadataValueType),
                escapeCSV(row.metadataValue),
                escapeCSV(row.metadataSchemaName),
                escapeCSV(row.metadataSchemaMultiFieldConflict),
                escapeCSV(row.metadataSchemaRequired),
                escapeCSV(row.metadataSchemaDefaultValue),
                escapeCSV(row.metadataSchemaSequence),
                // Schema Value Type is same as Value Type for schema fields
                escapeCSV(row.metadataSchemaField ? row.metadataValueType : ""),
                escapeCSV(formatArray(row.metadataSchemaControlledListKeys)),
                escapeCSV(formatArray(row.metadataSchemaDependsOn)),
            ].join(",");
        });

    return [header, ...csvRows].join("\n");
};

/**
 * Download CSV file
 */
export const downloadCSV = (csvContent: string, filename: string = "metadata.csv"): void => {
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);

    link.setAttribute("href", url);
    link.setAttribute("download", filename);
    link.style.visibility = "hidden";

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    URL.revokeObjectURL(url);
};

/**
 * Parse CSV content to metadata rows with validation
 * Only reads first 3 columns: Metadata Key, Value Type, Metadata Value
 */
export const importFromCSV = (
    csvContent: string,
    existingRows?: MetadataRowState[],
    restrictMetadataOutsideSchemas?: boolean
): {
    rows: Partial<MetadataRowState>[];
    errors: string[];
} => {
    const errors: string[] = [];
    const rows: Partial<MetadataRowState>[] = [];

    try {
        const lines = csvContent.split("\n").filter((line) => line.trim() !== "");

        if (lines.length === 0) {
            errors.push("CSV file is empty");
            return { rows, errors };
        }

        // Parse header
        const header = parseCSVLine(lines[0]);
        const expectedHeaders = ["Metadata Key", "Value Type", "Metadata Value"];

        // Validate header (at minimum, first 3 columns must match)
        const hasValidHeader = expectedHeaders.every(
            (h, i) => header[i] && header[i].toLowerCase() === h.toLowerCase()
        );

        if (!hasValidHeader) {
            errors.push(
                "Invalid CSV format. Expected columns: Metadata Key, Value Type, Metadata Value"
            );
            return { rows, errors };
        }

        // Track keys to detect duplicates within the import
        const importedKeys = new Set<string>();

        // Get schema fields from existing rows for validation
        const schemaFields = existingRows?.filter((r) => r.metadataSchemaField) || [];
        const schemaKeyMap = new Map(schemaFields.map((r) => [r.metadataKey, r.metadataValueType]));

        // Parse data rows (only read first 3 columns)
        for (let i = 1; i < lines.length; i++) {
            const lineNumber = i + 1;
            const values = parseCSVLine(lines[i]);

            if (values.length < 3) {
                errors.push(`Line ${lineNumber}: Insufficient columns (need at least 3)`);
                continue;
            }

            // Only read first 3 columns
            const [key, type, value] = values.slice(0, 3);

            // Validate key
            if (!key || key.trim() === "") {
                errors.push(`Line ${lineNumber}: Metadata key is required`);
                continue;
            }

            const trimmedKey = key.trim();

            // Check for duplicate keys within the import
            if (importedKeys.has(trimmedKey)) {
                errors.push(`Line ${lineNumber}: Duplicate key "${trimmedKey}" found in import`);
                continue;
            }
            importedKeys.add(trimmedKey);

            // Validate type
            const validTypes: MetadataValueType[] = [
                "string",
                "multiline_string",
                "inline_controlled_list",
                "number",
                "boolean",
                "date",
                "xyz",
                "wxyz",
                "matrix4x4",
                "geopoint",
                "geojson",
                "lla",
                "json",
            ];

            if (!validTypes.includes(type as MetadataValueType)) {
                errors.push(
                    `Line ${lineNumber}: Invalid value type "${type}". Must be one of: ${validTypes.join(
                        ", "
                    )}`
                );
                continue;
            }

            // Check if this key matches a schema field
            if (schemaKeyMap.has(trimmedKey)) {
                const schemaType = schemaKeyMap.get(trimmedKey);
                if (schemaType !== type) {
                    errors.push(
                        `Line ${lineNumber}: Key "${trimmedKey}" is a schema field with type "${schemaType}". Cannot change to "${type}"`
                    );
                    continue;
                }
            }

            // Check restrictMetadataOutsideSchemas
            if (restrictMetadataOutsideSchemas && schemaFields.length > 0) {
                // If restricted and this key is not in schemas, reject it
                if (!schemaKeyMap.has(trimmedKey)) {
                    errors.push(
                        `Line ${lineNumber}: Key "${trimmedKey}" is not a schema field. Adding new fields is restricted.`
                    );
                    continue;
                }
            }

            // Create row
            rows.push({
                metadataKey: trimmedKey,
                metadataValue: value || "",
                metadataValueType: type as MetadataValueType,
                isNew: true,
                hasChanges: true,
                isEditing: false,
                isDeleted: false,
                editKey: trimmedKey,
                editValue: value || "",
                editType: type as MetadataValueType,
            });
        }

        if (rows.length === 0 && errors.length === 0) {
            errors.push("No valid data rows found in CSV");
        }
    } catch (error) {
        errors.push(
            `CSV parsing error: ${error instanceof Error ? error.message : "Unknown error"}`
        );
    }

    return { rows, errors };
};

/**
 * Parse a single CSV line, handling quoted values
 */
const parseCSVLine = (line: string): string[] => {
    const result: string[] = [];
    let current = "";
    let inQuotes = false;

    for (let i = 0; i < line.length; i++) {
        const char = line[i];
        const nextChar = line[i + 1];

        if (char === '"') {
            if (inQuotes && nextChar === '"') {
                // Escaped quote
                current += '"';
                i++; // Skip next quote
            } else {
                // Toggle quote mode
                inQuotes = !inQuotes;
            }
        } else if (char === "," && !inQuotes) {
            // End of field
            result.push(current);
            current = "";
        } else {
            current += char;
        }
    }

    // Add last field
    result.push(current);

    return result;
};

/**
 * Read file as text
 */
export const readFileAsText = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();

        reader.onload = (event) => {
            if (event.target?.result) {
                resolve(event.target.result as string);
            } else {
                reject(new Error("Failed to read file"));
            }
        };

        reader.onerror = () => {
            reject(new Error("Error reading file"));
        };

        reader.readAsText(file);
    });
};

/**
 * Validate CSV file before import
 */
export const validateCSVFile = (file: File): { valid: boolean; error?: string } => {
    // Check file type
    if (!file.name.endsWith(".csv") && file.type !== "text/csv") {
        return { valid: false, error: "File must be a CSV file (.csv)" };
    }

    // Check file size (max 5MB)
    const maxSize = 5 * 1024 * 1024;
    if (file.size > maxSize) {
        return { valid: false, error: "File size exceeds 5MB limit" };
    }

    return { valid: true };
};
