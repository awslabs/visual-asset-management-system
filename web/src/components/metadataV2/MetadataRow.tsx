/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    Input,
    Select,
    Button,
    SpaceBetween,
    Icon,
    Modal,
    Box,
    Textarea,
    Alert,
} from "@cloudscape-design/components";
import { MetadataRowState, MetadataValueType } from "./types/metadata.types";
import {
    getAvailableValueTypes,
    formatValueForDisplay,
    isSchemaField,
    getValueTypeLabel,
} from "./utils/metadataHelpers";
import MetadataSchemaTooltip from "./MetadataSchemaTooltip";
import ValueHistoryTooltip from "./ValueHistoryTooltip";
import RawValueEditor from "./valueTypes/RawValueEditor";
import {
    XYZInput,
    WXYZInput,
    Matrix4x4Input,
    LLAInput,
    JSONTextInput,
    DateInput,
    BooleanInput,
    InlineControlledListInput,
} from "./valueTypes";

interface MetadataRowProps {
    row: MetadataRowState;
    index: number;
    rows?: MetadataRowState[]; // All rows for dependency checking
    onEdit: () => void;
    onCancel: () => void;
    onSave: () => void;
    onDelete: () => void;
    onKeyChange: (key: string) => void;
    onTypeChange: (type: MetadataValueType) => void;
    onValueChange: (value: string) => void;
    onValidationError?: (error: string | undefined) => void;
    readOnly?: boolean;
    isFileAttribute?: boolean;
}

