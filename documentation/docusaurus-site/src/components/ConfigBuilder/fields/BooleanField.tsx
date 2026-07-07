/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { FieldMeta } from "../types";
import styles from "../styles.module.css";

interface Props {
    field: FieldMeta;
    value: boolean;
    onChange: (value: boolean) => void;
}

export default function BooleanField({ field, value, onChange }: Props) {
    return (
        <div className={styles.field}>
            <div className={styles.checkboxRow}>
                <input
                    type="checkbox"
                    id={field.path}
                    checked={!!value}
                    onChange={(e) => onChange(e.target.checked)}
                />
                <label
                    htmlFor={field.path}
                    className={styles.fieldLabel}
                    style={{ marginBottom: 0 }}
                >
                    {field.label}
                </label>
            </div>
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}
