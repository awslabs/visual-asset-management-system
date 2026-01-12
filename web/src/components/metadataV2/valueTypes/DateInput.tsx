/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { DatePicker, SpaceBetween, FormField } from "@cloudscape-design/components";

interface DateInputProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
    error?: string;
}

export const DateInput: React.FC<DateInputProps> = ({
    value,
    onChange,
    placeholder = "YYYY-MM-DD",
    disabled = false,
    invalid = false,
    ariaLabel = "Date input",
    error,
}) => {
    const [dateValue, setDateValue] = useState("");

    // Parse the string value into date format
    useEffect(() => {
        if (value && value.trim() !== "") {
            try {
                // Try to parse ISO date string
                const date = new Date(value);
                if (!isNaN(date.getTime())) {
                    // Convert to YYYY-MM-DD format for DatePicker
                    const year = date.getFullYear();
                    const month = String(date.getMonth() + 1).padStart(2, "0");
                    const day = String(date.getDate()).padStart(2, "0");
                    setDateValue(`${year}-${month}-${day}`);
                } else {
                    setDateValue("");
                }
            } catch (error) {
                setDateValue("");
            }
        } else {
            setDateValue("");
        }
    }, [value]);

    const handleDateChange = (newValue: string) => {
        setDateValue(newValue);

        if (newValue) {
            try {
                // Convert to ISO string format for storage
                const date = new Date(newValue + "T00:00:00.000Z");
                if (!isNaN(date.getTime())) {
                    onChange(date.toISOString());
                } else {
                    onChange("");
                }
            } catch (error) {
                onChange("");
            }
        } else {
            onChange("");
        }
    };

    return (
        <FormField errorText={error}>
            <DatePicker
                value={dateValue}
                onChange={({ detail }) => handleDateChange(detail.value)}
                placeholder={placeholder}
                disabled={disabled}
                invalid={invalid}
                ariaLabel={ariaLabel}
            />
        </FormField>
    );
};

export default DateInput;