export const MetadataRow: React.FC<MetadataRowProps> = ({
    row,
    index,
    onEdit,
    onCancel,
    onSave,
    onDelete,
    onKeyChange,
    onTypeChange,
    onValueChange,
    onValidationError,
    readOnly = false,
    isFileAttribute = false,
    rows,
}) => {
    const [showRawEditor, setShowRawEditor] = useState(false);
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
    const [showComplexEditor, setShowComplexEditor] = useState(false);
    const [modalValidationErrors, setModalValidationErrors] = useState<string[]>([]);
    const [isModalValueValid, setIsModalValueValid] = useState(true);
    const [modalOriginalValue, setModalOriginalValue] = useState<string>("");

    const isSchema = isSchemaField(row);
    // Keys can only be edited for new rows (not yet saved)
    // Schema fields can never have their key or type edited
    const canEditKey = row.isNew && !isSchema;
    const canEditType = !isSchema; // Type can be edited for non-schema fields

    // Complex types that should show "Edit Value" button instead of inline controls
    const complexTypes: MetadataValueType[] = ["xyz", "wxyz", "matrix4x4", "lla"];
    const isComplexType = complexTypes.includes(row.editType);

    // Check if required field is empty
    const isRequiredAndEmpty =
        isSchema && row.metadataSchemaRequired && (!row.editValue || row.editValue.trim() === "");

    // Check if dependencies are met
    const checkDependenciesMet = (): { met: boolean; missingFields: string[] } => {
        if (!row.metadataSchemaDependsOn || row.metadataSchemaDependsOn.length === 0 || !rows) {
            return { met: true, missingFields: [] };
        }

        const missingFields: string[] = [];

        console.log("[MetadataRow] Starting dependency check for", row.metadataKey || row.editKey);
        console.log("[MetadataRow] dependsOn array:", row.metadataSchemaDependsOn);
        console.log(
            "[MetadataRow] All available rows:",
            rows.map((r) => ({
                metadataKey: r.metadataKey,
                editKey: r.editKey,
                editValue: r.editValue,
            }))
        );

        for (const depField of row.metadataSchemaDependsOn) {
            // Look for the dependent field by checking both metadataKey and editKey
            // This handles both saved rows (metadataKey) and new/unsaved rows (editKey)
            const depRow = rows.find((r) => r.metadataKey === depField || r.editKey === depField);

            console.log("[MetadataRow] Checking dependency:", {
                currentField: row.metadataKey || row.editKey,
                dependsOn: depField,
                foundDepRow: !!depRow,
                depRowKey: depRow?.metadataKey || depRow?.editKey,
                depRowEditValue: depRow?.editValue,
                depRowMetadataValue: depRow?.metadataValue,
            });

            if (!depRow || !depRow.editValue || depRow.editValue.trim() === "") {
                missingFields.push(depField);
            }
        }

        const result = { met: missingFields.length === 0, missingFields };
        console.log(
            "[MetadataRow] Dependency check result for",
            row.metadataKey || row.editKey,
            ":",
            result
        );
        return result;
    };

    const dependencyCheck = checkDependenciesMet();
    const areDependenciesMet = dependencyCheck.met;
    const missingDependencies = dependencyCheck.missingFields;

    // Render the appropriate input component based on value type
    const renderValueInput = () => {
        // Disable if dependencies are not met
        const isDisabledDueToDeps = !areDependenciesMet;

        const commonProps = {
            value: row.editValue,
            onChange: onValueChange,
            disabled: isDisabledDueToDeps,
            ariaLabel: `${row.editKey} value`,
            error: row.validationError,
        };

        switch (row.editType) {
            case "xyz":
                return <XYZInput {...commonProps} />;
            case "wxyz":
                return <WXYZInput {...commonProps} />;
            case "matrix4x4":
                return <Matrix4x4Input {...commonProps} />;
            case "lla":
                return <LLAInput {...commonProps} />;
            case "geopoint":
                return (
                    <JSONTextInput
                        {...commonProps}
                        type="GEOPOINT"
                        onValidationChange={(isValid, errors) => {
                            // Update the row's validation error state via callback
                            if (onValidationError) {
                                onValidationError(
                                    !isValid && errors.length > 0 ? errors[0] : undefined
                                );
                            }
                        }}
                    />
                );
            case "geojson":
                return (
                    <JSONTextInput
                        {...commonProps}
                        type="GEOJSON"
                        onValidationChange={(isValid, errors) => {
                            // Update the row's validation error state via callback
                            if (onValidationError) {
                                onValidationError(
                                    !isValid && errors.length > 0 ? errors[0] : undefined
                                );
                            }
                        }}
                    />
                );
            case "json":
                return (
                    <JSONTextInput
                        {...commonProps}
                        type="JSON"
                        onValidationChange={(isValid, errors) => {
                            // Update the row's validation error state via callback
                            if (onValidationError) {
                                onValidationError(
                                    !isValid && errors.length > 0 ? errors[0] : undefined
                                );
                            }
                        }}
                    />
                );
            case "date":
                return <DateInput {...commonProps} />;
            case "boolean":
                return <BooleanInput {...commonProps} />;
            case "inline_controlled_list":
                if (
                    row.metadataSchemaControlledListKeys &&
                    row.metadataSchemaControlledListKeys.length > 0
                ) {
                    return (
                        <InlineControlledListInput
                            {...commonProps}
                            invalid={isRequiredAndEmpty}
                            options={row.metadataSchemaControlledListKeys}
                        />
                    );
                }
                // Fallback to string input if no controlled list defined
                return (
                    <Input
                        value={commonProps.value}
                        onChange={({ detail }) => commonProps.onChange(detail.value)}
                        disabled={commonProps.disabled}
                        invalid={isRequiredAndEmpty}
                        ariaLabel={commonProps.ariaLabel}
                        placeholder={
                            isDisabledDueToDeps
                                ? `Depends on: ${missingDependencies.join(", ")}`
                                : "Enter value"
                        }
                        type="text"
                    />
                );
            case "multiline_string":
                return (
                    <Textarea
                        value={commonProps.value}
                        onChange={({ detail }) => commonProps.onChange(detail.value)}
                        disabled={commonProps.disabled}
                        invalid={isRequiredAndEmpty}
                        ariaLabel={commonProps.ariaLabel}
                        placeholder={
                            isDisabledDueToDeps
                                ? `Depends on: ${missingDependencies.join(", ")}`
                                : "Enter text (supports multiple lines)"
                        }
                        rows={4}
                    />
                );
            case "number":
                return (
                    <Input
                        value={commonProps.value}
                        onChange={({ detail }) => commonProps.onChange(detail.value)}
                        disabled={commonProps.disabled}
                        invalid={isRequiredAndEmpty}
                        ariaLabel={commonProps.ariaLabel}
                        placeholder={
                            isDisabledDueToDeps
                                ? `Depends on: ${missingDependencies.join(", ")}`
                                : "Enter number"
                        }
                        type="number"
                        step="any"
                    />
                );
            case "string":
            default:
                return (
                    <Input
                        value={commonProps.value}
                        onChange={({ detail }) => commonProps.onChange(detail.value)}
                        disabled={commonProps.disabled}
                        invalid={isRequiredAndEmpty}
                        ariaLabel={commonProps.ariaLabel}
                        placeholder={
                            isDisabledDueToDeps
                                ? `Depends on: ${missingDependencies.join(", ")}`
                                : "Enter value"
                        }
                        type="text"
                    />
                );
        }
    };

    return (
        <>
            <tr>
                {/* Metadata Key Column */}
                <td style={{ padding: "12px", verticalAlign: "middle" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        {canEditKey ? (
                            <Input
                                value={row.editKey}
                                onChange={({ detail }) => onKeyChange(detail.value)}
                                placeholder="Enter key name"
                                ariaLabel="Metadata key"
                                disabled={readOnly}
                                invalid={row.isNew && (!row.editKey || row.editKey.trim() === "")}
                            />
                        ) : (
                            <span style={{ fontWeight: isSchema ? "bold" : "normal" }}>
                                {row.metadataKey || row.editKey}
                            </span>
                        )}

                        {/* Schema conflict warning - show before other icons */}
                        {row.metadataSchemaMultiFieldConflict && (
                            <Icon name="status-warning" variant="warning" />
                        )}

                        {/* Pending change indicator - after key */}
                        {(row.hasChanges || row.isNew) && !row.metadataSchemaMultiFieldConflict && (
                            <Icon name="status-pending" variant="warning" />
                        )}

                        {/* Schema tooltip - after key */}
                        {isSchema && (
                            <MetadataSchemaTooltip
                                schemaName={row.metadataSchemaName}
                                required={row.metadataSchemaRequired}
                                dependsOn={row.metadataSchemaDependsOn}
                                controlledListKeys={row.metadataSchemaControlledListKeys}
                                multiFieldConflict={row.metadataSchemaMultiFieldConflict}
                                defaultValue={row.metadataSchemaDefaultValue}
                                sequence={row.metadataSchemaSequence}
                            />
                        )}
                    </div>
                </td>

                {/* Metadata Type Column */}
                <td style={{ padding: "12px", verticalAlign: "middle" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        {canEditType ? (
                            <Select
                                selectedOption={{
                                    label: getValueTypeLabel(row.editType),
                                    value: row.editType,
                                }}
                                onChange={({ detail }) =>
                                    onTypeChange(detail.selectedOption.value as MetadataValueType)
                                }
                                options={getAvailableValueTypes(isFileAttribute)}
                                ariaLabel="Metadata type"
                                expandToViewport={true}
                                disabled={readOnly}
                            />
                        ) : (
                            <span style={{ fontWeight: isSchema ? "bold" : "normal" }}>
                                {getValueTypeLabel(row.metadataValueType || row.editType)}
                            </span>
                        )}

                        {/* Value history tooltip - only show when there are actual changes */}
                        {!row.isNew && row.hasChanges && (
                            <ValueHistoryTooltip
                                oldValue={row.originalValue}
                                oldType={row.originalType}
                                schemaDefaultValue={row.metadataSchemaDefaultValue}
                                hasChanges={row.hasChanges}
                            />
                        )}
                    </div>
                </td>

                {/* Metadata Value Column */}
                <td style={{ padding: "12px", verticalAlign: "middle" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <div style={{ flex: 1 }}>
                            {isComplexType ? (
                                // For complex types, show a button to open editor
                                <div
                                    style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: "8px",
                                        width: "100%",
                                    }}
                                >
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <Input
                                            value={row.editValue || ""}
                                            disabled={true}
                                            invalid={isRequiredAndEmpty}
                                            placeholder=""
                                            ariaLabel={`${row.editKey} value preview`}
                                        />
                                    </div>
                                    <div style={{ display: "inline-block" }} tabIndex={-1}>
                                        <Button
                                            onClick={() => {
                                                // Save the original value when opening the modal
                                                setModalOriginalValue(row.editValue);
                                                setShowComplexEditor(true);
                                            }}
                                            disabled={readOnly || !areDependenciesMet}
                                            ariaLabel="Edit value"
                                        >
                                            <span
                                                style={{
                                                    color: isRequiredAndEmpty
                                                        ? "#d13212"
                                                        : "inherit",
                                                }}
                                            >
                                                Edit Value
                                            </span>
                                        </Button>
                                    </div>
                                </div>
                            ) : (
                                // For simple types, show inline controls
                                renderValueInput()
                            )}
                        </div>

                        {/* Raw editor button */}
                        <div style={{ display: "inline-block" }} tabIndex={-1}>
                            <Button
                                variant="inline-icon"
                                iconName="edit"
                                onClick={() => setShowRawEditor(true)}
                                ariaLabel="Edit raw value"
                                disabled={readOnly || !areDependenciesMet}
                            />
                        </div>
                    </div>
                </td>

                {/* Actions Column */}
                <td style={{ padding: "12px", verticalAlign: "middle" }}>
                    <SpaceBetween direction="horizontal" size="xs">
                        {/* Revert changes button - only show if there are changes */}
                        {row.hasChanges && !row.isNew && (
                            <div style={{ display: "inline-block" }} tabIndex={-1}>
                                <Button
                                    variant="icon"
                                    iconName="close"
                                    onClick={onCancel}
                                    disabled={readOnly}
                                    ariaLabel="Revert changes"
                                />
                            </div>
                        )}

                        {/* Delete/Clear button */}
                        <div style={{ display: "inline-block" }} tabIndex={-1}>
                            <Button
                                variant="icon"
                                iconName={isSchema ? "undo" : "remove"}
                                onClick={() => {
                                    // For schema fields, always skip modal (just clear)
                                    if (isSchema) {
                                        onDelete();
                                    }
                                    // For non-schema: skip modal for new rows or rows with pending changes
                                    else if (row.isNew || row.hasChanges) {
                                        onDelete();
                                    } else {
                                        setShowDeleteConfirm(true);
                                    }
                                }}
                                disabled={readOnly || (isSchema && row.metadataSchemaRequired)}
                                ariaLabel={isSchema ? "Clear metadata" : "Delete metadata"}
                            />
                        </div>
                    </SpaceBetween>
                </td>
            </tr>

            {/* Complex Value Editor Modal (XYZ, WXYZ, Matrix4x4, LLA) */}
            <Modal
                visible={showComplexEditor}
                onDismiss={() => {
                    // Restore the original value when dismissing (X button clicked)
                    onValueChange(modalOriginalValue);
                    setShowComplexEditor(false);
                    setModalValidationErrors([]);
                    setIsModalValueValid(true);
                }}
                header={`Edit ${row.editType.toUpperCase()} Value`}
                size="large"
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                onClick={() => {
                                    onValueChange("");
                                    setShowComplexEditor(false);
                                    setModalValidationErrors([]);
                                    setIsModalValueValid(true);
                                }}
                                disabled={readOnly}
                            >
                                Clear
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => {
                                    setShowComplexEditor(false);
                                    setModalValidationErrors([]);
                                    setIsModalValueValid(true);
                                }}
                                disabled={!isModalValueValid}
                            >
                                Done
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <SpaceBetween direction="vertical" size="m">
                    {modalValidationErrors.length > 0 && (
                        <Alert type="error" dismissible={false}>
                            <SpaceBetween direction="vertical" size="xs">
                                <Box variant="p">
                                    Please correct the following validation errors:
                                </Box>
                                <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
                                    {modalValidationErrors.map((error, idx) => (
                                        <li key={idx}>{error}</li>
                                    ))}
                                </ul>
                            </SpaceBetween>
                        </Alert>
                    )}
                    <Box padding={{ vertical: "m" }}>
                        {row.editType === "xyz" && (
                            <XYZInput
                                value={row.editValue}
                                onChange={onValueChange}
                                disabled={readOnly}
                                ariaLabel={`${row.editKey} value`}
                                onValidationChange={(isValid, errors) => {
                                    setIsModalValueValid(isValid);
                                    setModalValidationErrors(errors);
                                }}
                            />
                        )}
                        {row.editType === "wxyz" && (
                            <WXYZInput
                                value={row.editValue}
                                onChange={onValueChange}
                                disabled={readOnly}
                                ariaLabel={`${row.editKey} value`}
                                onValidationChange={(isValid, errors) => {
                                    setIsModalValueValid(isValid);
                                    setModalValidationErrors(errors);
                                }}
                            />
                        )}
                        {row.editType === "matrix4x4" && (
                            <Matrix4x4Input
                                value={row.editValue}
                                onChange={onValueChange}
                                disabled={readOnly}
                                ariaLabel={`${row.editKey} value`}
                                onValidationChange={(isValid, errors) => {
                                    setIsModalValueValid(isValid);
                                    setModalValidationErrors(errors);
                                }}
                            />
                        )}
                        {row.editType === "lla" && (
                            <LLAInput
                                value={row.editValue}
                                onChange={onValueChange}
                                disabled={readOnly}
                                ariaLabel={`${row.editKey} value`}
                                onValidationChange={(isValid, errors) => {
                                    setIsModalValueValid(isValid);
                                    setModalValidationErrors(errors);
                                }}
                            />
                        )}
                    </Box>
                </SpaceBetween>
            </Modal>

            {/* Raw Value Editor Modal */}
            <Modal
                visible={showRawEditor}
                onDismiss={() => setShowRawEditor(false)}
                header="Edit Raw Value"
                size="large"
            >
                <RawValueEditor
                    value={row.editValue}
                    valueType={row.editType}
                    onChange={onValueChange}
                    onClose={() => setShowRawEditor(false)}
                />
            </Modal>

            {/* Delete Confirmation Modal */}
            <Modal
                visible={showDeleteConfirm}
                onDismiss={() => setShowDeleteConfirm(false)}
                header={isSchema ? "Clear Metadata" : "Delete Metadata"}
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button variant="link" onClick={() => setShowDeleteConfirm(false)}>
                                Cancel
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => {
                                    onDelete();
                                    setShowDeleteConfirm(false);
                                }}
                            >
                                {isSchema ? "Clear" : "Delete"}
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <SpaceBetween direction="vertical" size="m">
                    <Box variant="span">
                        {isSchema
                            ? `Are you sure you want to clear the value for "${
                                  row.metadataKey || row.editKey
                              }"? This will remove the current value but keep the field available for future use.`
                            : `Are you sure you want to delete the metadata field "${
                                  row.metadataKey || row.editKey
                              }"? This action cannot be undone.`}
                    </Box>
                </SpaceBetween>
            </Modal>
        </>
    );
};

MetadataRow.displayName = "MetadataRow";

export default MetadataRow;
