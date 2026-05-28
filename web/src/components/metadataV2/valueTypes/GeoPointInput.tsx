/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { FormField, Input, SpaceBetween } from "@cloudscape-design/components";

interface GeoPointInputProps {
    value: string;
    onChange: (value: string) => void;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
    onValidationChange?: (isValid: boolean, errors: string[]) => void;
}

interface GeoPointDisplay {
    lat: string;
    lon: string;
}

const isFiniteNumber = (val: string) => !isNaN(parseFloat(val)) && isFinite(parseFloat(val));
const isValidLat = (val: string) =>
    isFiniteNumber(val) && parseFloat(val) >= -90 && parseFloat(val) <= 90;
const isValidLon = (val: string) =>
    isFiniteNumber(val) && parseFloat(val) >= -180 && parseFloat(val) <= 180;

/**
 * Editor for GeoJSON Point values stored as a JSON string of the form
 * {"type":"Point","coordinates":[lon, lat]}. Mirrors LLAInput's API so the
 * complex-edit modal can mount it next to the map picker.
 */
export const GeoPointInput: React.FC<GeoPointInputProps> = ({
    value,
    onChange,
    disabled = false,
    invalid = false,
    ariaLabel = "GeoPoint coordinates",
    onValidationChange,
}) => {
    const [display, setDisplay] = useState<GeoPointDisplay>({ lat: "", lon: "" });

    useEffect(() => {
        if (!value || value.trim() === "") {
            setDisplay({ lat: "", lon: "" });
            return;
        }
        try {
            const parsed = JSON.parse(value);
            if (
                parsed &&
                typeof parsed === "object" &&
                parsed.type === "Point" &&
                Array.isArray(parsed.coordinates) &&
                parsed.coordinates.length >= 2
            ) {
                setDisplay({
                    lon: String(parsed.coordinates[0]),
                    lat: String(parsed.coordinates[1]),
                });
            }
        } catch {
            // Leave the existing display values; the parent will surface the JSON error.
        }
    }, [value]);

    const handleField = (field: "lat" | "lon", next: string) => {
        const updated = { ...display, [field]: next };
        setDisplay(updated);

        const errors: string[] = [];
        if (updated.lat !== "" && !isValidLat(updated.lat))
            errors.push("Latitude must be between -90 and 90");
        if (updated.lon !== "" && !isValidLon(updated.lon))
            errors.push("Longitude must be between -180 and 180");
        const allFilled = updated.lat !== "" && updated.lon !== "";

        onValidationChange?.(allFilled && errors.length === 0, errors);

        if (allFilled && errors.length === 0) {
            onChange(
                JSON.stringify({
                    type: "Point",
                    coordinates: [parseFloat(updated.lon), parseFloat(updated.lat)],
                })
            );
        } else {
            onChange("");
        }
    };

    return (
        <SpaceBetween direction="horizontal" size="xs">
            <FormField label="Latitude">
                <Input
                    value={display.lat}
                    onChange={({ detail }) => handleField("lat", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && display.lat !== "" && !isValidLat(display.lat)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Latitude (-90 to 90)`}
                />
            </FormField>
            <FormField label="Longitude">
                <Input
                    value={display.lon}
                    onChange={({ detail }) => handleField("lon", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && display.lon !== "" && !isValidLon(display.lon)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Longitude (-180 to 180)`}
                />
            </FormField>
        </SpaceBetween>
    );
};

export default GeoPointInput;
