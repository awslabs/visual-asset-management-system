/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Input, SpaceBetween, FormField } from "@cloudscape-design/components";

interface LLAInputProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
}

interface LLADisplayValue {
    lat: string;
    long: string;
    alt: string;
}

export const LLAInput: React.FC<LLAInputProps> = ({
    value,
    onChange,
    placeholder = "0, 0, 0",
    disabled = false,
    invalid = false,
    ariaLabel = "LLA coordinates",
}) => {
    const [llaValues, setLlaValues] = useState<LLADisplayValue>({ lat: "", long: "", alt: "" });

    // Parse the string value into LLA components
    useEffect(() => {
        if (value && value.trim() !== "") {
            try {
                const parsed = JSON.parse(value);
                if (
                    parsed &&
                    typeof parsed === "object" &&
                    "lat" in parsed &&
                    "long" in parsed &&
                    "alt" in parsed
                ) {
                    setLlaValues({
                        lat: parsed.lat.toString(),
                        long: parsed.long.toString(),
                        alt: parsed.alt.toString(),
                    });
                } else {
                    // Try to parse comma-separated values
                    const parts = value.split(",").map((p) => p.trim());
                    if (parts.length === 3) {
                        setLlaValues({
                            lat: parts[0],
                            long: parts[1],
                            alt: parts[2],
                        });
                    }
                }
            } catch (error) {
                // If parsing fails, try comma-separated format
                const parts = value.split(",").map((p) => p.trim());
                if (parts.length === 3) {
                    setLlaValues({
                        lat: parts[0],
                        long: parts[1],
                        alt: parts[2],
                    });
                }
            }
        } else {
            // For empty values, use empty strings
            setLlaValues({ lat: "", long: "", alt: "" });
        }
    }, [value]);

    const handleValueChange = (field: "lat" | "long" | "alt", newValue: string) => {
        const updatedValues = { ...llaValues, [field]: newValue };
        setLlaValues(updatedValues);

        // Only create JSON if all values are provided and valid
        if (updatedValues.lat !== "" && updatedValues.long !== "" && updatedValues.alt !== "") {
            const numericValues = {
                lat: parseFloat(updatedValues.lat) || 0,
                long: parseFloat(updatedValues.long) || 0,
                alt: parseFloat(updatedValues.alt) || 0,
            };
            const jsonString = JSON.stringify(numericValues);
            onChange(jsonString);
        } else {
            // If any field is empty, send empty string
            onChange("");
        }
    };

    const isValidNumber = (val: string) => {
        return !isNaN(parseFloat(val)) && isFinite(parseFloat(val));
    };

    const isValidLatitude = (val: string) => {
        const num = parseFloat(val);
        return isValidNumber(val) && num >= -90 && num <= 90;
    };

    const isValidLongitude = (val: string) => {
        const num = parseFloat(val);
        return isValidNumber(val) && num >= -180 && num <= 180;
    };

    return (
        <SpaceBetween direction="horizontal" size="xs">
            <FormField label="Latitude">
                <Input
                    value={llaValues.lat}
                    onChange={({ detail }) => handleValueChange("lat", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && llaValues.lat !== "" && !isValidLatitude(llaValues.lat)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Latitude (-90 to 90)`}
                />
            </FormField>
            <FormField label="Longitude">
                <Input
                    value={llaValues.long}
                    onChange={({ detail }) => handleValueChange("long", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && llaValues.long !== "" && !isValidLongitude(llaValues.long)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Longitude (-180 to 180)`}
                />
            </FormField>
            <FormField label="Altitude">
                <Input
                    value={llaValues.alt}
                    onChange={({ detail }) => handleValueChange("alt", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && llaValues.alt !== "" && !isValidNumber(llaValues.alt)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Altitude`}
                />
            </FormField>
        </SpaceBetween>
    );
};

export default LLAInput;
