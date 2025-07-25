/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Input, SpaceBetween, FormField } from "@cloudscape-design/components";
import { XYZValue } from "../types/AssetLinksTypes";

interface XYZInputProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
}

interface XYZDisplayValue {
    x: string;
    y: string;
    z: string;
}

export const XYZInput: React.FC<XYZInputProps> = ({
    value,
    onChange,
    placeholder = "0, 0, 0",
    disabled = false,
    invalid = false,
    ariaLabel = "XYZ coordinates",
}) => {
    const [xyzValues, setXyzValues] = useState<XYZDisplayValue>({ x: "", y: "", z: "" });

    // Parse the string value into XYZ components
    useEffect(() => {
        if (value && value.trim() !== "") {
            try {
                const parsed = JSON.parse(value);
                if (
                    parsed &&
                    typeof parsed === "object" &&
                    "x" in parsed &&
                    "y" in parsed &&
                    "z" in parsed
                ) {
                    setXyzValues({
                        x: parsed.x.toString(),
                        y: parsed.y.toString(),
                        z: parsed.z.toString(),
                    });
                } else {
                    // Try to parse comma-separated values
                    const parts = value.split(",").map((p) => p.trim());
                    if (parts.length === 3) {
                        setXyzValues({
                            x: parts[0],
                            y: parts[1],
                            z: parts[2],
                        });
                    }
                }
            } catch (error) {
                // If parsing fails, try comma-separated format
                const parts = value.split(",").map((p) => p.trim());
                if (parts.length === 3) {
                    setXyzValues({
                        x: parts[0],
                        y: parts[1],
                        z: parts[2],
                    });
                }
            }
        } else {
            // Fix Issue 2: For empty values, use empty strings instead of 0
            setXyzValues({ x: "", y: "", z: "" });
        }
    }, [value]);

    const handleValueChange = (axis: "x" | "y" | "z", newValue: string) => {
        const updatedValues = { ...xyzValues, [axis]: newValue };
        setXyzValues(updatedValues);

        // Only create JSON if all values are provided and valid
        if (updatedValues.x !== "" && updatedValues.y !== "" && updatedValues.z !== "") {
            const numericValues = {
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
            <FormField label="X">
                <Input
                    value={xyzValues.x}
                    onChange={({ detail }) => handleValueChange("x", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && xyzValues.x !== "" && !isValidNumber(xyzValues.x)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} X coordinate`}
                />
            </FormField>
            <FormField label="Y">
                <Input
                    value={xyzValues.y}
                    onChange={({ detail }) => handleValueChange("y", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && xyzValues.y !== "" && !isValidNumber(xyzValues.y)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Y coordinate`}
                />
            </FormField>
            <FormField label="Z">
                <Input
                    value={xyzValues.z}
                    onChange={({ detail }) => handleValueChange("z", detail.value)}
                    placeholder="0"
                    disabled={disabled}
                    invalid={invalid && xyzValues.z !== "" && !isValidNumber(xyzValues.z)}
                    type="number"
                    step="any"
                    ariaLabel={`${ariaLabel} Z coordinate`}
                />
            </FormField>
        </SpaceBetween>
    );
};

export default XYZInput;
