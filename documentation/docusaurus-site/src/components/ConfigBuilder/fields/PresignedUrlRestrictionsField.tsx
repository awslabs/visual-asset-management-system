/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { FieldMeta } from "../types";
import styles from "../styles.module.css";

interface Restrictions {
    allowedIpRanges: string[];
    allowedVpceIds: string[];
}

interface Props {
    field: FieldMeta;
    value: Restrictions | null;
    onChange: (value: Restrictions) => void;
}

const EMPTY: Restrictions = { allowedIpRanges: [], allowedVpceIds: [] };

/**
 * Editor for app.assetBuckets.presignedUrlNetworkRestrictions — two string
 * lists (allowed CIDR ranges and allowed VPC endpoint IDs). The two lists are
 * mutually exclusive; config.ts rejects setting both.
 */
export default function PresignedUrlRestrictionsField({ field, value, onChange }: Props) {
    const restrictions: Restrictions = value ?? EMPTY;
    const ipRanges = restrictions.allowedIpRanges ?? [];
    const vpceIds = restrictions.allowedVpceIds ?? [];

    const updateList = (key: keyof Restrictions, next: string[]) =>
        onChange({
            ...restrictions,
            allowedIpRanges: ipRanges,
            allowedVpceIds: vpceIds,
            [key]: next,
        });

    const renderList = (
        key: keyof Restrictions,
        items: string[],
        placeholder: string,
        addLabel: string
    ) => (
        <>
            {items.map((item, index) => (
                <div className={styles.listRow} key={index}>
                    <input
                        className={`${styles.input} ${styles.listInput}`}
                        placeholder={placeholder}
                        value={item}
                        onChange={(e) => {
                            const copy = [...items];
                            copy[index] = e.target.value;
                            updateList(key, copy);
                        }}
                    />
                    <button
                        type="button"
                        className={styles.iconButton}
                        onClick={() =>
                            updateList(
                                key,
                                items.filter((_, i) => i !== index)
                            )
                        }
                        aria-label="Remove"
                    >
                        Remove
                    </button>
                </div>
            ))}
            <button
                type="button"
                className={styles.addButton}
                onClick={() => updateList(key, [...items, ""])}
            >
                {addLabel}
            </button>
        </>
    );

    return (
        <div className={styles.field}>
            <label className={styles.fieldLabel}>{field.label}</label>
            <div className={styles.field}>
                <label className={styles.fieldLabel}>Allowed IP ranges (CIDR)</label>
                {renderList("allowedIpRanges", ipRanges, "203.0.113.0/24", "+ Add IP range")}
            </div>
            <div className={styles.field}>
                <label className={styles.fieldLabel}>Allowed VPC endpoint IDs</label>
                {renderList(
                    "allowedVpceIds",
                    vpceIds,
                    "vpce-0123456789abcdef0",
                    "+ Add VPC endpoint ID"
                )}
            </div>
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}
