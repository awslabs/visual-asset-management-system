/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    Button,
    FormField,
    Input,
    Textarea,
    Select,
    SelectProps,
    Checkbox,
    Multiselect,
    MultiselectProps,
    SpaceBetween,
    Container,
    Header,
    Box,
    TokenGroup,
    TokenGroupProps,
    Icon,
} from "@cloudscape-design/components";
import {
    MetadataSchemaField,
    MetadataValueType,
    MetadataSchemaEntityType,
    VALUE_TYPE_LABELS,
} from "./types";
import {
    XYZInput,
    WXYZInput,
    Matrix4x4Input,
    LLAInput,
    JSONTextInput,
    DateInput,
    BooleanInput,
    InlineControlledListInput,
} from "../metadataV2/valueTypes";

interface SchemaFieldEditorProps {
    fields: MetadataSchemaField[];
    entityType: MetadataSchemaEntityType;
    onChange: (fields: MetadataSchemaField[]) => void;
}

const ControlledListInput: React.FC<{
    value: string[];
    onChange: (value: string[]) => void;
}> = ({ value, onChange }) => {
    const [inputValue, setInputValue] = React.useState(value.join(", "));

    React.useEffect(() => {
        setInputValue(value.join(", "));
    }, [value.join(", ")]);

    const handleBlur = () => {
        const values = inputValue
            .split(",")
            .map((v) => v.trim())
            .filter((v) => v.length > 0);
        onChange(values);
    };

    return (
        <Input
            value={inputValue}
            onChange={({ detail }) => setInputValue(detail.value)}
            onBlur={handleBlur}
            placeholder="value1, value2, value3"
        />
    );
};

