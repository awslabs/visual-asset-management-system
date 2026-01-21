/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback } from "react";
import { MetadataRowState, ValidationResult } from "../types/metadata.types";
import { validateMetadataRow, validateAllRows } from "../utils/validationHelpers";

interface UseMetadataValidationResult {
    validateRow: (row: MetadataRowState, allRows: MetadataRowState[]) => ValidationResult;
    validateAll: (rows: MetadataRowState[]) => ValidationResult;
    canSaveRow: (row: MetadataRowState, allRows: MetadataRowState[]) => boolean;
    canCommitChanges: (rows: MetadataRowState[]) => boolean;
}

/**
 * Custom hook for metadata validation logic
 */
export const useMetadataValidation = (): UseMetadataValidationResult => {
    // Validate a single row
    const validateRow = useCallback(
        (row: MetadataRowState, allRows: MetadataRowState[]): ValidationResult => {
            return validateMetadataRow(row, allRows);
        },
        []
    );

    // Validate all rows
    const validateAll = useCallback((rows: MetadataRowState[]): ValidationResult => {
        return validateAllRows(rows);
    }, []);

    // Check if a row can be saved
    const canSaveRow = useCallback(
        (row: MetadataRowState, allRows: MetadataRowState[]): boolean => {
            // Must have a key
            if (!row.editKey || row.editKey.trim() === "") {
                return false;
            }

            // Must have changes or be new
            if (!row.hasChanges && !row.isNew) {
                return false;
            }

            // Validate the row
            const validation = validateRow(row, allRows);
            return validation.isValid;
        },
        [validateRow]
    );

    // Check if changes can be committed
    const canCommitChanges = useCallback(
        (rows: MetadataRowState[]): boolean => {
            // Must have changes
            const hasChanges = rows.some((r) => r.hasChanges || r.isNew || r.isDeleted);
            if (!hasChanges) {
                return false;
            }

            // All non-deleted rows must be valid (including new rows with keys)
            const validation = validateAll(rows.filter((r) => !r.isDeleted));
            return validation.isValid;
        },
        [validateAll]
    );

    return {
        validateRow,
        validateAll,
        canSaveRow,
        canCommitChanges,
    };
};

export default useMetadataValidation;
