/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, Button, FormField, SpaceBetween, Textarea } from "@cloudscape-design/components";

interface GeoJSONInputProps {
    value: string;
    onChange: (value: string) => void;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
    onValidationChange?: (isValid: boolean, errors: string[]) => void;
}

const VALID_GEOMETRY_TYPES = new Set([
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
]);

const validateGeoJSON = (raw: string): string[] => {
    if (!raw || raw.trim() === "") return ["GeoJSON value is required"];
    let parsed: any;
    try {
        parsed = JSON.parse(raw);
    } catch (e: any) {
        return [`Invalid JSON: ${e?.message ?? "parse error"}`];
    }
    if (!parsed || typeof parsed !== "object") return ["GeoJSON must be a JSON object"];
    if (!parsed.type) return ["GeoJSON must have a type property"];
    if (parsed.type === "Feature") {
        if (!parsed.geometry || !parsed.geometry.type) {
            return ["GeoJSON Feature must contain a geometry with a type"];
        }
        return [];
    }
    if (parsed.type === "FeatureCollection") {
        if (!Array.isArray(parsed.features)) {
            return ["GeoJSON FeatureCollection must contain a features array"];
        }
        return [];
    }
    if (!VALID_GEOMETRY_TYPES.has(parsed.type)) {
        return [`Unsupported GeoJSON type: ${parsed.type}`];
    }
    if (parsed.coordinates === undefined && parsed.type !== "GeometryCollection") {
        return [`GeoJSON ${parsed.type} must contain coordinates`];
    }
    return [];
};

/**
 * Textarea-based GeoJSON editor. Mirrors LLAInput / GeoPointInput contract so
 * the complex-edit modal can mount it side-by-side with MapMetadataPicker.
 */
export const GeoJSONInput: React.FC<GeoJSONInputProps> = ({
    value,
    onChange,
    disabled = false,
    invalid = false,
    ariaLabel = "GeoJSON value",
    onValidationChange,
}) => {
    const [text, setText] = useState<string>(value ?? "");

    useEffect(() => {
        setText(value ?? "");
        // Initial validation pass so the modal's Done button reflects parent-supplied state.
        if (value && value.trim() !== "") {
            const errors = validateGeoJSON(value);
            onValidationChange?.(errors.length === 0, errors);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value]);

    const propagate = (next: string) => {
        setText(next);
        const errors = validateGeoJSON(next);
        onValidationChange?.(errors.length === 0, errors);
        // Only propagate when the value parses; clearing the field is also valid as "empty"
        if (next.trim() === "") {
            onChange("");
        } else if (errors.length === 0) {
            onChange(next);
        } else {
            // Still send the raw string up so other side (the map) can stay coordinated;
            // the modal's Done button is gated by onValidationChange.
            onChange(next);
        }
    };

    const formatJson = () => {
        if (!text.trim()) return;
        try {
            const formatted = JSON.stringify(JSON.parse(text), null, 2);
            propagate(formatted);
        } catch {
            // Leave as-is; validation already surfaces the parse error.
        }
    };

    const errors = validateGeoJSON(text);

    return (
        <SpaceBetween direction="vertical" size="xs">
            <FormField
                label="GeoJSON value"
                description="Paste or edit a GeoJSON Geometry, Feature, or FeatureCollection."
                errorText={text && errors.length > 0 ? errors[0] : undefined}
            >
                <Textarea
                    value={text}
                    onChange={({ detail }) => propagate(detail.value)}
                    placeholder='{"type":"Polygon","coordinates":[[[lon,lat],...]]}'
                    rows={10}
                    disabled={disabled}
                    invalid={invalid && text !== "" && errors.length > 0}
                    ariaLabel={ariaLabel}
                />
            </FormField>
            <Box float="right">
                <Button onClick={formatJson} disabled={disabled || !text.trim()}>
                    Format JSON
                </Button>
            </Box>
        </SpaceBetween>
    );
};

export default GeoJSONInput;
