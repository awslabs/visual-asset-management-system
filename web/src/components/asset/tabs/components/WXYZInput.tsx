/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Input, SpaceBetween, FormField } from "@cloudscape-design/components";

interface WXYZInputProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
}

interface WXYZDisplayValue {
    w: string;
    x: string;
    y: string;
    z: string;
}

export const WXYZInput: React.FC<WXYZInputProps> = ({
    value,
    onChange,
    placeholder = "1, 0, 0, 0",
    disabled = false,
    invalid = false,
    ariaLabel = "WXYZ quaternion",
}) => {
    const [wxyzValues, setWxyzValues] = useState<WXYZDisplayValue>({ w: "", x: "", y: "", z: "" });

    // Parse the string value into WXYZ components
    useEffect(() => {
        if (value && value.trim() !== "") {
            try {
                const parsed = JSON.parse(value);
                if (
                    parsed &&
                    typeof parsed === "object" &&
                    "w" in parsed &&
                    "x" in parsed &&
                    "y" in parsed &&
                    "z" in parsed
                ) {
                    setWxyzValues({
                        w: parsed.w.toString(),
                        x: parsed.x.toString(),
                        y: parsed.y.toString(),
                        z: parsed.z.toString(),
                    });
                } else {
                    // Try to parse comma-separated values
                    const parts = value.split(",").map((p) => p.trim());
                    if (parts.length === 4) {
                        setWxyzValues({
                            w: parts[0],
                            x: parts[1],
                            y: parts[2],
                            z: parts[3],
                        });
                    }
                }
            } catch (error) {
                // If parsing fails, try comma-separated format
                const parts = value.split(",").map((p) => p.trim());
                if (parts.length === 4) {
                    setWxyzValues({
                        w: parts[0],
                        x: parts[1],
                        y: parts[2],
                        z: parts[3],
                    });
                }
            }
        } else {
            // For empty values, use empty strings
            setWxyzValues({ w: "", x: "", y: "", z: "" });
        }
    }, [value]);

    const handleValueChange = (axis: "w" | "x" | "y" | "z", newValue: string) => {
        const updatedValues = { ...wxyzValues, [axis]: newValue };
        setWxyzValues(updatedValues);

        // Only create JSON if all values are provided and valid
        if (
            updatedValues.w !== "" &&
            updatedValues.x !== "" &&
            updatedValues.y !== "" &&
            updatedValues.z !== ""
        ) {
            const numericValues = {
                w: parseFloat(updatedValues.w) || 0,
                x: parseFloat(updatedValues.x) || 0,
                y: parseFloat(updatedValues.y) || 0,
                z: parseFloat(updatedValues.z) || 0,
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

    return (
        <SpaceBetween direction="horizontal" size="xs">
            <FormField label="W">
                <Input
                    value={wxyzValues.w}
                    onChange={({ detail }) => handleValueChange("w", detail.value)}
                    placeholder="1"
                    disabled={disabled}
                    invalid={invalid && wxyzValues.w !== "" && !isValidNumber(wxyzValues.w)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} W component`}
                />
            </FormField>
            <FormField label="X">
                <Input
                    value={wxyzValues.x}
                    onChange={({ detail }) => handleValueChange("x", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && wxyzValues.x !== "" && !isValidNumber(wxyzValues.x)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} X component`}
                />
            </FormField>
            <FormField label="Y">
                <Input
                    value={wxyzValues.y}
                    onChange={({ detail }) => handleValueChange("y", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && wxyzValues.y !== "" && !isValidNumber(wxyzValues.y)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Y component`}
                />
            </FormField>
            <FormField label="Z">
                <Input
                    value={wxyzValues.z}
                    onChange={({ detail }) => handleValueChange("z", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && wxyzValues.z !== "" && !isValidNumber(wxyzValues.z)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Z component`}
                />
            </FormField>
        </SpaceBetween>
    );
};

export default WXYZInput;
