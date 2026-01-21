/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Toggle, FormField } from "@cloudscape-design/components";

interface BooleanInputProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
    error?: string;
}

export const BooleanInput: React.FC<BooleanInputProps> = ({
    value,
    onChange,
    placeholder,
    disabled = false,
    invalid = false,
    ariaLabel = "Boolean value",
    error,
}) => {
    const [booleanValue, setBooleanValue] = useState(false);

    // Parse the string value into boolean
    useEffect(() => {
        if (value && value.trim() !== "") {
            setBooleanValue(value.toLowerCase() === "true");
        } else {
            setBooleanValue(false);
        }
    }, [value]);

    const handleToggleChange = (checked: boolean) => {
        setBooleanValue(checked);
        onChange(checked ? "true" : "false");
    };

    return (
        <FormField errorText={error}>
            <Toggle
                checked={booleanValue}
                onChange={({ detail }) => handleToggleChange(detail.checked)}
                disabled={disabled}
                ariaLabel={ariaLabel}
            >
                {booleanValue ? "True" : "False"}
            </Toggle>
        </FormField>
    );
};

export default BooleanInput;
