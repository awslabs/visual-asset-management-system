/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { MetadataRowState, MetadataValueType, ValidationResult } from "../types/metadata.types";

/**
 * Validate a metadata value based on its type
 */
export const validateMetadataValue = (value: string, type: MetadataValueType): ValidationResult => {
    const errors: string[] = [];

    // Empty values are allowed (required check is separate)
    if (!value || value.trim() === "") {
        return { isValid: true, errors: [] };
    }

    try {
        switch (type) {
            case "string":
            case "multiline_string":
            case "inline_controlled_list":
                // No validation needed for strings
                break;

            case "number":
                if (isNaN(Number(value))) {
                    errors.push("Value must be a valid number");
                }
                break;

            case "boolean":
                if (value.toLowerCase() !== "true" && value.toLowerCase() !== "false") {
                    errors.push('Value must be "true" or "false"');
                }
                break;

            case "date":
                try {
                    const date = new Date(value);
                    if (isNaN(date.getTime())) {
                        errors.push("Value must be a valid ISO date format");
                    }
                } catch {
                    errors.push("Value must be a valid ISO date format");
                }
                break;

            case "xyz": {
                const parsed = JSON.parse(value);
                if (typeof parsed !== "object" || parsed === null) {
                    errors.push("XYZ value must be a JSON object");
                    break;
                }
                const requiredKeys = ["x", "y", "z"];
                const missingKeys = requiredKeys.filter((key) => !(key in parsed));
                if (missingKeys.length > 0) {
                    errors.push(`XYZ value must contain keys: ${requiredKeys.join(", ")}`);
                }
                requiredKeys.forEach((key) => {
                    if (key in parsed && typeof parsed[key] !== "number") {
                        errors.push(`XYZ coordinate '${key}' must be a number`);
                    }
                });
                break;
            }

            case "wxyz": {
                const parsed = JSON.parse(value);
                if (typeof parsed !== "object" || parsed === null) {
                    errors.push("WXYZ value must be a JSON object");
                    break;
                }
                const requiredKeys = ["w", "x", "y", "z"];
                const missingKeys = requiredKeys.filter((key) => !(key in parsed));
                if (missingKeys.length > 0) {
                    errors.push(`WXYZ value must contain keys: ${requiredKeys.join(", ")}`);
                }
                requiredKeys.forEach((key) => {
                    if (key in parsed && typeof parsed[key] !== "number") {
                        errors.push(`WXYZ coordinate '${key}' must be a number`);
                    }
                });
                break;
            }

            case "matrix4x4": {
                const parsed = JSON.parse(value);
                if (!Array.isArray(parsed)) {
                    errors.push("Matrix4x4 value must be a JSON array");
                    break;
                }
                if (parsed.length !== 4) {
                    errors.push("Matrix4x4 must be a 4x4 matrix (4 rows)");
                    break;
                }
                parsed.forEach((row, i) => {
                    if (!Array.isArray(row)) {
                        errors.push(`Matrix4x4 row ${i} must be an array`);
                    } else if (row.length !== 4) {
                        errors.push(`Matrix4x4 row ${i} must contain exactly 4 elements`);
                    } else {
                        row.forEach((element, j) => {
                            if (typeof element !== "number") {
                                errors.push(`Matrix4x4 element at [${i}][${j}] must be a number`);
                            }
                        });
                    }
                });
                break;
            }

            case "lla": {
                const parsed = JSON.parse(value);
                if (typeof parsed !== "object" || parsed === null) {
                    errors.push("LLA value must be a JSON object");
                    break;
                }
                const requiredKeys = ["lat", "long", "alt"];
                const missingKeys = requiredKeys.filter((key) => !(key in parsed));
                if (missingKeys.length > 0) {
                    errors.push(`LLA value must contain keys: ${requiredKeys.join(", ")}`);
                }
                if ("lat" in parsed) {
                    if (typeof parsed.lat !== "number") {
                        errors.push("LLA latitude must be a number");
                    } else if (parsed.lat < -90 || parsed.lat > 90) {
                        errors.push("LLA latitude must be between -90 and 90");
                    }
                }
                if ("long" in parsed) {
                    if (typeof parsed.long !== "number") {
                        errors.push("LLA longitude must be a number");
                    } else if (parsed.long < -180 || parsed.long > 180) {
                        errors.push("LLA longitude must be between -180 and 180");
                    }
                }
                if ("alt" in parsed && typeof parsed.alt !== "number") {
                    errors.push("LLA altitude must be a number");
                }
                break;
            }

            case "geopoint": {
                const parsed = JSON.parse(value);
                if (typeof parsed !== "object" || parsed === null) {
                    errors.push("GeoPoint value must be a JSON object");
                    break;
                }
                if (parsed.type !== "Point") {
                    errors.push('GeoPoint type must be "Point"');
                }
                if (!Array.isArray(parsed.coordinates) || parsed.coordinates.length !== 2) {
                    errors.push("GeoPoint coordinates must be an array of 2 numbers");
                }
                break;
            }

            case "geojson": {
                const parsed = JSON.parse(value);
                if (typeof parsed !== "object" || parsed === null) {
                    errors.push("GeoJSON value must be a JSON object");
                    break;
                }
                if (!parsed.type) {
                    errors.push("GeoJSON must have a type property");
                }
                break;
            }

            case "json": {
                JSON.parse(value); // Will throw if invalid
                break;
            }

            default:
                errors.push(`Unknown metadata value type: ${type}`);
        }
    } catch (e) {
        if (
            type === "xyz" ||
            type === "wxyz" ||
            type === "matrix4x4" ||
            type === "lla" ||
            type === "geopoint" ||
            type === "geojson" ||
            type === "json"
        ) {
            errors.push("Value must be valid JSON");
        } else {
            errors.push(`Validation error: ${e instanceof Error ? e.message : "Unknown error"}`);
        }
    }

    return {
        isValid: errors.length === 0,
        errors,
    };
};

