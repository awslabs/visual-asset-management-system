/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Textarea, FormField, Alert, SpaceBetween, Button } from "@cloudscape-design/components";
import { MetadataValueType } from "../types/metadata.types";
import { validateMetadataValue } from "../utils/validationHelpers";

interface RawValueEditorProps {
    value: string;
    valueType: MetadataValueType;
    onChange: (value: string) => void;
    onClose: () => void;
    disabled?: boolean;
    ariaLabel?: string;
}

export const RawValueEditor: React.FC<RawValueEditorProps> = ({
    value,
    valueType,
    onChange,
    onClose,
    disabled = false,
    ariaLabel = "Raw value editor",
}) => {
    const [rawValue, setRawValue] = useState(value);
    const [validationError, setValidationError] = useState<string | null>(null);

    useEffect(() => {
        setRawValue(value);
        setValidationError(null);
    }, [value]);

    const handleRawValueChange = (newValue: string) => {
        setRawValue(newValue);

        // Validate the raw value against the type
        const validation = validateMetadataValue(newValue, valueType);
        if (validation.isValid) {
            setValidationError(null);
        } else {
            setValidationError(validation.errors.join(", "));
        }
    };

    const handleSave = () => {
        // Final validation before saving
        const validation = validateMetadataValue(rawValue, valueType);
        if (validation.isValid) {
            onChange(rawValue);
            onClose();
        } else {
            setValidationError(validation.errors.join(", "));
        }
    };

    const handleCancel = () => {
        setRawValue(value);
        setValidationError(null);
        onClose();
    };

    const formatJSON = () => {
        try {
            const parsed = JSON.parse(rawValue);
            const formatted = JSON.stringify(parsed, null, 2);
            setRawValue(formatted);
            setValidationError(null);
        } catch (error) {
            setValidationError("Cannot format: Invalid JSON");
        }
    };

    const isJSONType =
        valueType === "xyz" ||
        valueType === "wxyz" ||
        valueType === "matrix4x4" ||
        valueType === "lla" ||
        valueType === "geopoint" ||
        valueType === "geojson" ||
        valueType === "json";

    return (
        <SpaceBetween direction="vertical" size="m">
            <FormField
                label={`Raw ${valueType.toUpperCase()} Value`}
                description={`Edit the raw string value for this ${valueType} field. The value must be valid ${valueType} format.`}
            >
                <div style={{ position: "relative" }}>
                    <Textarea
                        value={rawValue}
                        onChange={({ detail }) => handleRawValueChange(detail.value)}
                        placeholder={`Enter raw ${valueType} value`}
                        disabled={disabled}
                        invalid={!!validationError}
                        rows={isJSONType ? 10 : 4}
                        ariaLabel={ariaLabel}
                    />
                    {isJSONType && rawValue && !validationError && (
                        <button
                            type="button"
                            onClick={formatJSON}
                            disabled={disabled}
                            style={{
                                position: "absolute",
                                top: "8px",
                                right: "8px",
                                padding: "4px 8px",
                                fontSize: "11px",
                                border: "1px solid #ccc",
                                borderRadius: "4px",
                                background: "#f5f5f5",
                                cursor: disabled ? "not-allowed" : "pointer",
                                zIndex: 1,
                            }}
                        >
                            Format JSON
                        </button>
                    )}
                </div>
            </FormField>

            {validationError && (
                <Alert type="error" dismissible={false}>
                    {validationError}
                </Alert>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <Button variant="link" onClick={handleCancel} disabled={disabled}>
                    Cancel
                </Button>
                <Button
                    variant="primary"
                    onClick={handleSave}
                    disabled={disabled || !!validationError}
                >
                    Apply
                </Button>
            </div>

            <div
                style={{
                    fontSize: "11px",
                    color: "#666",
                    padding: "8px",
                    background: "#f5f5f5",
                    borderRadius: "4px",
                }}
            >
                <strong>Tips:</strong>
                <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
                    {isJSONType && (
                        <>
                            <li>Enter valid JSON format</li>
                            <li>Click "Format JSON" to auto-format</li>
                        </>
                    )}
                    {valueType === "xyz" && <li>Format: {`{"x": 0, "y": 0, "z": 0}`}</li>}
                    {valueType === "wxyz" && <li>Format: {`{"w": 1, "x": 0, "y": 0, "z": 0}`}</li>}
                    {valueType === "lla" && <li>Format: {`{"lat": 0, "long": 0, "alt": 0}`}</li>}
                    {valueType === "geopoint" && (
                        <li>Format: {`{"type": "Point", "coordinates": [lng, lat]}`}</li>
                    )}
                    {valueType === "date" && (
                        <li>Format: ISO 8601 (e.g., 2024-01-01T00:00:00.000Z)</li>
                    )}
                    {valueType === "boolean" && <li>Enter: "true" or "false"</li>}
                    {valueType === "number" && <li>Enter a numeric value</li>}
                </ul>
            </div>
        </SpaceBetween>
    );
};

export default RawValueEditor;
