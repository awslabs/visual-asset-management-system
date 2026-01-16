/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo, useEffect } from "react";
import {
    Table,
    Box,
    Button,
    SpaceBetween,
    Header,
    Icon,
    Modal,
    Pagination,
    Popover,
} from "@cloudscape-design/components";
import { MetadataRowState, MetadataValueType, EditMode, EntityType } from "./types/metadata.types";
import MetadataRow from "./MetadataRow";
import MetadataSearchFilter from "./MetadataSearchFilter";
import { getChangesCount } from "./utils/metadataHelpers";

interface MetadataTableProps {
    rows: MetadataRowState[];
    loading: boolean;
    editMode: EditMode;
    entityType?: EntityType;
    mode?: "online" | "offline";
    onCancelEdit: (index: number) => void;
    onDeleteRow: (index: number) => void;
    onAddNew: () => void;
    onKeyChange: (index: number, key: string) => void;
    onTypeChange: (index: number, type: MetadataValueType) => void;
    onValueChange: (index: number, value: string) => void;
    onValidationError?: (index: number, error: string | undefined) => void;
    onToggleEditMode: () => void;
    onCommitChanges: () => void;
    onCancelAllChanges: () => void;
    onRefresh: () => void;
    hasChanges: boolean;
    canCommit: boolean;
    readOnly?: boolean;
    isFileAttribute?: boolean;
    refreshing?: boolean;
    restrictMetadata?: boolean;
}