/**
 * Validate a metadata row including schema rules
 * Uses editKey/editValue/editType for validation to support always-editable workflow
 */
export const validateMetadataRow = (
    row: MetadataRowState,
    allRows: MetadataRowState[]
): ValidationResult => {
    const errors: string[] = [];

    // Use editKey for validation (supports always-editable workflow)
    const keyToValidate = row.editKey || row.metadataKey;
    const valueToValidate = row.editValue || row.metadataValue;
    const typeToValidate = row.editType || row.metadataValueType;

    // Validate key
    if (!keyToValidate || keyToValidate.trim() === "") {
        errors.push("Metadata key is required");
    }

    // Check for duplicate keys (compare against both metadataKey and editKey)
    const duplicates = allRows.filter((r) => {
        if (r === row || r.isDeleted) return false;
        const otherKey = r.editKey || r.metadataKey;
        return otherKey === keyToValidate;
    });
    if (duplicates.length > 0) {
        errors.push(`Duplicate metadata key: ${keyToValidate}`);
    }

    // Validate required fields
    // Check editValue first (even if empty string), then fall back to metadataValue
    if (row.metadataSchemaRequired) {
        const actualValue =
            row.editValue !== undefined && row.editValue !== null
                ? row.editValue
                : row.metadataValue;
        if (!actualValue || actualValue.trim() === "") {
            errors.push("This field is required by the schema");
        }
    }

    // Validate dependencies
    if (row.metadataSchemaDependsOn && row.metadataSchemaDependsOn.length > 0) {
        if (valueToValidate && valueToValidate.trim() !== "") {
            const missingDependencies = row.metadataSchemaDependsOn.filter((depKey) => {
                const depRow = allRows.find((r) => {
                    const rKey = r.editKey || r.metadataKey;
                    return rKey === depKey && !r.isDeleted;
                });
                if (!depRow) return true;
                const depValue = depRow.editValue || depRow.metadataValue;
                return !depValue || depValue.trim() === "";
            });

            if (missingDependencies.length > 0) {
                errors.push(
                    `This field depends on: ${missingDependencies.join(
                        ", "
                    )}. Please fill those fields first.`
                );
            }
        }
    }

    // Validate value type
    const valueValidation = validateMetadataValue(valueToValidate, typeToValidate);
    if (!valueValidation.isValid) {
        errors.push(...valueValidation.errors);
    }

    // Validate inline controlled list
    if (
        typeToValidate === "inline_controlled_list" &&
        row.metadataSchemaControlledListKeys &&
        row.metadataSchemaControlledListKeys.length > 0
    ) {
        if (valueToValidate && !row.metadataSchemaControlledListKeys.includes(valueToValidate)) {
            errors.push(`Value must be one of: ${row.metadataSchemaControlledListKeys.join(", ")}`);
        }
    }

    return {
        isValid: errors.length === 0,
        errors,
    };
};

/**
 * Validate all rows before commit
 */
export const validateAllRows = (rows: MetadataRowState[]): ValidationResult => {
    const errors: string[] = [];
    const activeRows = rows.filter((r) => !r.isDeleted);

    activeRows.forEach((row, index) => {
        const rowValidation = validateMetadataRow(row, activeRows);
        if (!rowValidation.isValid) {
            const rowName = row.editKey || row.metadataKey || "unnamed";
            errors.push(`Row ${index + 1} (${rowName}): ${rowValidation.errors.join(", ")}`);
        }
    });

    return {
        isValid: errors.length === 0,
        errors,
    };
};

/**
 * Check if a value can be parsed as JSON
 */
export const isValidJSON = (value: string): boolean => {
    try {
        JSON.parse(value);
        return true;
    } catch {
        return false;
    }
};

/**
 * Validate metadata key format
 */
export const validateMetadataKey = (key: string): ValidationResult => {
    const errors: string[] = [];

    if (!key || key.trim() === "") {
        errors.push("Metadata key cannot be empty");
    }

    if (key.length > 256) {
        errors.push("Metadata key cannot exceed 256 characters");
    }

    // Check for invalid characters (optional - adjust based on backend requirements)
    const invalidChars = /[<>:"/\\|?*\x00-\x1F]/;
    if (invalidChars.test(key)) {
        errors.push("Metadata key contains invalid characters");
    }

    return {
        isValid: errors.length === 0,
        errors,
    };
};
