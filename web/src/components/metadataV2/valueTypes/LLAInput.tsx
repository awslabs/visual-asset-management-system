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
    error?: string;
    onValidationChange?: (isValid: boolean, errors: string[]) => void;
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
    error,
    onValidationChange,
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
            setLlaValues({ lat: "", long: "", alt: "" });
        }
    }, [value]);

    const handleValueChange = (field: "lat" | "long" | "alt", newValue: string) => {
        const updatedValues = { ...llaValues, [field]: newValue };
        setLlaValues(updatedValues);

        // Validate the values
        const validationErrors: string[] = [];
        let allFieldsFilled = true;

        if (updatedValues.lat === "" || updatedValues.long === "" || updatedValues.alt === "") {
            allFieldsFilled = false;
        }

        if (updatedValues.lat !== "" && !isValidLatitude(updatedValues.lat)) {
            validationErrors.push("Latitude must be between -90 and 90");
        }

        if (updatedValues.long !== "" && !isValidLongitude(updatedValues.long)) {
            validationErrors.push("Longitude must be between -180 and 180");
        }

        if (updatedValues.alt !== "" && !isValidNumber(updatedValues.alt)) {
            validationErrors.push("Altitude must be a valid number");
        }

        // Notify parent of validation state
        if (onValidationChange) {
            const isValid = allFieldsFilled && validationErrors.length === 0;
            onValidationChange(isValid, validationErrors);
        }

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
            <FormField
                label="Latitude"
                errorText={
                    error && llaValues.lat !== "" && !isValidLatitude(llaValues.lat)
                        ? "Must be -90 to 90"
                        : undefined
                }
            >
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
            <FormField
                label="Longitude"
                errorText={
                    error && llaValues.long !== "" && !isValidLongitude(llaValues.long)
                        ? "Must be -180 to 180"
                        : undefined
                }
            >
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
            <FormField
                label="Altitude"
                errorText={
                    error && llaValues.alt !== "" && !isValidNumber(llaValues.alt)
                        ? "Invalid number"
                        : undefined
                }
            >
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
