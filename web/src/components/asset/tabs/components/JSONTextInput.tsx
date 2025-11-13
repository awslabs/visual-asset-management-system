/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Textarea, SpaceBetween, FormField, Alert } from "@cloudscape-design/components";

interface JSONTextInputProps {
    value: string;
    onChange: (value: string) => void;
    type: "GEOPOINT" | "GEOJSON" | "JSON";
    placeholder?: string;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
}

export const JSONTextInput: React.FC<JSONTextInputProps> = ({
    value,
    onChange,
    type,
    placeholder,
    disabled = false,
    invalid = false,
    ariaLabel,
}) => {
    const [inputValue, setInputValue] = useState(value);
    const [validationError, setValidationError] = useState<string | null>(null);

    // Default placeholders based on type
    const getDefaultPlaceholder = () => {
        switch (type) {
            case "GEOPOINT":
                return '{"type": "Point", "coordinates": [-74.0060, 40.7128]}';
            case "GEOJSON":
                return '{"type": "Polygon", "coordinates": [[[0,0],[1,0],[1,1],[0,1],[0,0]]]}';
            case "JSON":
                return '{"key": "value", "number": 123}';
            default:
                return "{}";
        }
    };

    const getAriaLabel = () => {
        if (ariaLabel) return ariaLabel;
        switch (type) {
            case "GEOPOINT":
                return "GeoJSON Point coordinates";
            case "GEOJSON":
                return "GeoJSON object";
            case "JSON":
                return "JSON object";
            default:
                return "JSON input";
        }
    };

    useEffect(() => {
        setInputValue(value);
        setValidationError(null);
    }, [value]);

    const validateJSON = (jsonString: string): string | null => {
        if (!jsonString.trim()) {
            return null; // Empty is valid
        }

        try {
            const parsed = JSON.parse(jsonString);

            // Additional validation for GEOPOINT
            if (type === "GEOPOINT") {
                if (typeof parsed !== "object" || parsed === null) {
                    return "GEOPOINT must be a JSON object";
                }
                if (parsed.type !== "Point") {
                    return "GEOPOINT must have type: 'Point'";
                }
                if (!Array.isArray(parsed.coordinates) || parsed.coordinates.length !== 2) {
                    return "GEOPOINT must have coordinates array with [longitude, latitude]";
                }
                const [lng, lat] = parsed.coordinates;
                if (typeof lng !== "number" || typeof lat !== "number") {
                    return "GEOPOINT coordinates must be numbers";
                }
                if (lat < -90 || lat > 90) {
                    return "GEOPOINT latitude must be between -90 and 90";
                }
                if (lng < -180 || lng > 180) {
                    return "GEOPOINT longitude must be between -180 and 180";
                }
            }

            // Additional validation for GEOJSON
            if (type === "GEOJSON") {
                if (typeof parsed !== "object" || parsed === null) {
                    return "GEOJSON must be a JSON object";
                }
                if (!parsed.type) {
                    return "GEOJSON must have a 'type' property";
                }
                const validTypes = [
                    "Point",
                    "LineString",
                    "Polygon",
                    "MultiPoint",
                    "MultiLineString",
                    "MultiPolygon",
                    "GeometryCollection",
                    "Feature",
                    "FeatureCollection",
                ];
                if (!validTypes.includes(parsed.type)) {
                    return `GEOJSON type must be one of: ${validTypes.join(", ")}`;
                }
            }

            return null; // Valid JSON
        } catch (error) {
            return `Invalid JSON: ${error instanceof Error ? error.message : "Unknown error"}`;
        }
    };

    const handleInputChange = (newValue: string) => {
        setInputValue(newValue);

        const error = validateJSON(newValue);
        setValidationError(error);

        // Only call onChange if JSON is valid or empty
        if (!error) {
            onChange(newValue);
        }
    };

    const formatJSON = () => {
        try {
            const parsed = JSON.parse(inputValue);
            const formatted = JSON.stringify(parsed, null, 2);
            setInputValue(formatted);
            onChange(formatted);
        } catch (error) {
            // If parsing fails, don't format
        }
    };

    return (
        <SpaceBetween direction="vertical" size="xs">
            <FormField
                label={`${type} Data`}
                description={
                    type === "GEOPOINT"
                        ? "Enter a GeoJSON Point object"
                        : type === "GEOJSON"
                        ? "Enter any valid GeoJSON object"
                        : "Enter valid JSON data"
                }
            >
                <div style={{ position: "relative" }}>
                    <Textarea
                        value={inputValue}
                        onChange={({ detail }) => handleInputChange(detail.value)}
                        placeholder={placeholder || getDefaultPlaceholder()}
                        disabled={disabled}
                        invalid={invalid || !!validationError}
                        rows={6}
                        ariaLabel={getAriaLabel()}
                    />
                    {inputValue && !validationError && (
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
                            Format
                        </button>
                    )}
                </div>
            </FormField>

            {validationError && (
                <Alert type="error" dismissible={false}>
                    {validationError}
                </Alert>
            )}

            {type === "GEOPOINT" && !validationError && inputValue && (
                <div style={{ fontSize: "11px", color: "#666" }}>✓ Valid GeoJSON Point format</div>
            )}

            {type === "GEOJSON" && !validationError && inputValue && (
                <div style={{ fontSize: "11px", color: "#666" }}>✓ Valid GeoJSON format</div>
            )}

            {type === "JSON" && !validationError && inputValue && (
                <div style={{ fontSize: "11px", color: "#666" }}>✓ Valid JSON format</div>
            )}
        </SpaceBetween>
    );
};

export default JSONTextInput;
