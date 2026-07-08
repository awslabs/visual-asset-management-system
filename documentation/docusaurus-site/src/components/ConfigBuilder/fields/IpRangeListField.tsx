/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { FieldMeta } from "../types";
import styles from "../styles.module.css";

interface Props {
    field: FieldMeta;
    value: string[][];
    onChange: (value: string[][]) => void;
}

/** Editable list of [minIp, maxIp] pairs for allowedIpRanges. */
export default function IpRangeListField({ field, value, onChange }: Props) {
    const ranges: string[][] = Array.isArray(value) ? value : [];

    const updateAt = (index: number, position: 0 | 1, next: string) => {
        const copy = ranges.map((r) => [...r]);
        if (!Array.isArray(copy[index])) copy[index] = ["", ""];
        copy[index][position] = next;
        onChange(copy);
    };

    const removeAt = (index: number) => {
        onChange(ranges.filter((_, i) => i !== index));
    };

    const add = () => onChange([...ranges, ["", ""]]);

    return (
        <div className={styles.field}>
            <label className={styles.fieldLabel}>{field.label}</label>
            {ranges.map((range, index) => (
                <div className={styles.listRow} key={index}>
                    <input
                        className={`${styles.input} ${styles.listInput}`}
                        placeholder="min IP (192.168.1.1)"
                        value={range?.[0] ?? ""}
                        onChange={(e) => updateAt(index, 0, e.target.value)}
                    />
                    <input
                        className={`${styles.input} ${styles.listInput}`}
                        placeholder="max IP (192.168.1.255)"
                        value={range?.[1] ?? ""}
                        onChange={(e) => updateAt(index, 1, e.target.value)}
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
                + Add IP range
            </button>
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}