export const SchemaFieldEditor: React.FC<SchemaFieldEditorProps> = ({
    fields,
    entityType,
    onChange,
}) => {
    const isFileAttribute = entityType === "fileAttribute";
    const [collapsedFields, setCollapsedFields] = React.useState<Set<number>>(new Set());

    // Sort fields by sequence number when they're loaded
    React.useEffect(() => {
        if (fields.length > 0) {
            const sortedFields = [...fields].sort((a, b) => {
                // Fields without sequence go to the end
                if (a.sequence === undefined && b.sequence === undefined) return 0;
                if (a.sequence === undefined) return 1;
                if (b.sequence === undefined) return -1;
                return a.sequence - b.sequence;
            });

            // Only update if order changed
            const orderChanged = sortedFields.some((f, i) => f !== fields[i]);
            if (orderChanged) {
                onChange(sortedFields);
            }
        }
    }, []); // Only run once on mount

    // Initialize all existing fields as collapsed when component mounts or fields are loaded
    React.useEffect(() => {
        if (fields.length > 0 && collapsedFields.size === 0) {
            // Collapse all fields that have a field name (existing fields)
            const initialCollapsed = new Set(
                fields.map((f, i) => (f.metadataFieldKeyName ? i : -1)).filter((i) => i !== -1)
            );
            setCollapsedFields(initialCollapsed);
        }
    }, [fields.length]);

    const addField = () => {
        const newField: MetadataSchemaField = {
            metadataFieldKeyName: "",
            metadataFieldValueType: "string",
            required: false,
            sequence: 99,
        };
        onChange([...fields, newField]);
        // New field will be expanded by default (not in collapsedFields set)
    };

    const toggleCollapse = (index: number) => {
        setCollapsedFields((prev) => {
            const newSet = new Set(prev);
            if (newSet.has(index)) {
                newSet.delete(index);
            } else {
                newSet.add(index);
            }
            return newSet;
        });
    };

    const removeField = (index: number) => {
        const newFields = fields.filter((_, i) => i !== index);
        onChange(newFields);
    };

    const updateField = (index: number, updates: Partial<MetadataSchemaField>) => {
        const newFields = [...fields];
        newFields[index] = { ...newFields[index], ...updates };
        onChange(newFields);
    };

    const getValueTypeOptions = (): SelectProps.Option[] => {
        if (isFileAttribute) {
            return [{ value: "string", label: VALUE_TYPE_LABELS.string }];
        }

        return Object.entries(VALUE_TYPE_LABELS).map(([value, label]) => ({
            value,
            label,
        }));
    };

    const getDependencyOptions = (currentIndex: number): MultiselectProps.Option[] => {
        return fields
            .filter((_, i) => i !== currentIndex)
            .filter((f) => f.metadataFieldKeyName && f.metadataFieldKeyName.length >= 1)
            .map((f) => ({
                value: f.metadataFieldKeyName,
                label: f.metadataFieldKeyName,
            }));
    };

    const renderDefaultValueInput = (field: MetadataSchemaField, index: number) => {
        const value = field.defaultMetadataFieldValue || "";
        const onChange = (newValue: string) => {
            updateField(index, { defaultMetadataFieldValue: newValue });
        };

        switch (field.metadataFieldValueType) {
            case "string":
            case "multiline_string":
                return (
                    <Input
                        value={value}
                        onChange={({ detail }) => onChange(detail.value)}
                        placeholder="Enter default value"
                    />
                );

            case "number":
                return (
                    <Input
                        value={value}
                        type="number"
                        onChange={({ detail }) => onChange(detail.value)}
                        placeholder="Enter default number"
                    />
                );

            case "boolean":
                return <BooleanInput value={value} onChange={onChange} />;

            case "date":
                return <DateInput value={value} onChange={onChange} />;

            case "xyz":
                return <XYZInput value={value} onChange={onChange} />;

            case "wxyz":
                return <WXYZInput value={value} onChange={onChange} />;

            case "matrix4x4":
                return <Matrix4x4Input value={value} onChange={onChange} />;

            case "lla":
                return <LLAInput value={value} onChange={onChange} />;

            case "geopoint":
                return <JSONTextInput value={value} onChange={onChange} type="GEOPOINT" />;

            case "geojson":
                return <JSONTextInput value={value} onChange={onChange} type="GEOJSON" />;

            case "json":
                return <JSONTextInput value={value} onChange={onChange} type="JSON" />;

            case "inline_controlled_list":
                return (
                    <InlineControlledListInput
                        value={value}
                        onChange={onChange}
                        options={field.controlledListKeys || []}
                    />
                );

            default:
                return (
                    <Input
                        value={value}
                        onChange={({ detail }) => onChange(detail.value)}
                        placeholder="Enter default value"
                    />
                );
        }
    };

    return (
        <SpaceBetween size="l">
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button iconName="add-plus" onClick={addField}>
                                Add Field
                            </Button>
                        }
                    >
                        Schema Fields
                    </Header>
                }
            >
                {fields.length === 0 ? (
                    <Box textAlign="center" color="text-body-secondary" padding="l">
                        No fields defined. Click "Add Field" to create a field.
                    </Box>
                ) : (
                    <SpaceBetween size="s">
                        {fields.map((field, index) => {
                            const isCollapsed = collapsedFields.has(index);
                            return (
                                <div style={{ marginBottom: isCollapsed ? "4px" : "16px" }}>
                                    <Container
                                        key={index}
                                        disableContentPaddings={isCollapsed}
                                        header={
                                            <Header
                                                variant="h3"
                                                actions={
                                                    <SpaceBetween direction="horizontal" size="xs">
                                                        <Button
                                                            iconName={
                                                                isCollapsed
                                                                    ? "angle-down"
                                                                    : "angle-up"
                                                            }
                                                            variant="icon"
                                                            onClick={() => toggleCollapse(index)}
                                                            ariaLabel={
                                                                isCollapsed
                                                                    ? "Expand field"
                                                                    : "Collapse field"
                                                            }
                                                        />
                                                        <Button
                                                            iconName="remove"
                                                            variant="icon"
                                                            onClick={() => removeField(index)}
                                                            ariaLabel="Remove field"
                                                        />
                                                    </SpaceBetween>
                                                }
                                            >
                                                {field.metadataFieldKeyName || `Field ${index + 1}`}
                                                {isCollapsed && field.metadataFieldKeyName && (
                                                    <span
                                                        style={{
                                                            marginLeft: "8px",
                                                            fontSize: "0.9em",
                                                            color: "#666",
                                                        }}
                                                    >
                                                        {field.required && (
                                                            <span
                                                                style={{
                                                                    color: "#d13212",
                                                                    fontWeight: "bold",
                                                                }}
                                                            >
                                                                [R]{" "}
                                                            </span>
                                                        )}
                                                        ({field.metadataFieldValueType})
                                                    </span>
                                                )}
                                            </Header>
                                        }
                                    >
                                        {!isCollapsed && (
                                            <SpaceBetween size="m">
                                                <FormField
                                                    label="Field Name"
                                                    errorText={
                                                        !field.metadataFieldKeyName ||
                                                        field.metadataFieldKeyName.length < 1
                                                            ? "Field name is required"
                                                            : undefined
                                                    }
                                                >
                                                    <Input
                                                        value={field.metadataFieldKeyName}
                                                        onChange={({ detail }) =>
                                                            updateField(index, {
                                                                metadataFieldKeyName: detail.value,
                                                            })
                                                        }
                                                        placeholder="Enter field name"
                                                    />
                                                </FormField>

                                                <FormField
                                                    label="Value Type"
                                                    constraintText={
                                                        isFileAttribute
                                                            ? "File attributes only support string value type"
                                                            : "Select the data type for this field"
                                                    }
                                                >
                                                    <Select
                                                        selectedOption={
                                                            getValueTypeOptions().find(
                                                                (opt) =>
                                                                    opt.value ===
                                                                    field.metadataFieldValueType
                                                            ) || null
                                                        }
                                                        onChange={({ detail }) => {
                                                            const updates: Partial<MetadataSchemaField> =
                                                                {
                                                                    metadataFieldValueType: detail
                                                                        .selectedOption
                                                                        .value as MetadataValueType,
                                                                };
                                                            // Clear controlled list keys if not inline_controlled_list
                                                            if (
                                                                detail.selectedOption.value !==
                                                                "inline_controlled_list"
                                                            ) {
                                                                updates.controlledListKeys =
                                                                    undefined;
                                                            }
                                                            // Clear default value when type changes
                                                            updates.defaultMetadataFieldValue =
                                                                undefined;
                                                            updateField(index, updates);
                                                        }}
                                                        options={getValueTypeOptions()}
                                                        disabled={isFileAttribute}
                                                    />
                                                </FormField>

                                                {field.metadataFieldValueType ===
                                                    "inline_controlled_list" && (
                                                    <FormField
                                                        label="Controlled List Values"
                                                        constraintText="Enter comma-delimited values (e.g., value1, value2, value3). These will be converted to a list for the API."
                                                        errorText={
                                                            !field.controlledListKeys ||
                                                            field.controlledListKeys.length === 0
                                                                ? "At least one value is required for controlled lists"
                                                                : undefined
                                                        }
                                                    >
                                                        <ControlledListInput
                                                            value={field.controlledListKeys || []}
                                                            onChange={(values) => {
                                                                updateField(index, {
                                                                    controlledListKeys:
                                                                        values.length > 0
                                                                            ? values
                                                                            : undefined,
                                                                });
                                                            }}
                                                        />
                                                    </FormField>
                                                )}

                                                <FormField label="Required">
                                                    <Checkbox
                                                        checked={field.required}
                                                        onChange={({ detail }) =>
                                                            updateField(index, {
                                                                required: detail.checked,
                                                            })
                                                        }
                                                    >
                                                        This field is required
                                                    </Checkbox>
                                                </FormField>

                                                <FormField
                                                    label="Sequence Number (Optional)"
                                                    constraintText="This sequence is used based on all the applied schemas to an entity type's metadata. Lower numbers appear first."
                                                >
                                                    <Input
                                                        value={field.sequence?.toString() || ""}
                                                        type="number"
                                                        onChange={({ detail }) =>
                                                            updateField(index, {
                                                                sequence: detail.value
                                                                    ? parseInt(detail.value)
                                                                    : undefined,
                                                            })
                                                        }
                                                        placeholder="99"
                                                    />
                                                </FormField>

                                                <FormField
                                                    label="Dependencies"
                                                    constraintText="Fields that must be filled before this field"
                                                >
                                                    <Multiselect
                                                        selectedOptions={
                                                            field.dependsOnFieldKeyName?.map(
                                                                (key) => ({
                                                                    value: key,
                                                                    label: key,
                                                                })
                                                            ) || []
                                                        }
                                                        onChange={({ detail }) =>
                                                            updateField(index, {
                                                                dependsOnFieldKeyName:
                                                                    detail.selectedOptions.map(
                                                                        (opt) => opt.value || ""
                                                                    ),
                                                            })
                                                        }
                                                        options={getDependencyOptions(index)}
                                                        placeholder="Select dependent fields"
                                                        disabled={
                                                            getDependencyOptions(index).length === 0
                                                        }
                                                    />
                                                </FormField>

                                                <FormField
                                                    label="Default Value (Optional)"
                                                    constraintText="Default value for this field"
                                                >
                                                    <SpaceBetween direction="horizontal" size="xs">
                                                        <div style={{ flex: 1 }}>
                                                            {renderDefaultValueInput(field, index)}
                                                        </div>
                                                        {field.defaultMetadataFieldValue && (
                                                            <Button
                                                                variant="normal"
                                                                iconName="close"
                                                                onClick={() =>
                                                                    updateField(index, {
                                                                        defaultMetadataFieldValue:
                                                                            undefined,
                                                                    })
                                                                }
                                                                ariaLabel="Clear default value"
                                                            >
                                                                Clear
                                                            </Button>
                                                        )}
                                                    </SpaceBetween>
                                                </FormField>
                                            </SpaceBetween>
                                        )}
                                    </Container>
                                </div>
                            );
                        })}
                    </SpaceBetween>
                )}
            </Container>
        </SpaceBetween>
    );
};
