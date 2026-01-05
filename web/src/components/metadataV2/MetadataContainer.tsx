/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Container, Alert, SpaceBetween, Tabs } from "@cloudscape-design/components";
import {
    EntityType,
    FileMetadataType,
    MetadataContainerProps,
    MetadataValueType,
    EditMode,
    MetadataRecord,
} from "./types/metadata.types";
import {
    useMetadataFetch,
    useMetadataState,
    useMetadataValidation,
    useMetadataSchemas,
} from "./hooks";
import MetadataTable from "./MetadataTable";
import BulkEditMode from "./BulkEditMode";
import MetadataSearchFilter from "./MetadataSearchFilter";
import { createMetadata, updateMetadata, deleteMetadata } from "./utils/apiHelpers";
import { convertToMetadataRecords } from "./utils/metadataHelpers";

export const MetadataContainer: React.FC<MetadataContainerProps> = ({
    entityType,
    entityId,
    databaseId,
    filePath,
    fileType,
    mode = "online",
    initialData = [],
    onDataChange,
    readOnly = false,
    showBulkEdit = true,
    restrictMetadataOutsideSchemas = false,
}) => {
    const [editMode, setEditMode] = useState<EditMode>("normal");
    const [error, setError] = useState<string | null>(null);
    const [refreshing, setRefreshing] = useState(false);
    // Initialize activeFileType immediately to prevent extra refetch
    const [activeFileType, setActiveFileType] = useState<FileMetadataType | null>(
        entityType === "file" ? fileType || "attribute" : null
    );
    const [dismissedFetchError, setDismissedFetchError] = useState(false);
    const hasInitializedTab = useRef(false);
    const [restrictMetadata, setRestrictMetadata] = useState(restrictMetadataOutsideSchemas);
    const [hasSchemas, setHasSchemas] = useState(false);

    // Fetch metadata schemas for offline mode
    const entityTypeForSchema =
        entityType === "asset" ? "asset" : entityType === "assetLink" ? "assetLink" : undefined;
    const {
        schemas,
        loading: schemasLoading,
        error: schemasError,
    } = useMetadataSchemas(
        databaseId,
        entityTypeForSchema,
        mode === "offline" && !!databaseId && !!entityTypeForSchema
    );

    // Search and filter state
    const [searchTerm, setSearchTerm] = useState("");
    const [typeFilter, setTypeFilter] = useState<MetadataValueType | null>(null);
    const [schemaOnlyFilter, setSchemaOnlyFilter] = useState(false);

    // Hooks
    const {
        data,
        loading,
        error: fetchError,
        refetch,
        restrictMetadataOutsideSchemas: apiRestriction,
        attributeCount,
        metadataCount,
    } = useMetadataFetch(
        entityType,
        entityId,
        databaseId,
        filePath,
        entityType === "file" ? activeFileType || "attribute" : fileType,
        mode
    );

    const {
        rows,
        hasChanges,
        changes,
        initializeRows,
        addNewRow,
        updateRow,
        deleteRow,
        resetChanges,
        commitChanges: commitStateChanges,
    } = useMetadataState(mode === "offline" ? initialData : []);

    const { validateRow, validateAll, canCommitChanges } = useMetadataValidation();

    // Filter rows based on search and filters
    const filteredRows = useMemo(() => {
        let filtered = rows;

        // Apply search filter
        if (searchTerm) {
            const lowerSearch = searchTerm.toLowerCase();
            filtered = filtered.filter(
                (row) =>
                    row.metadataKey.toLowerCase().includes(lowerSearch) ||
                    row.metadataValue.toLowerCase().includes(lowerSearch)
            );
        }

        // Apply type filter
        if (typeFilter) {
            filtered = filtered.filter((row) => row.metadataValueType === typeFilter);
        }

        // Apply schema only filter
        if (schemaOnlyFilter) {
            filtered = filtered.filter((row) => row.metadataSchemaField === true);
        }

        return filtered;
    }, [rows, searchTerm, typeFilter, schemaOnlyFilter]);

    // Initialize rows when data is fetched (use ref to prevent re-initialization)
    const hasInitializedData = useRef(false);
    const lastDataRef = useRef<string>("");
    const lastInitialDataRef = useRef<string>("");
    const forceRefreshFlag = useRef(false);
    const hasInitializedSchemas = useRef(false);

    // In offline mode, if schemas are loaded and we have no initial data, pre-populate from schemas
    useEffect(() => {
        if (
            mode === "offline" &&
            schemas &&
            schemas.fields.length > 0 &&
            !hasInitializedSchemas.current
        ) {
            console.log("[MetadataContainer] Offline mode - initializing from schemas:", schemas);

            // Check if we already have initial data
            if (initialData.length === 0) {
                // Create metadata records from schema fields with default values
                // Use the first schema name as the schema name for all fields
                const schemaName =
                    schemas.schemaNames.length > 0 ? schemas.schemaNames[0] : "Schema";

                const schemaRecords: MetadataRecord[] = schemas.fields.map((field) => ({
                    metadataKey: field.metadataFieldKeyName,
                    metadataValue: field.defaultMetadataFieldValue || "",
                    metadataValueType: field.metadataFieldValueType,
                    metadataSchemaName: schemaName,
                    metadataSchemaField: true,
                    metadataSchemaRequired: field.required,
                    metadataSchemaSequence: field.sequence,
                    metadataSchemaDefaultValue: field.defaultMetadataFieldValue,
                    metadataSchemaDependsOn: field.dependsOnFieldKeyName
                        ? Array.isArray(field.dependsOnFieldKeyName)
                            ? field.dependsOnFieldKeyName
                            : [field.dependsOnFieldKeyName]
                        : undefined,
                    metadataSchemaControlledListKeys: field.controlledListKeys,
                    metadataSchemaMultiFieldConflict: field.hasConflict,
                }));

                console.log(
                    "[MetadataContainer] Initializing rows from schema records:",
                    schemaRecords
                );
                initializeRows(schemaRecords);
                hasInitializedSchemas.current = true;
            }
        }
    }, [mode, schemas, initialData, initializeRows]);

    useEffect(() => {
        console.log(
            "[MetadataContainer] useEffect called - mode:",
            mode,
            "hasInitializedData:",
            hasInitializedData.current
        );

        // For offline mode, DON'T use initialData in dependencies
        // The useMetadataState hook handles initialization from initialData
        if (mode === "offline") {
            console.log(
                "[MetadataContainer] Offline mode - skipping (useMetadataState handles initialization)"
            );
            return;
        }

        // For online mode, only initialize when data actually changes OR when forced
        const dataStr = JSON.stringify(data);
        const dataChanged = dataStr !== lastDataRef.current;
        console.log(
            "[MetadataContainer] Online mode check - dataChanged:",
            dataChanged,
            "forceRefresh:",
            forceRefreshFlag.current
        );

        if (dataChanged || forceRefreshFlag.current) {
            console.log(
                "[MetadataContainer] Data changed or forced refresh, initializing rows:",
                data
            );
            initializeRows(data);
            lastDataRef.current = dataStr;
            forceRefreshFlag.current = false;

            // Check if data has schema fields to determine if restriction should apply
            const hasSchemaFields = data.some((record) => record.metadataSchemaField === true);
            setHasSchemas(hasSchemaFields);
        } else {
            console.log("[MetadataContainer] Online mode - skipping initialization (no changes)");
        }
    }, [data, mode]);

    // Update restriction state when API returns the flag or prop changes
    useEffect(() => {
        if (mode === "online" && apiRestriction !== undefined) {
            setRestrictMetadata(apiRestriction);
            // Check if we have schema fields in the data
            const hasSchemaFields = data.some((record) => record.metadataSchemaField === true);
            setHasSchemas(hasSchemaFields);
        } else if (mode === "offline") {
            setRestrictMetadata(restrictMetadataOutsideSchemas);
            // Check if we have schemas loaded
            setHasSchemas(schemas !== null && schemas.fields.length > 0);
        }
    }, [apiRestriction, restrictMetadataOutsideSchemas, mode, data, schemas]);

    // Handle manual refresh
    const handleRefresh = useCallback(async () => {
        if (mode === "offline") return;

        setRefreshing(true);
        setError(null);
        setDismissedFetchError(false); // Reset dismissed state on refresh
        try {
            await refetch();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to refresh metadata");
        } finally {
            setRefreshing(false);
        }
    }, [mode, refetch]);

    // Handle cancel/revert changes for a single row
    const handleCancelEdit = useCallback(
        (index: number) => {
            const row = rows[index];

            if (row.isNew) {
                // Remove new unsaved rows completely
                deleteRow(index);
            } else {
                // Reset to original values
                updateRow(index, {
                    editKey: row.metadataKey,
                    editValue: row.metadataValue,
                    editType: row.metadataValueType,
                    hasChanges: false,
                    validationError: undefined,
                });
            }
        },
        [rows, updateRow, deleteRow]
    );

    // Handle cancel all changes
    const handleCancelAllChanges = useCallback(() => {
        resetChanges();
    }, [resetChanges]);

    // Handle key change - only update editKey, metadataKey updates on commit
    const handleKeyChange = useCallback(
        (index: number, key: string) => {
            console.log("[MetadataContainer] handleKeyChange called - index:", index, "key:", key);
            updateRow(index, {
                editKey: key,
                hasChanges: true,
            });
        },
        [updateRow]
    );

    // Handle type change - only update editType, metadataValueType updates on commit
    const handleTypeChange = useCallback(
        (index: number, type: MetadataValueType) => {
            console.log(
                "[MetadataContainer] handleTypeChange called - index:",
                index,
                "type:",
                type
            );

            const row = rows[index];
            const currentValue = row.editValue;

            // Try to preserve the value if it validates against the new type
            let newValue = "";
            if (currentValue && currentValue.trim() !== "") {
                // Import validation helper
                const { validateMetadataValue } = require("./utils/validationHelpers");
                const validation = validateMetadataValue(currentValue, type);

                if (validation.isValid) {
                    // Value validates against new type, keep it
                    newValue = currentValue;
                    console.log(
                        "[MetadataContainer] Value validates against new type, preserving:",
                        currentValue
                    );
                } else {
                    console.log(
                        "[MetadataContainer] Value does not validate against new type, clearing"
                    );
                }
            }

            updateRow(index, {
                editType: type,
                editValue: newValue,
                hasChanges: true,
            });
        },
        [updateRow, rows]
    );

    // Handle value change - only update editValue, metadataValue updates on commit
    const handleValueChange = useCallback(
        (index: number, value: string) => {
            console.log(
                "[MetadataContainer] handleValueChange called - index:",
                index,
                "value:",
                value
            );
            const row = rows[index];

            // Check if value actually changed from original
            // For new rows, always mark as changed if value is set
            // For existing rows, only mark as changed if different from original
            const actuallyChanged = row.isNew ? value !== "" : value !== row.metadataValue;

            updateRow(index, {
                editValue: value,
                hasChanges: actuallyChanged,
            });
        },
        [updateRow, rows]
    );

    // Handle commit changes
    const handleCommitChanges = useCallback(async () => {
        // Validate all rows
        const validation = validateAll(rows);
        if (!validation.isValid) {
            setError(`Validation failed: ${validation.errors.join("; ")}`);
            return;
        }

        if (mode === "offline") {
            // In offline mode, apply edit values to actual values
            commitStateChanges(); // This applies editKey/editValue to metadataKey/metadataValue

            // Get the committed records to send to parent
            const activeRows = rows.filter((r) => !r.isDeleted);
            const committed = activeRows.map((row) => ({
                metadataKey: row.editKey,
                metadataValue: row.editValue,
                metadataValueType: row.editType,
            }));

            // Only notify parent if callback is provided
            // This prevents unnecessary re-renders in the parent component
            if (onDataChange) {
                // Use setTimeout to defer the callback and prevent infinite loops
                setTimeout(() => {
                    onDataChange(committed);
                }, 0);
            }
            return;
        }

        // Online mode - make API calls
        setError(null);
        setRefreshing(true);

        try {
            const results: any[] = [];

            // Process creates
            if (changes.added.length > 0) {
                const result = await createMetadata(
                    entityType,
                    entityId,
                    changes.added,
                    databaseId,
                    filePath,
                    entityType === "file" ? activeFileType || "attribute" : fileType
                );
                results.push(result);
            }

            // Process updates
            if (changes.updated.length > 0) {
                const result = await updateMetadata(
                    entityType,
                    entityId,
                    changes.updated,
                    "update",
                    databaseId,
                    filePath,
                    entityType === "file" ? activeFileType || "attribute" : fileType
                );
                results.push(result);
            }

            // Process deletes
            if (changes.deleted.length > 0) {
                const result = await deleteMetadata(
                    entityType,
                    entityId,
                    changes.deleted,
                    databaseId,
                    filePath,
                    entityType === "file" ? activeFileType || "attribute" : fileType
                );
                results.push(result);
            }

            // Check for failures in responses (success: false with failedItems)
            const failures: string[] = [];
            results.forEach((result) => {
                if (result && result.success === false) {
                    if (result.failedItems && Array.isArray(result.failedItems)) {
                        result.failedItems.forEach((item: any) => {
                            failures.push(`${item.key}: ${item.error}`);
                        });
                    }
                    if (result.message) {
                        failures.push(result.message);
                    }
                }
            });

            if (failures.length > 0) {
                setError(`Failed to commit some changes:\n${failures.join("\n")}`);
                // Force refresh to get the actual state from backend
                forceRefreshFlag.current = true;
                await refetch();
                setRefreshing(false);
                return;
            }

            // Force a complete refresh from the backend to get the actual state
            // This ensures we see any backend modifications (defaults, validation changes, etc.)
            forceRefreshFlag.current = true;
            await refetch();

            // Don't call resetChanges() here - let the useEffect handle re-initialization
            // The forceRefreshFlag will trigger a fresh initialization with the new data
        } catch (err) {
            console.error("[MetadataContainer] Error committing changes:", err);
            setError(err instanceof Error ? err.message : "Failed to commit changes");
            // Force refresh even on error to get actual backend state
            forceRefreshFlag.current = true;
            await refetch();
        } finally {
            setRefreshing(false);
        }
    }, [
        rows,
        changes,
        mode,
        entityType,
        entityId,
        databaseId,
        filePath,
        fileType,
        activeFileType,
        validateAll,
        commitStateChanges,
        refetch,
        onDataChange,
    ]);

    // Handle bulk edit save
    const handleBulkEditSave = useCallback(
        async (updatedRows) => {
            // In bulk edit, we use REPLACE_ALL
            const metadataRecords = convertToMetadataRecords(updatedRows);

            if (mode === "offline") {
                if (onDataChange) {
                    onDataChange(metadataRecords);
                }
                initializeRows(metadataRecords);
                setEditMode("normal");
                return;
            }

            // Online mode - use REPLACE_ALL
            setError(null);
            setRefreshing(true);

            try {
                await updateMetadata(
                    entityType,
                    entityId,
                    metadataRecords,
                    "replace_all",
                    databaseId,
                    filePath,
                    entityType === "file" ? activeFileType || "attribute" : fileType
                );

                // Refresh data
                await refetch();
                setEditMode("normal");
            } catch (err) {
                console.error("[MetadataContainer] Error in bulk edit:", err);
                setError(err instanceof Error ? err.message : "Failed to save bulk changes");
            } finally {
                setRefreshing(false);
            }
        },
        [
            mode,
            entityType,
            entityId,
            databaseId,
            filePath,
            fileType,
            activeFileType,
            refetch,
            onDataChange,
            initializeRows,
        ]
    );

    // Render for file entity with tabs
    if (entityType === "file") {
        // Use the fetched counts from the hook
        const displayAttributeCount = attributeCount !== undefined ? attributeCount : "...";
        const displayMetadataCount = metadataCount !== undefined ? metadataCount : "...";

        return (
            <Container>
                <SpaceBetween direction="vertical" size="m">
                    {(error || (fetchError && !dismissedFetchError)) && (
                        <Alert
                            type="error"
                            dismissible
                            onDismiss={() => {
                                setError(null);
                                setDismissedFetchError(true);
                            }}
                            header="Metadata Error"
                        >
                            {error || fetchError}
                        </Alert>
                    )}

                    {schemasError && mode === "offline" && (
                        <Alert type="warning" header="Schema Loading Warning">
                            Unable to load metadata schemas: {schemasError}. You can still add
                            metadata manually.
                        </Alert>
                    )}

                    <Tabs
                        activeTabId={activeFileType || "attribute"}
                        onChange={({ detail }) =>
                            setActiveFileType(detail.activeTabId as FileMetadataType)
                        }
                        ariaLabel="File metadata and attributes"
                        tabs={[
                            {
                                id: "attribute",
                                label: `File Attributes (${displayAttributeCount})`,
                                content:
                                    editMode === "bulk" ? (
                                        <BulkEditMode
                                            rows={rows}
                                            mode={mode}
                                            restrictMetadata={restrictMetadata && hasSchemas}
                                            onSave={handleBulkEditSave}
                                            onCancel={() => {
                                                resetChanges();
                                                setEditMode("normal");
                                            }}
                                        />
                                    ) : (
                                        <MetadataTable
                                            rows={rows}
                                            loading={loading}
                                            editMode={editMode}
                                            entityType={entityType}
                                            mode={mode}
                                            onCancelEdit={handleCancelEdit}
                                            onDeleteRow={deleteRow}
                                            onAddNew={addNewRow}
                                            onKeyChange={handleKeyChange}
                                            onTypeChange={handleTypeChange}
                                            onValueChange={handleValueChange}
                                            onToggleEditMode={() => setEditMode("bulk")}
                                            onCommitChanges={handleCommitChanges}
                                            onCancelAllChanges={handleCancelAllChanges}
                                            onRefresh={handleRefresh}
                                            hasChanges={hasChanges}
                                            canCommit={canCommitChanges(rows)}
                                            readOnly={readOnly}
                                            isFileAttribute={true}
                                            refreshing={refreshing}
                                            restrictMetadata={restrictMetadata && hasSchemas}
                                        />
                                    ),
                            },
                            {
                                id: "metadata",
                                label: `File Metadata (${displayMetadataCount})`,
                                content:
                                    editMode === "bulk" ? (
                                        <BulkEditMode
                                            rows={rows}
                                            mode={mode}
                                            restrictMetadata={restrictMetadata && hasSchemas}
                                            onSave={handleBulkEditSave}
                                            onCancel={() => {
                                                resetChanges();
                                                setEditMode("normal");
                                            }}
                                        />
                                    ) : (
                                        <MetadataTable
                                            rows={rows}
                                            loading={loading}
                                            editMode={editMode}
                                            entityType={entityType}
                                            mode={mode}
                                            onCancelEdit={handleCancelEdit}
                                            onDeleteRow={deleteRow}
                                            onAddNew={addNewRow}
                                            onKeyChange={handleKeyChange}
                                            onTypeChange={handleTypeChange}
                                            onValueChange={handleValueChange}
                                            onToggleEditMode={() => setEditMode("bulk")}
                                            onCommitChanges={handleCommitChanges}
                                            onCancelAllChanges={handleCancelAllChanges}
                                            onRefresh={handleRefresh}
                                            hasChanges={hasChanges}
                                            canCommit={canCommitChanges(rows)}
                                            readOnly={readOnly}
                                            isFileAttribute={false}
                                            refreshing={refreshing}
                                            restrictMetadata={restrictMetadata && hasSchemas}
                                        />
                                    ),
                            },
                        ]}
                    />
                </SpaceBetween>
            </Container>
        );
    }

    // Render for other entity types (no tabs)
    return (
        <Container>
            <SpaceBetween direction="vertical" size="m">
                {(error || (fetchError && !dismissedFetchError)) && (
                    <Alert
                        type="error"
                        dismissible
                        onDismiss={() => {
                            setError(null);
                            setDismissedFetchError(true);
                        }}
                        header="Metadata Error"
                    >
                        {error || fetchError}
                    </Alert>
                )}

                {schemasError && mode === "offline" && (
                    <Alert type="warning" header="Schema Loading Warning">
                        Unable to load metadata schemas: {schemasError}. You can still add metadata
                        manually.
                    </Alert>
                )}

                {editMode === "bulk" ? (
                    <BulkEditMode
                        rows={rows}
                        mode={mode}
                        restrictMetadata={restrictMetadata && hasSchemas}
                        onSave={handleBulkEditSave}
                        onCancel={() => {
                            resetChanges();
                            setEditMode("normal");
                        }}
                    />
                ) : (
                    <MetadataTable
                        rows={rows}
                        loading={loading}
                        editMode={editMode}
                        entityType={entityType}
                        mode={mode}
                        onCancelEdit={handleCancelEdit}
                        onDeleteRow={deleteRow}
                        onAddNew={addNewRow}
                        onKeyChange={handleKeyChange}
                        onTypeChange={handleTypeChange}
                        onValueChange={handleValueChange}
                        onToggleEditMode={() => setEditMode("bulk")}
                        onCommitChanges={handleCommitChanges}
                        onCancelAllChanges={handleCancelAllChanges}
                        onRefresh={handleRefresh}
                        hasChanges={hasChanges}
                        canCommit={canCommitChanges(rows)}
                        readOnly={readOnly}
                        isFileAttribute={false}
                        refreshing={refreshing}
                        restrictMetadata={restrictMetadata && hasSchemas}
                    />
                )}
            </SpaceBetween>
        </Container>
    );
};

export default MetadataContainer;
