/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useRef } from "react";
import {
    Table,
    Input,
    Button,
    SpaceBetween,
    Header,
    Box,
    Alert,
    Modal,
    Icon,
    FileUpload,
    Select,
    Textarea,
} from "@cloudscape-design/components";
import { MetadataRowState, MetadataValueType } from "./types/metadata.types";
import { isSchemaField, getValueTypeLabel } from "./utils/metadataHelpers";
import { validateAllRows, validateMetadataValue } from "./utils/validationHelpers";
import MetadataSchemaTooltip from "./MetadataSchemaTooltip";
import { Popover } from "@cloudscape-design/components";
import {
    exportToCSV,
    downloadCSV,
    importFromCSV,
    readFileAsText,
    validateCSVFile,
} from "./utils/csvHelpers";

interface BulkEditModeProps {
    rows: MetadataRowState[];
    mode?: "online" | "offline";
    restrictMetadata?: boolean;
    onSave: (rows: MetadataRowState[]) => void;
    onCancel: () => void;
}

// Available value types for dropdown
const VALUE_TYPE_OPTIONS = [
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
    { label: "Inline Controlled List", value: "inline_controlled_list" },
];

export const BulkEditMode: React.FC<BulkEditModeProps> = ({
    rows,
    mode = "online",
    restrictMetadata = false,
    onSave,
    onCancel,
}) => {
    const [editedRows, setEditedRows] = useState<MetadataRowState[]>([]);
    const [showSaveConfirm, setShowSaveConfirm] = useState(false);
    const [validationError, setValidationError] = useState<string | null>(null);
    const [showImportModal, setShowImportModal] = useState(false);
    const [importFile, setImportFile] = useState<File[]>([]);
    const [importErrors, setImportErrors] = useState<string[]>([]);
    const [rowValidationErrors, setRowValidationErrors] = useState<Map<number, string>>(new Map());
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Collect all validation errors for Save All button tooltip
    const allValidationErrors = React.useMemo(() => {
        const errors: string[] = [];

        editedRows.forEach((row, idx) => {
            // Check required fields
            if (row.metadataSchemaRequired && (!row.editValue || row.editValue.trim() === "")) {
                errors.push(`${row.metadataKey || row.editKey}: Required field is empty`);
            }

            // Check row validation errors
            if (rowValidationErrors.has(idx)) {
                errors.push(`${row.metadataKey || row.editKey}: ${rowValidationErrors.get(idx)}`);
            }

            // Check for duplicate keys
            const duplicates = editedRows.filter((r) => r.editKey === row.editKey);
            if (duplicates.length > 1 && row.editKey) {
                errors.push(`${row.editKey}: Duplicate key`);
            }

            // Check for empty keys
            if (!row.editKey || row.editKey.trim() === "") {
                errors.push(`Row ${idx + 1}: Key is required`);
            }
        });

        return errors;
    }, [editedRows, rowValidationErrors]);

    const canSave = allValidationErrors.length === 0;

    // Initialize edited rows
    useEffect(() => {
        // Convert all values to raw string format for bulk editing
        const bulkRows = rows
            .filter((r) => !r.isDeleted)
            .map((row) => ({
                ...row,
                editKey: row.metadataKey,
                editValue: row.metadataValue,
                editType: row.metadataValueType,
            }));
        setEditedRows(bulkRows);
    }, [rows]);

    // Validate a single row's value against its type
    const validateRowValue = (row: MetadataRowState): string | null => {
        if (!row.editValue || row.editValue.trim() === "") {
            return null; // Empty values are allowed (required check is separate)
        }

        // Validate type format
        const validation = validateMetadataValue(row.editValue, row.editType);
        if (!validation.isValid) {
            return validation.errors.join("; ");
        }

        // Additional validation for inline controlled lists
        if (
            row.editType === "inline_controlled_list" &&
            row.metadataSchemaControlledListKeys &&
            row.metadataSchemaControlledListKeys.length > 0
        ) {
            if (!row.metadataSchemaControlledListKeys.includes(row.editValue)) {
                return `Value must be one of: ${row.metadataSchemaControlledListKeys.join(", ")}`;
            }
        }

        return null;
    };

    // Handle cell edit
    const handleCellEdit = (index: number, field: "key" | "value" | "type", value: string) => {
        setEditedRows((prev) => {
            const newRows = prev.map((row, i) => {
                if (i !== index) return row;

                const updated = { ...row };

                if (field === "key") {
                    updated.editKey = value;
                    updated.metadataKey = value;
                } else if (field === "value") {
                    updated.editValue = value;
                    updated.metadataValue = value;

                    // Validate the new value
                    const error = validateRowValue(updated);
                    setRowValidationErrors((prev) => {
                        const newErrors = new Map(prev);
                        if (error) {
                            newErrors.set(index, error);
                        } else {
                            newErrors.delete(index);
                        }
                        return newErrors;
                    });
                } else if (field === "type") {
                    // When changing type, try to preserve the value if it validates
                    const newType = value as MetadataValueType;
                    const currentValue = row.editValue;

                    // Try to validate current value against new type
                    if (currentValue && currentValue.trim() !== "") {
                        const validation = validateMetadataValue(currentValue, newType);
                        if (validation.isValid) {
                            // Value validates against new type, keep it
                            updated.editValue = currentValue;
                            updated.metadataValue = currentValue;
                        } else {
                            // Value doesn't validate against new type, clear it
                            updated.editValue = "";
                            updated.metadataValue = "";
                        }
                    }

                    updated.editType = newType;
                    updated.metadataValueType = newType;

                    // Validate after type change
                    const error = validateRowValue(updated);
                    setRowValidationErrors((prev) => {
                        const newErrors = new Map(prev);
                        if (error) {
                            newErrors.set(index, error);
                        } else {
                            newErrors.delete(index);
                        }
                        return newErrors;
                    });
                }

                updated.hasChanges = true;
                return updated;
            });

            return newRows;
        });
    };

    // Handle add new row
    const handleAddRow = () => {
        const newRow: MetadataRowState = {
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
        setEditedRows((prev) => [...prev, newRow]);
    };

    // Handle row delete
    const handleDeleteRow = (index: number) => {
        const row = editedRows[index];

        // Schema fields can only be cleared, not deleted
        if (isSchemaField(row)) {
            setEditedRows((prev) =>
                prev.map((r, i) =>
                    i === index ? { ...r, metadataValue: "", editValue: "", hasChanges: true } : r
                )
            );
        } else {
            setEditedRows((prev) => prev.filter((_, i) => i !== index));
        }
    };

    // Handle save
    const handleSave = () => {
        // Validate all rows
        const validation = validateAllRows(editedRows);
        if (!validation.isValid) {
            setValidationError(validation.errors.join("\n"));
            return;
        }

        setValidationError(null);
        setShowSaveConfirm(true);
    };

    const confirmSave = () => {
        onSave(editedRows);
        setShowSaveConfirm(false);
    };

    // Handle CSV export
    const handleExportCSV = () => {
        const csvContent = exportToCSV(editedRows);
        const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, -5);
        downloadCSV(csvContent, `metadata-export-${timestamp}.csv`);
    };

    // Handle CSV import
    const handleImportCSV = async () => {
        if (importFile.length === 0) return;

        const file = importFile[0];
        const validation = validateCSVFile(file);

        if (!validation.valid) {
            setImportErrors([validation.error || "Invalid file"]);
            return;
        }

        try {
            const content = await readFileAsText(file);
            const { rows: importedRows, errors } = importFromCSV(
                content,
                editedRows, // Pass existing rows for schema validation
                restrictMetadata // Pass restriction flag
            );

            if (errors.length > 0) {
                setImportErrors(errors);
                return;
            }

            // Merge imported rows with existing rows
            // Schema fields from existing rows are preserved
            const schemaRows = editedRows.filter((row) => isSchemaField(row));
            const nonSchemaImported = importedRows.filter(
                (row) => !schemaRows.some((sr) => sr.metadataKey === row.metadataKey)
            );

            // Combine: schema rows first, then imported rows
            const mergedRows = [
                ...schemaRows,
                ...(nonSchemaImported.map((row) => ({
                    ...row,
                    isNew: true,
                    hasChanges: true,
                    isEditing: false,
                    isDeleted: false,
                })) as MetadataRowState[]),
            ];

            // Validate all imported rows
            const newValidationErrors = new Map<number, string>();
            mergedRows.forEach((row, index) => {
                const error = validateRowValue(row);
                if (error) {
                    newValidationErrors.set(index, error);
                }
            });

            setEditedRows(mergedRows);
            setRowValidationErrors(newValidationErrors);
            setShowImportModal(false);
            setImportFile([]);
            setImportErrors([]);
        } catch (error) {
            setImportErrors([error instanceof Error ? error.message : "Failed to import CSV"]);
        }
    };

    const columnDefinitions = [
        {
            id: "key",
            header: "Metadata Key",
            cell: (item: MetadataRowState) => {
                const index = editedRows.indexOf(item);
                const isSchema = isSchemaField(item);

                return (
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        {isSchema ? (
                            <strong>{item.metadataKey}</strong>
                        ) : (
                            <Input
                                value={item.editKey}
                                onChange={({ detail }) =>
                                    handleCellEdit(index, "key", detail.value)
                                }
                                placeholder="Enter key"
                                ariaLabel={`Metadata key row ${index + 1}`}
                            />
                        )}

                        {/* Schema tooltip */}
                        {isSchema && (
                            <MetadataSchemaTooltip
                                schemaName={item.metadataSchemaName}
                                required={item.metadataSchemaRequired}
                                dependsOn={item.metadataSchemaDependsOn}
                                controlledListKeys={item.metadataSchemaControlledListKeys}
                                multiFieldConflict={item.metadataSchemaMultiFieldConflict}
                                defaultValue={item.metadataSchemaDefaultValue}
                                sequence={item.metadataSchemaSequence}
                            />
                        )}
                    </div>
                );
            },
        },
        {
            id: "type",
            header: "Value Type",
            cell: (item: MetadataRowState) => {
                const index = editedRows.indexOf(item);
                const isSchema = isSchemaField(item);

                if (isSchema) {
                    return <strong>{getValueTypeLabel(item.metadataValueType)}</strong>;
                }

                const selectedOption = VALUE_TYPE_OPTIONS.find(
                    (opt) => opt.value === item.editType
                );

                return (
                    <Select
                        selectedOption={selectedOption || null}
                        onChange={({ detail }) =>
                            handleCellEdit(index, "type", detail.selectedOption.value || "string")
                        }
                        options={VALUE_TYPE_OPTIONS}
                        placeholder="Select type"
                        ariaLabel={`Value type row ${index + 1}`}
                        expandToViewport={true}
                    />
                );
            },
        },
        {
            id: "value",
            header: "Raw Value (String)",
            cell: (item: MetadataRowState) => {
                const index = editedRows.indexOf(item);
                const hasError = rowValidationErrors.has(index);
                const isRequiredAndEmpty =
                    item.metadataSchemaRequired &&
                    (!item.editValue || item.editValue.trim() === "");

                return (
                    <div>
                        <Textarea
                            value={item.editValue}
                            onChange={({ detail }) => handleCellEdit(index, "value", detail.value)}
                            placeholder="Enter value"
                            ariaLabel={`Value row ${index + 1}`}
                            rows={3}
                            invalid={hasError || isRequiredAndEmpty}
                        />
                        {hasError && (
                            <Box
                                color="text-status-error"
                                fontSize="body-s"
                                margin={{ top: "xxs" }}
                            >
                                {rowValidationErrors.get(index)}
                            </Box>
                        )}
                        {isRequiredAndEmpty && !hasError && (
                            <Box
                                color="text-status-error"
                                fontSize="body-s"
                                margin={{ top: "xxs" }}
                            >
                                Required field is empty
                            </Box>
                        )}
                    </div>
                );
            },
        },
        {
            id: "actions",
            header: "Actions",
            cell: (item: MetadataRowState) => {
                const index = editedRows.indexOf(item);
                const isSchema = isSchemaField(item);
                const canDelete = !isSchema || !item.metadataSchemaRequired;

                return (
                    <div style={{ display: "inline-block" }} tabIndex={-1}>
                        <Button
                            variant="icon"
                            iconName={isSchema ? "undo" : "remove"}
                            onClick={() => handleDeleteRow(index)}
                            disabled={!canDelete}
                            ariaLabel={isSchema ? "Clear value" : "Delete row"}
                        />
                    </div>
                );
            },
        },
    ];

    return (
        <>
            <SpaceBetween direction="vertical" size="m">
                {mode === "online" && (
                    <Alert type="warning" header="Bulk Edit Mode">
                        <SpaceBetween direction="vertical" size="xs">
                            <div>
                                You are in bulk edit mode. All values are shown as raw strings for
                                quick editing.
                            </div>
                            <div>
                                <strong>Important:</strong> Saving will use REPLACE_ALL mode, which
                                replaces all metadata on the backend. Make sure all required fields
                                are filled.
                            </div>
                        </SpaceBetween>
                    </Alert>
                )}
                {mode === "offline" && (
                    <Alert type="info" header="Bulk Edit Mode">
                        You are in bulk edit mode. All values are shown as raw strings for quick
                        editing. Click "Back" when finished to return to normal mode.
                    </Alert>
                )}

                {validationError && (
                    <Alert type="error" dismissible onDismiss={() => setValidationError(null)}>
                        <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{validationError}</pre>
                    </Alert>
                )}

                <Table
                    columnDefinitions={columnDefinitions}
                    items={editedRows}
                    header={
                        <Header
                            variant="h3"
                            counter={`(${editedRows.length})`}
                            actions={
                                <SpaceBetween direction="horizontal" size="xs">
                                    {/* Import CSV */}
                                    <Button
                                        iconName="upload"
                                        onClick={() => setShowImportModal(true)}
                                        ariaLabel="Import from CSV"
                                    >
                                        Import CSV
                                    </Button>

                                    {/* Export CSV */}
                                    <Button
                                        iconName="download"
                                        onClick={handleExportCSV}
                                        ariaLabel="Export to CSV"
                                    >
                                        Export CSV
                                    </Button>

                                    {mode === "offline" ? (
                                        <>
                                            {/* Add Row */}
                                            <Button
                                                iconName="add-plus"
                                                onClick={handleAddRow}
                                                disabled={restrictMetadata}
                                                ariaLabel="Add new row"
                                            >
                                                Add Row
                                            </Button>

                                            {/* Back */}
                                            <Button
                                                variant="primary"
                                                onClick={() => {
                                                    onSave(editedRows);
                                                }}
                                                ariaLabel="Back to normal mode"
                                            >
                                                Back
                                            </Button>
                                        </>
                                    ) : (
                                        <>
                                            {/* Cancel */}
                                            <Button
                                                variant="link"
                                                onClick={onCancel}
                                                ariaLabel="Cancel bulk edit"
                                            >
                                                Cancel
                                            </Button>

                                            {/* Add Row */}
                                            <Button
                                                iconName="add-plus"
                                                onClick={handleAddRow}
                                                disabled={restrictMetadata}
                                                ariaLabel="Add new row"
                                            >
                                                Add Row
                                            </Button>

                                            {/* Save All */}
                                            {!canSave && allValidationErrors.length > 0 ? (
                                                <Popover
                                                    dismissButton={false}
                                                    position="top"
                                                    size="large"
                                                    triggerType="custom"
                                                    content={
                                                        <SpaceBetween
                                                            direction="vertical"
                                                            size="xs"
                                                        >
                                                            <Box variant="h4">
                                                                Validation Errors
                                                            </Box>
                                                            <Box variant="p">
                                                                The following errors must be
                                                                corrected before saving:
                                                            </Box>
                                                            <ul
                                                                style={{
                                                                    margin: "4px 0",
                                                                    paddingLeft: "20px",
                                                                    maxHeight: "200px",
                                                                    overflowY: "auto",
                                                                }}
                                                            >
                                                                {allValidationErrors.map(
                                                                    (error, idx) => (
                                                                        <li key={idx}>{error}</li>
                                                                    )
                                                                )}
                                                            </ul>
                                                        </SpaceBetween>
                                                    }
                                                >
                                                    <Button
                                                        variant="primary"
                                                        onClick={handleSave}
                                                        disabled={true}
                                                        ariaLabel="Save all changes"
                                                    >
                                                        <Icon name="upload" /> Save All
                                                    </Button>
                                                </Popover>
                                            ) : (
                                                <Button
                                                    variant="primary"
                                                    onClick={handleSave}
                                                    disabled={!canSave}
                                                    ariaLabel="Save all changes"
                                                >
                                                    <Icon name="upload" /> Save All
                                                </Button>
                                            )}
                                        </>
                                    )}
                                </SpaceBetween>
                            }
                        >
                            Bulk Edit Mode
                        </Header>
                    }
                    empty={
                        <Box textAlign="center" color="inherit">
                            <SpaceBetween size="m">
                                <b>No metadata to edit</b>
                                {!restrictMetadata && (
                                    <Button
                                        iconName="add-plus"
                                        variant="primary"
                                        onClick={handleAddRow}
                                        ariaLabel="Add new row"
                                    >
                                        Add Row
                                    </Button>
                                )}
                                {restrictMetadata && (
                                    <Box variant="p" color="text-status-info">
                                        <Icon name="status-info" /> Adding new metadata is
                                        restricted to schema-defined fields only.
                                    </Box>
                                )}
                            </SpaceBetween>
                        </Box>
                    }
                />

                <Box variant="small" color="text-body-secondary">
                    <strong>Tips:</strong>
                    <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
                        <li>Use Tab key to navigate between cells</li>
                        <li>Schema fields (bold) cannot have their key or type changed</li>
                        <li>All values are edited as raw strings - ensure proper formatting</li>
                        <li>All raw values must validate against their respective value types</li>
                        <li>Click Save All when finished to commit all changes at once</li>
                    </ul>
                </Box>
            </SpaceBetween>

            {/* Save Confirmation Modal - Only for online mode */}
            {mode === "online" && (
                <Modal
                    visible={showSaveConfirm}
                    onDismiss={() => setShowSaveConfirm(false)}
                    header="Confirm Bulk Save"
                    footer={
                        <Box float="right">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button variant="link" onClick={() => setShowSaveConfirm(false)}>
                                    Cancel
                                </Button>
                                <Button variant="primary" onClick={confirmSave}>
                                    Confirm Save
                                </Button>
                            </SpaceBetween>
                        </Box>
                    }
                >
                    <SpaceBetween direction="vertical" size="m">
                        <Alert type="warning" header="Warning: REPLACE_ALL Operation">
                            This will replace ALL metadata on the backend with the current table
                            contents. Any metadata not shown in this table will be permanently
                            deleted.
                        </Alert>

                        <Box variant="p">
                            <strong>Summary:</strong>
                            <ul style={{ margin: "8px 0", paddingLeft: "20px" }}>
                                <li>{editedRows.length} metadata records will be saved</li>
                                <li>All other metadata will be removed</li>
                                <li>This action cannot be undone</li>
                            </ul>
                        </Box>

                        <Box variant="p">Are you sure you want to proceed?</Box>
                    </SpaceBetween>
                </Modal>
            )}

            {/* CSV Import Modal */}
            <Modal
                visible={showImportModal}
                onDismiss={() => {
                    setShowImportModal(false);
                    setImportFile([]);
                    setImportErrors([]);
                }}
                header="Import Metadata from CSV"
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() => {
                                    setShowImportModal(false);
                                    setImportFile([]);
                                    setImportErrors([]);
                                }}
                            >
                                Cancel
                            </Button>
                            <Button
                                variant="primary"
                                onClick={handleImportCSV}
                                disabled={importFile.length === 0}
                            >
                                Import
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <SpaceBetween direction="vertical" size="m">
                    <Alert type="info" header="CSV Import Instructions">
                        <SpaceBetween direction="vertical" size="xs">
                            <div>
                                <strong>Required CSV Format:</strong>
                                <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
                                    <li>
                                        First row must be headers: Metadata Key, Value Type,
                                        Metadata Value
                                    </li>
                                    <li>Each subsequent row represents one metadata record</li>
                                    <li>Schema fields will be preserved (cannot be overwritten)</li>
                                    <li>Maximum file size: 5MB</li>
                                </ul>
                            </div>
                            <div>
                                <strong>Example:</strong>
                                <pre
                                    style={{
                                        background: "#f5f5f5",
                                        padding: "8px",
                                        borderRadius: "4px",
                                        fontSize: "11px",
                                        overflow: "auto",
                                    }}
                                >
                                    {`Metadata Key,Value Type,Metadata Value
Author,string,John Doe
Version,number,1.0
Published,date,2024-01-01T00:00:00.000Z
Active,boolean,true`}
                                </pre>
                            </div>
                        </SpaceBetween>
                    </Alert>

                    {importErrors.length > 0 && (
                        <Alert type="error" dismissible onDismiss={() => setImportErrors([])}>
                            <SpaceBetween direction="vertical" size="xs">
                                <strong>Import Errors:</strong>
                                <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
                                    {importErrors.map((error, index) => (
                                        <li key={index}>{error}</li>
                                    ))}
                                </ul>
                            </SpaceBetween>
                        </Alert>
                    )}

                    <FileUpload
                        onChange={({ detail }) => setImportFile(detail.value)}
                        value={importFile}
                        i18nStrings={{
                            uploadButtonText: (e) => (e ? "Choose file" : "Choose file"),
                            dropzoneText: (e) =>
                                e ? "Drop file to upload" : "Drop file to upload",
                            removeFileAriaLabel: (e) => `Remove file ${e + 1}`,
                            limitShowFewer: "Show fewer files",
                            limitShowMore: "Show more files",
                            errorIconAriaLabel: "Error",
                        }}
                        showFileLastModified
                        showFileSize
                        showFileThumbnail
                        tokenLimit={1}
                        constraintText="CSV file, maximum 5MB"
                        accept=".csv,text/csv"
                    />
                </SpaceBetween>
            </Modal>
        </>
    );
};

export default BulkEditMode;
