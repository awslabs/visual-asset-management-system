/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { FieldMeta } from "../types";
import styles from "../styles.module.css";

interface Props {
    field: FieldMeta;
    value: string[];
    onChange: (value: string[]) => void;
}

/** Editable list of strings (e.g. NVIDIA instanceTypes). */
export default function StringArrayField({ field, value, onChange }: Props) {
    const items = Array.isArray(value) ? value : [];

    const updateAt = (index: number, next: string) => {
        const copy = [...items];
        copy[index] = next;
        onChange(copy);
    };

    const removeAt = (index: number) => {
        onChange(items.filter((_, i) => i !== index));
    };

    const add = () => onChange([...items, ""]);

    return (
        <div className={styles.field}>
            <label className={styles.fieldLabel}>{field.label}</label>
            {items.map((item, index) => (
                <div className={styles.listRow} key={index}>
                    <input
                        className={`${styles.input} ${styles.listInput}`}
                        value={item}
                        onChange={(e) => updateAt(index, e.target.value)}
                    />
                    <button
                        type="button"
                        className={styles.iconButton}
                        onClick={() => removeAt(index)}
                        aria-label="Remove"
                    >
                        Remove
                    </button>
                </div>
            ))}
            <button type="button" className={styles.addButton} onClick={add}>
                + Add value
            </button>
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}
