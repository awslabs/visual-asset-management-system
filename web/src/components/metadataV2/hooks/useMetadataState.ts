/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useCallback, useEffect } from "react";
import {
    MetadataRecord,
    MetadataRowState,
    MetadataValueType,
    MetadataChanges,
} from "../types/metadata.types";
import {
    convertToRowState,
    sortRows,
    createNewRow,
    calculateChanges,
    hasUnsavedChanges,
} from "../utils/metadataHelpers";

interface UseMetadataStateResult {
    rows: MetadataRowState[];
    originalRows: MetadataRowState[];
    hasChanges: boolean;
    changes: MetadataChanges;
    setRows: (rows: MetadataRowState[]) => void;
    initializeRows: (data: MetadataRecord[]) => void;
    addNewRow: () => void;
    updateRow: (index: number, updates: Partial<MetadataRowState>) => void;
    deleteRow: (index: number) => void;
    resetChanges: () => void;
    commitChanges: () => void;
}

/**
 * Custom hook for managing metadata state
 */
export const useMetadataState = (initialData: MetadataRecord[] = []): UseMetadataStateResult => {
    const [rows, setRows] = useState<MetadataRowState[]>([]);
    const [originalRows, setOriginalRows] = useState<MetadataRowState[]>([]);

    // Initialize rows from data
    const initializeRows = useCallback((data: MetadataRecord[]) => {
        console.log("[useMetadataState] Initializing rows with data:", data);
        const rowState = convertToRowState(data);
        const sorted = sortRows(rowState);
        setRows(sorted);
        setOriginalRows(sorted);
    }, []);

    // Initialize on mount only - use ref to track if we've initialized
    const hasInitialized = React.useRef(false);
    const lastInitialDataRef = React.useRef<string>("");

    useEffect(() => {
        console.log(
            "[useMetadataState] useEffect called - hasInitialized:",
            hasInitialized.current
        );

        // Only initialize once on mount with initialData
        if (!hasInitialized.current && initialData.length >= 0) {
            console.log(
                "[useMetadataState] Initial mount - initializing with initialData:",
                initialData
            );
            initializeRows(initialData);
            lastInitialDataRef.current = JSON.stringify(initialData);
            hasInitialized.current = true;
        } else {
            console.log("[useMetadataState] Skipping initialization - already initialized");
        }
        // After initial mount, ignore changes to initialData
        // The component manages its own state and only syncs back via onDataChange when committing
    }, []); // Empty deps - only run once on mount

    // Add a new row
    const addNewRow = useCallback(() => {
        const newRow = createNewRow();
        setRows((prev) => [...prev, newRow]);
    }, []);

    // Update a specific row
    const updateRow = useCallback(
        (index: number, updates: Partial<MetadataRowState>) => {
            console.log("[useMetadataState] updateRow called - index:", index, "updates:", updates);
            setRows((prev) =>
                prev.map((row, i) => {
                    if (i !== index) return row;

                    const updated = { ...row, ...updates };

                    // For new rows, hasChanges is always true if explicitly set
                    if (row.isNew && updates.hasChanges !== undefined) {
                        updated.hasChanges = updates.hasChanges;
                        return updated;
                    }

                    // Check if there are changes compared to original (for existing rows)
                    const original = originalRows.find((r) => r.metadataKey === row.metadataKey);
                    if (original && !row.isNew) {
                        // Compare edit values to original values
                        updated.hasChanges =
                            updated.editValue !== original.metadataValue ||
                            updated.editType !== original.metadataValueType ||
                            updated.editKey !== original.metadataKey;
                    }

                    return updated;
                })
            );
        },
        [originalRows]
    );

    // Delete a row (mark as deleted or remove if new)
    const deleteRow = useCallback((index: number) => {
        setRows((prev) => {
            const row = prev[index];

            // If it's a new row that hasn't been saved, remove it completely
            if (row.isNew) {
                return prev.filter((_, i) => i !== index);
            }

            // If it's a schema field, we can only clear the value, not delete
            if (row.metadataSchemaField) {
                // Check if value is already empty - if so, don't mark as changed
                const valueAlreadyEmpty = !row.metadataValue || row.metadataValue.trim() === "";

                return prev.map((r, i) =>
                    i === index
                        ? {
                              ...r,
                              metadataValue: "",
                              editValue: "",
                              hasChanges: !valueAlreadyEmpty, // Only mark as changed if value wasn't already empty
                              isEditing: false,
                          }
                        : r
                );
            }

            // Otherwise, mark as deleted
            return prev.map((r, i) =>
                i === index ? { ...r, isDeleted: true, isEditing: false } : r
            );
        });
    }, []);

    // Reset all changes
    const resetChanges = useCallback(() => {
        setRows(originalRows);
    }, [originalRows]);

    // Commit changes (apply edit values to actual values and update original state)
    const commitChanges = useCallback(() => {
        const activeRows = rows.filter((r) => !r.isDeleted);

        // Apply edit values to actual values
        const committed = activeRows.map((row) => ({
            ...row,
            metadataKey: row.editKey,
            metadataValue: row.editValue,
            metadataValueType: row.editType,
            isEditing: false,
            hasChanges: false,
            isNew: false,
            originalValue: row.editValue,
            originalType: row.editType,
        }));

        const sorted = sortRows(committed);
        setRows(sorted);
        setOriginalRows(sorted);
    }, [rows]);

    // Calculate if there are unsaved changes
    const hasChanges = hasUnsavedChanges(rows);

    // Calculate the changes
    const changes = calculateChanges(originalRows, rows);

    return {
        rows,
        originalRows,
        hasChanges,
        changes,
        setRows,
        initializeRows,
        addNewRow,
        updateRow,
        deleteRow,
        resetChanges,
        commitChanges,
    };
};

export default useMetadataState;
