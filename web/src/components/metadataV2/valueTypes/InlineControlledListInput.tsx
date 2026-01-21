/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Select, FormField } from "@cloudscape-design/components";

interface InlineControlledListInputProps {
    value: string;
    onChange: (value: string) => void;
    options: string[];
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
    error?: string;
}

export const InlineControlledListInput: React.FC<InlineControlledListInputProps> = ({
    value,
    onChange,
    options,
    disabled = false,
    invalid = false,
    ariaLabel = "Controlled list selection",
    error,
}) => {
    // Add a "Clear" option at the beginning
    const selectOptions = [
        { label: "(Clear)", value: "", disabled: false },
        ...options.map((opt) => ({
            label: opt,
            value: opt,
        })),
    ];

    const selectedOption = value ? { label: value, value: value } : null;

    return (
        <FormField
            label="Select Value"
            description="Choose from available options"
            errorText={error}
        >
            <Select
                selectedOption={selectedOption}
                onChange={({ detail }) => {
                    onChange(detail.selectedOption.value || "");
                }}
                options={selectOptions}
                placeholder="Select an option"
                disabled={disabled}
                invalid={invalid}
                ariaLabel={ariaLabel}
                expandToViewport={true}
            />
        </FormField>
    );
};

export default InlineControlledListInput;
