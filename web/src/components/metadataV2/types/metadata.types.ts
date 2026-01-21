/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * TypeScript interfaces and types for MetadataV2 components
 */

import { AggregatedSchema } from "../hooks/useMetadataSchemas";

// Entity types that can have metadata
export type EntityType = "asset" | "assetLink" | "file" | "database";

// File metadata sub-types
export type FileMetadataType = "metadata" | "attribute";

// Metadata value types (matching backend enum)
export type MetadataValueType =
    | "string"
    | "multiline_string"
    | "inline_controlled_list"
    | "number"
    | "boolean"
    | "date"
    | "xyz"
    | "wxyz"
    | "matrix4x4"
    | "geopoint"
    | "geojson"
    | "lla"
    | "json";

// Update types for bulk operations
export type UpdateType = "update" | "replace_all";

// Component modes
export type MetadataMode = "online" | "offline";

// Edit modes
export type EditMode = "normal" | "bulk";

/**
 * Core metadata record structure
 */
export interface MetadataRecord {
    metadataKey: string;
    metadataValue: string;
    metadataValueType: MetadataValueType;
    // Schema enrichment fields (from backend)
    metadataSchemaName?: string;
    metadataSchemaField?: boolean;
    metadataSchemaRequired?: boolean;
    metadataSchemaSequence?: number;
    metadataSchemaDefaultValue?: string;
    metadataSchemaDependsOn?: string[];
    metadataSchemaMultiFieldConflict?: boolean;
    metadataSchemaControlledListKeys?: string[];
}

/**
 * Extended metadata record with UI state
 */
export interface MetadataRowState extends MetadataRecord {
    // UI state
    isEditing: boolean;
    hasChanges: boolean;
    isNew: boolean;
    isDeleted: boolean;
    // Edit values (separate from display values)
    editKey: string;
    editValue: string;
    editType: MetadataValueType;
    // Original values (for change tracking and history)
    originalValue?: string;
    originalType?: MetadataValueType;
    // Validation state
    validationError?: string;
}

/**
 * Metadata schema definition
 */
export interface MetadataSchema {
    databaseId: string;
    entityType: string;
    schemaName: string;
    fields: MetadataSchemaField[];
}

/**
 * Individual schema field definition
 */
export interface MetadataSchemaField {
    fieldKey: string;
    fieldType: MetadataValueType;
    required: boolean;
    sequence?: number;
    defaultValue?: string;
    dependsOn?: string[];
    inlineControlledList?: string[];
    multiFieldConflict?: boolean;
}

/**
 * Props for MetadataContainer component
 */
export interface MetadataContainerProps {
    // Entity identification
    entityType: EntityType;
    entityId: string; // assetLinkId, assetId, or databaseId
    databaseId?: string; // Required for asset/file
    filePath?: string; // Required for file
    fileType?: FileMetadataType; // Required for file

    // Mode
    mode?: MetadataMode;

    // Offline mode props
    initialData?: MetadataRecord[];
    onDataChange?: (data: MetadataRecord[]) => void;
    onHasChangesChange?: (hasChanges: boolean) => void; // Callback when uncommitted changes state changes
    onValidationChange?: (isValid: boolean) => void; // Callback when validation state changes (e.g., required fields)

    // Optional customization
    readOnly?: boolean;
    showBulkEdit?: boolean;
    restrictMetadataOutsideSchemas?: boolean; // Restrict adding new metadata outside schemas
}

/**
 * Props for MetadataTable component
 */
export interface MetadataTableProps {
    rows: MetadataRowState[];
    loading: boolean;
    editMode: EditMode;
    onEditRow: (index: number) => void;
    onCancelEdit: (index: number) => void;
    onSaveRow: (index: number) => void;
    onDeleteRow: (index: number) => void;
    onAddNew: () => void;
    onKeyChange: (index: number, key: string) => void;
    onTypeChange: (index: number, type: MetadataValueType) => void;
    onValueChange: (index: number, value: string) => void;
    onToggleEditMode: () => void;
    readOnly?: boolean;
    restrictMetadata?: boolean; // Restrict adding new metadata outside schemas
}

/**
 * Props for MetadataRow component
 */
export interface MetadataRowProps {
    row: MetadataRowState;
    index: number;
    editMode: EditMode;
    onEdit: () => void;
    onCancel: () => void;
    onSave: () => void;
    onDelete: () => void;
    onKeyChange: (key: string) => void;
    onTypeChange: (type: MetadataValueType) => void;
    onValueChange: (value: string) => void;
    readOnly?: boolean;
}

/**
 * Props for BulkEditMode component
 */
export interface BulkEditModeProps {
    rows: MetadataRowState[];
    onSave: (rows: MetadataRowState[]) => void;
    onCancel: () => void;
}

/**
 * Props for value type input components
 */
export interface ValueTypeInputProps {
    value: string;
    onChange: (value: string) => void;
    disabled?: boolean;
    ariaLabel?: string;
    error?: string;
}

/**
 * Props for InlineControlledListInput
 */
export interface InlineControlledListInputProps extends ValueTypeInputProps {
    options: string[];
}

/**
 * Props for schema tooltip
 */
export interface MetadataSchemaTooltipProps {
    schemaName?: string;
    required?: boolean;
    dependsOn?: string[];
    controlledListKeys?: string[];
    multiFieldConflict?: boolean;
    defaultValue?: string;
    sequence?: number;
}

/**
 * Props for value history tooltip
 */
export interface ValueHistoryTooltipProps {
    oldValue?: string;
    schemaDefaultValue?: string;
}

/**
 * API response types
 */
export interface MetadataAPIResponse {
    metadata: MetadataRecord[];
    restrictMetadataOutsideSchemas?: boolean;
    NextToken?: string;
    message?: string;
}

export interface BulkOperationResponse {
    success: boolean;
    totalItems: number;
    successCount: number;
    failureCount: number;
    successfulItems: string[];
    failedItems: Array<{ key: string; error: string }>;
    message: string;
    timestamp: string;
}

/**
 * Change tracking for commit operation
 */
export interface MetadataChanges {
    added: MetadataRecord[];
    updated: MetadataRecord[];
    deleted: string[];
}

/**
 * Validation result
 */
export interface ValidationResult {
    isValid: boolean;
    errors: string[];
}
