/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import clsx from "clsx";
import type { FieldMeta } from "../types";
import styles from "../styles.module.css";

interface Props {
    field: FieldMeta;
    value: any;
    invalid?: boolean;
    onChange: (value: any) => void;
}

/** Handles text, number, and select inputs. */
export default function TextField({ field, value, invalid, onChange }: Props) {
    const isNumber = field.input === "number";
    const isSelect = field.input === "select";

    // null/undefined render as empty so placeholder text shows through.
    const stringValue = value == null ? "" : String(value);

    return (
        <div className={styles.field}>
            <label htmlFor={field.path} className={styles.fieldLabel}>
                {field.label}
            </label>
            {isSelect ? (
                <select
                    id={field.path}
                    className={clsx(styles.select, invalid && styles.inputInvalid)}
                    value={stringValue}
                    onChange={(e) => onChange(e.target.value)}
                >
                    {(field.options ?? []).map((opt) => (
                        <option key={opt.value} value={opt.value}>
                            {opt.label}
                        </option>
                    ))}
                </select>
            ) : (
                <input
                    id={field.path}
                    type={isNumber ? "number" : "text"}
                    className={clsx(styles.input, invalid && styles.inputInvalid)}
                    value={stringValue}
                    placeholder={field.placeholder}
                    min={field.min}
                    onChange={(e) => {
                        if (isNumber) {
                            const raw = e.target.value;
                            // Preserve empty as empty string; otherwise coerce to number.
                            onChange(raw === "" ? "" : Number(raw));
                        } else {
                            // Empty string maps to null (matches template "unset" convention).
                            onChange(e.target.value === "" ? null : e.target.value);
                        }
                    }}
                />
            )}
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}