export const MetadataTable: React.FC<MetadataTableProps> = React.memo(
    ({
        rows,
        loading,
        editMode,
        entityType,
        mode = "online",
        onCancelEdit,
        onDeleteRow,
        onAddNew,
        onKeyChange,
        onTypeChange,
        onValueChange,
        onValidationError,
        onToggleEditMode,
        onCommitChanges,
        onCancelAllChanges,
        onRefresh,
        hasChanges,
        canCommit,
        readOnly = false,
        isFileAttribute = false,
        refreshing = false,
        restrictMetadata = false,
    }) => {
        // Search and filter state
        const [searchTerm, setSearchTerm] = useState("");
        const [typeFilter, setTypeFilter] = useState<MetadataValueType | null>(null);
        const [schemaOnlyFilter, setSchemaOnlyFilter] = useState(false);
        const [showCancelConfirm, setShowCancelConfirm] = useState(false);

        // Pagination state
        const [currentPage, setCurrentPage] = useState(1);
        const itemsPerPage = 20;

        // Filter out deleted rows first
        const nonDeletedRows = rows.filter((r) => !r.isDeleted);

        // Apply search and filters
        const filteredRows = nonDeletedRows.filter((row) => {
            // Apply search filter
            if (searchTerm) {
                const lowerSearch = searchTerm.toLowerCase();
                const matchesSearch =
                    row.metadataKey.toLowerCase().includes(lowerSearch) ||
                    row.metadataValue.toLowerCase().includes(lowerSearch);
                if (!matchesSearch) return false;
            }

            // Apply type filter
            if (typeFilter && row.metadataValueType !== typeFilter) {
                return false;
            }

            // Apply schema only filter
            if (schemaOnlyFilter && !row.metadataSchemaField) {
                return false;
            }

            return true;
        });

        const changesCount = getChangesCount(rows);
        const hasActiveFilters = searchTerm !== "" || typeFilter !== null || schemaOnlyFilter;

        // Reset to page 1 when filters change
        useEffect(() => {
            setCurrentPage(1);
        }, [searchTerm, typeFilter, schemaOnlyFilter]);

        // Calculate pagination
        const totalPages = Math.ceil(filteredRows.length / itemsPerPage);
        const startIndex = (currentPage - 1) * itemsPerPage;
        const endIndex = startIndex + itemsPerPage;
        const paginatedRows = filteredRows.slice(startIndex, endIndex);

        // Collect validation errors across all pages
        const validationErrors = useMemo(() => {
            const errors: string[] = [];

            rows.forEach((row, idx) => {
                // Skip deleted rows
                if (row.isDeleted) return;

                // Check required fields
                if (row.metadataSchemaRequired && (!row.editValue || row.editValue.trim() === "")) {
                    errors.push(`${row.metadataKey || row.editKey}: Required field is empty`);
                }

                // Check dependencies
                if (row.metadataSchemaDependsOn && row.metadataSchemaDependsOn.length > 0) {
                    const missingDeps: string[] = [];
                    for (const depField of row.metadataSchemaDependsOn) {
                        const depRow = rows.find((r) => r.metadataKey === depField);
                        if (!depRow || !depRow.editValue || depRow.editValue.trim() === "") {
                            missingDeps.push(depField);
                        }
                    }
                    if (missingDeps.length > 0) {
                        errors.push(
                            `${row.metadataKey || row.editKey}: Depends on ${missingDeps.join(
                                ", "
                            )}`
                        );
                    }
                }

                // Check validation errors
                if (row.validationError) {
                    errors.push(`${row.metadataKey || row.editKey}: ${row.validationError}`);
                }

                // Check for duplicate keys
                const duplicates = rows.filter((r) => !r.isDeleted && r.editKey === row.editKey);
                if (duplicates.length > 1 && row.editKey) {
                    errors.push(`${row.editKey}: Duplicate key`);
                }
            });

            return errors;
        }, [rows]);

        // Format entity type for display
        const getEntityTitle = () => {
            // For file attributes, use "Attribute" terminology
            if (entityType === "file" && isFileAttribute) {
                return "File Attributes";
            }

            if (!entityType) return "Metadata";

            switch (entityType) {
                case "asset":
                    return "Asset Metadata";
                case "assetLink":
                    return "Asset Link Metadata";
                case "file":
                    return "File Metadata";
                case "database":
                    return "Database Metadata";
                default:
                    return "Metadata";
            }
        };

        // Get terminology based on whether it's file attribute or metadata
        const terminology = isFileAttribute ? "Attribute" : "Metadata";

        return (
            <div>
                <Box>
                    <Header
                        variant="h3"
                        counter={
                            hasActiveFilters
                                ? `(${filteredRows.length} of ${nonDeletedRows.length})`
                                : `(${filteredRows.length})`
                        }
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                {/* Refresh button */}
                                <Button
                                    iconName="refresh"
                                    onClick={onRefresh}
                                    disabled={loading || refreshing}
                                    loading={refreshing}
                                    ariaLabel="Refresh metadata"
                                />

                                {/* Bulk edit toggle */}
                                {editMode === "normal" && !readOnly && (
                                    <Button
                                        onClick={onToggleEditMode}
                                        disabled={loading || nonDeletedRows.length === 0}
                                        ariaLabel="Switch to bulk edit mode"
                                    >
                                        Bulk Edit
                                    </Button>
                                )}

                                {/* Add new button */}
                                {editMode === "normal" && !readOnly && (
                                    <Button
                                        iconName="add-plus"
                                        onClick={onAddNew}
                                        disabled={loading || restrictMetadata}
                                        ariaLabel="Add new row"
                                    >
                                        Add Row
                                    </Button>
                                )}

                                {/* Cancel all changes button - show if there are changes */}
                                {hasChanges && editMode === "normal" && changesCount.total > 0 && (
                                    <Button
                                        variant="link"
                                        onClick={() => setShowCancelConfirm(true)}
                                        disabled={loading}
                                        ariaLabel="Cancel all changes"
                                    >
                                        Cancel
                                    </Button>
                                )}

                                {/* Commit changes button - show in both online and offline modes if there are changes */}
                                {hasChanges &&
                                    editMode === "normal" &&
                                    changesCount.total > 0 &&
                                    (!canCommit && validationErrors.length > 0 ? (
                                        <Popover
                                            dismissButton={false}
                                            position="top"
                                            size="large"
                                            triggerType="custom"
                                            content={
                                                <SpaceBetween direction="vertical" size="xs">
                                                    <Box variant="h4">Validation Errors</Box>
                                                    <Box variant="p">
                                                        The following errors must be corrected
                                                        before committing:
                                                    </Box>
                                                    <ul
                                                        style={{
                                                            margin: "4px 0",
                                                            paddingLeft: "20px",
                                                            maxHeight: "200px",
                                                            overflowY: "auto",
                                                        }}
                                                    >
                                                        {validationErrors.map((error, idx) => (
                                                            <li key={idx}>{error}</li>
                                                        ))}
                                                    </ul>
                                                </SpaceBetween>
                                            }
                                        >
                                            <Button
                                                variant="primary"
                                                onClick={onCommitChanges}
                                                disabled={true}
                                                ariaLabel="Commit all changes"
                                            >
                                                <Icon
                                                    name={mode === "online" ? "upload" : "check"}
                                                />{" "}
                                                Commit Changes ({changesCount.total})
                                            </Button>
                                        </Popover>
                                    ) : (
                                        <Button
                                            variant="primary"
                                            onClick={onCommitChanges}
                                            disabled={!canCommit || loading}
                                            ariaLabel="Commit all changes"
                                        >
                                            <Icon name={mode === "online" ? "upload" : "check"} />{" "}
                                            Commit Changes ({changesCount.total})
                                        </Button>
                                    ))}
                            </SpaceBetween>
                        }
                        info={
                            hasChanges && (
                                <Box color="text-status-info">
                                    <Icon name="status-info" /> {changesCount.added} added,{" "}
                                    {changesCount.updated} updated, {changesCount.deleted} deleted
                                </Box>
                            )
                        }
                        description={
                            restrictMetadata ? (
                                <Box color="text-status-warning">
                                    <Icon name="status-warning" /> Adding new{" "}
                                    {terminology.toLowerCase()} fields is restricted. Only
                                    schema-defined fields can be edited.
                                </Box>
                            ) : undefined
                        }
                    >
                        {getEntityTitle()}
                    </Header>

                    {/* Search and Filter - with consistent spacing */}
                    {nonDeletedRows.length > 0 && (
                        <div style={{ marginTop: "8px" }}>
                            <MetadataSearchFilter
                                onSearchChange={setSearchTerm}
                                onTypeFilter={setTypeFilter}
                                onSchemaFilter={setSchemaOnlyFilter}
                                totalRows={nonDeletedRows.length}
                                filteredRows={filteredRows.length}
                            />
                        </div>
                    )}

                    {loading ? (
                        <Box textAlign="center" padding="l">
                            Loading metadata...
                        </Box>
                    ) : filteredRows.length === 0 ? (
                        <Box textAlign="center" color="inherit" padding="l">
                            <SpaceBetween size="m">
                                <b>No {terminology.toLowerCase()}</b>
                                <Box variant="p" color="inherit">
                                    Add {terminology.toLowerCase()} to provide additional
                                    information.
                                </Box>
                                {!readOnly && !restrictMetadata && (
                                    <Button
                                        iconName="add-plus"
                                        variant="primary"
                                        onClick={onAddNew}
                                        disabled={loading}
                                        ariaLabel="Add new row"
                                    >
                                        Add Row
                                    </Button>
                                )}
                                {restrictMetadata && (
                                    <Box variant="p" color="text-status-info">
                                        <Icon name="status-info" /> Adding new{" "}
                                        {terminology.toLowerCase()} is restricted to schema-defined
                                        fields only.
                                    </Box>
                                )}
                            </SpaceBetween>
                        </Box>
                    ) : (
                        <table style={{ width: "100%", borderCollapse: "collapse" }}>
                            <thead>
                                <tr>
                                    <th
                                        style={{
                                            padding: "12px",
                                            textAlign: "left",
                                            borderBottom: "2px solid #e9ebed",
                                        }}
                                    >
                                        {terminology} Key
                                    </th>
                                    <th
                                        style={{
                                            padding: "12px",
                                            textAlign: "left",
                                            borderBottom: "2px solid #e9ebed",
                                        }}
                                    >
                                        Value Type
                                    </th>
                                    <th
                                        style={{
                                            padding: "12px",
                                            textAlign: "left",
                                            borderBottom: "2px solid #e9ebed",
                                        }}
                                    >
                                        {terminology} Value
                                    </th>
                                    <th
                                        style={{
                                            padding: "12px",
                                            textAlign: "left",
                                            borderBottom: "2px solid #e9ebed",
                                        }}
                                    >
                                        Actions
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {paginatedRows.map((row, displayIndex) => {
                                    // Calculate the actual index in the full rows array
                                    const actualIndex = rows.findIndex((r) => r === row);
                                    return (
                                        <MetadataRow
                                            key={`${row.metadataKey}-${actualIndex}`}
                                            row={row}
                                            index={actualIndex}
                                            rows={rows}
                                            onEdit={() => {}} // No-op, rows are always editable
                                            onCancel={() => onCancelEdit(actualIndex)}
                                            onSave={() => {}} // No-op, changes are auto-saved
                                            onDelete={() => onDeleteRow(actualIndex)}
                                            onKeyChange={(key) => onKeyChange(actualIndex, key)}
                                            onTypeChange={(type) => onTypeChange(actualIndex, type)}
                                            onValueChange={(value) =>
                                                onValueChange(actualIndex, value)
                                            }
                                            onValidationError={
                                                onValidationError
                                                    ? (error) =>
                                                          onValidationError(actualIndex, error)
                                                    : undefined
                                            }
                                            readOnly={readOnly}
                                            isFileAttribute={isFileAttribute}
                                        />
                                    );
                                })}
                            </tbody>
                        </table>
                    )}

                    {/* Pagination - show when there are multiple pages */}
                    {!loading && filteredRows.length > itemsPerPage && (
                        <Box margin={{ top: "s" }}>
                            <Pagination
                                currentPageIndex={currentPage}
                                pagesCount={totalPages}
                                onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
                                ariaLabels={{
                                    nextPageLabel: "Next page",
                                    previousPageLabel: "Previous page",
                                    pageLabel: (pageNumber) => `Page ${pageNumber}`,
                                }}
                            />
                        </Box>
                    )}
                </Box>

                {/* Cancel All Changes Confirmation Modal */}
                <Modal
                    visible={showCancelConfirm}
                    onDismiss={() => setShowCancelConfirm(false)}
                    header="Discard All Changes"
                    footer={
                        <Box float="right">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button variant="link" onClick={() => setShowCancelConfirm(false)}>
                                    Keep Editing
                                </Button>
                                <Button
                                    variant="primary"
                                    onClick={() => {
                                        onCancelAllChanges();
                                        setShowCancelConfirm(false);
                                    }}
                                >
                                    Discard Changes
                                </Button>
                            </SpaceBetween>
                        </Box>
                    }
                >
                    <SpaceBetween direction="vertical" size="m">
                        <Box variant="p">
                            Are you sure you want to discard all uncommitted changes?
                        </Box>
                        <Box variant="p">
                            <strong>This will revert:</strong>
                            <ul style={{ margin: "8px 0", paddingLeft: "20px" }}>
                                <li>
                                    {changesCount.added} added {terminology.toLowerCase()} field
                                    {changesCount.added !== 1 ? "s" : ""}
                                </li>
                                <li>
                                    {changesCount.updated} updated {terminology.toLowerCase()} field
                                    {changesCount.updated !== 1 ? "s" : ""}
                                </li>
                                <li>
                                    {changesCount.deleted} deleted {terminology.toLowerCase()} field
                                    {changesCount.deleted !== 1 ? "s" : ""}
                                </li>
                            </ul>
                        </Box>
                        <Box variant="p" color="text-status-warning">
                            This action cannot be undone.
                        </Box>
                    </SpaceBetween>
                </Modal>
            </div>
        );
    }
);

MetadataTable.displayName = "MetadataTable";

export default MetadataTable;
