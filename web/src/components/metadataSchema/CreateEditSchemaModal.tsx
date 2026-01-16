/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    FormField,
    Input,
    Select,
    SelectProps,
    Checkbox,
    Alert,
} from "@cloudscape-design/components";
import {
    MetadataSchema,
    MetadataSchemaField,
    MetadataSchemaEntityType,
    ENTITY_TYPE_LABELS,
} from "./types";
import { SchemaFieldEditor } from "./SchemaFieldEditor";

interface CreateEditSchemaModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSubmit: (schemaData: any) => Promise<void>;
    editingSchema?: MetadataSchema | null;
    databaseId: string;
}

export const CreateEditSchemaModal: React.FC<CreateEditSchemaModalProps> = ({
    visible,
    onDismiss,
    onSubmit,
    editingSchema,
    databaseId,
}) => {
    const isEditMode = !!editingSchema;

    const [schemaName, setSchemaName] = useState("");
    const [entityType, setEntityType] = useState<MetadataSchemaEntityType>("assetMetadata");
    const [enabled, setEnabled] = useState(true);
    const [fileKeyTypeRestriction, setFileKeyTypeRestriction] = useState("");
    const [fields, setFields] = useState<MetadataSchemaField[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Initialize form when editing
    useEffect(() => {
        if (editingSchema) {
            setSchemaName(editingSchema.schemaName);
            setEntityType(editingSchema.metadataSchemaEntityType);
            setEnabled(editingSchema.enabled);
            setFileKeyTypeRestriction(editingSchema.fileKeyTypeRestriction || "");
            setFields(editingSchema.fields.fields || []);
        } else {
            // Reset form for create mode
            setSchemaName("");
            setEntityType("assetMetadata");
            setEnabled(true);
            setFileKeyTypeRestriction("");
            setFields([]);
        }
        setError(null);
    }, [editingSchema, visible]);

    const showFileTypeRestriction = entityType === "fileMetadata" || entityType === "fileAttribute";

    const validateForm = (): string | null => {
        if (!schemaName || schemaName.length < 1) {
            return "Schema name is required";
        }

        if (fields.length === 0) {
            return "At least one field is required";
        }

        // Validate all fields have names
        for (let i = 0; i < fields.length; i++) {
            if (!fields[i].metadataFieldKeyName || fields[i].metadataFieldKeyName.length < 1) {
                return `Field ${i + 1}: Field name is required`;
            }
        }

        // Check for duplicate field names
        const fieldNames = fields.map((f) => f.metadataFieldKeyName);
        const duplicates = fieldNames.filter((name, index) => fieldNames.indexOf(name) !== index);
        if (duplicates.length > 0) {
            return `Duplicate field names found: ${duplicates.join(", ")}`;
        }

        // Validate controlled list fields have values
        for (let i = 0; i < fields.length; i++) {
            if (
                fields[i].metadataFieldValueType === "inline_controlled_list" &&
                (!fields[i].controlledListKeys || fields[i].controlledListKeys!.length === 0)
            ) {
                return `Field ${i + 1}: Controlled list fields must have at least one value`;
            }
        }

        // Validate fileAttribute only has string fields
        if (entityType === "fileAttribute") {
            const nonStringFields = fields.filter((f) => f.metadataFieldValueType !== "string");
            if (nonStringFields.length > 0) {
                return "File attribute schemas can only contain string type fields";
            }
        }

        return null;
    };

    const handleSubmit = async () => {
        const validationError = validateForm();
        if (validationError) {
            setError(validationError);
            return;
        }

        setLoading(true);
        setError(null);

        try {
            const schemaData: any = {
                schemaName,
                enabled,
                fields: {
                    fields: fields,
                },
            };

            if (isEditMode) {
                schemaData.metadataSchemaId = editingSchema!.metadataSchemaId;
            } else {
                schemaData.databaseId = databaseId;
                schemaData.metadataSchemaEntityType = entityType;
            }

            // Add file type restriction if applicable
            if (showFileTypeRestriction && fileKeyTypeRestriction) {
                schemaData.fileKeyTypeRestriction = fileKeyTypeRestriction;
            }

            await onSubmit(schemaData);
            onDismiss();
        } catch (err: any) {
            console.error("Error submitting schema:", err);
            setError(err.message || "Failed to save schema");
        } finally {
            setLoading(false);
        }
    };

    const getEntityTypeOptions = (): SelectProps.Option[] => {
        return Object.entries(ENTITY_TYPE_LABELS).map(([value, label]) => ({
            value,
            label,
        }));
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            size="large"
            header={isEditMode ? "Edit Metadata Schema" : "Create Metadata Schema"}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss} disabled={loading}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleSubmit}
                            loading={loading}
                            disabled={loading}
                        >
                            {isEditMode ? "Update Schema" : "Create Schema"}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween size="l">
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}

                {isEditMode && editingSchema && (
                    <FormField label="Schema ID" constraintText="Read-only identifier">
                        <Input value={editingSchema.metadataSchemaId} disabled readOnly />
                    </FormField>
                )}

                <FormField
                    label="Schema Name"
                    constraintText="A descriptive name for this metadata schema"
                >
                    <Input
                        value={schemaName}
                        onChange={({ detail }) => setSchemaName(detail.value)}
                        placeholder="e.g., Asset Properties, File Attributes"
                    />
                </FormField>

                <FormField
                    label="Entity Type"
                    constraintText={
                        isEditMode
                            ? "Entity type cannot be changed after creation"
                            : "Select the type of entity this schema applies to"
                    }
                >
                    <Select
                        selectedOption={
                            getEntityTypeOptions().find((opt) => opt.value === entityType) || null
                        }
                        onChange={({ detail }) => {
                            const newEntityType = detail.selectedOption
                                .value as MetadataSchemaEntityType;
                            setEntityType(newEntityType);

                            // If switching to fileAttribute, convert all fields to string
                            if (newEntityType === "fileAttribute") {
                                setFields(
                                    fields.map((f) => ({
                                        ...f,
                                        metadataFieldValueType: "string",
                                        controlledListKeys: undefined,
                                    }))
                                );
                            }

                            // Clear file type restriction if not applicable
                            if (
                                newEntityType !== "fileMetadata" &&
                                newEntityType !== "fileAttribute"
                            ) {
                                setFileKeyTypeRestriction("");
                            }
                        }}
                        options={getEntityTypeOptions()}
                        disabled={isEditMode}
                    />
                </FormField>

                {showFileTypeRestriction && (
                    <FormField
                        label="File Type Restriction (Optional)"
                        constraintText="Comma-delimited file extensions (e.g., .jpg,.png,.pdf)"
                    >
                        <Input
                            value={fileKeyTypeRestriction}
                            onChange={({ detail }) => setFileKeyTypeRestriction(detail.value)}
                            placeholder=".jpg,.png,.pdf"
                        />
                    </FormField>
                )}

                <FormField label="Enabled">
                    <Checkbox
                        checked={enabled}
                        onChange={({ detail }) => setEnabled(detail.checked)}
                    >
                        Schema is enabled and active
                    </Checkbox>
                </FormField>

                <SchemaFieldEditor fields={fields} entityType={entityType} onChange={setFields} />
            </SpaceBetween>
        </Modal>
    );
};
