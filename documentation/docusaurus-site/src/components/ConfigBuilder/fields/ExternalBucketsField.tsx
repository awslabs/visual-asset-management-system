/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { FieldMeta } from "../types";
import styles from "../styles.module.css";

interface Bucket {
    bucketArn: string;
    baseAssetsPrefix: string;
    defaultSyncDatabaseId: string;
}

interface Props {
    field: FieldMeta;
    value: Bucket[] | null;
    onChange: (value: Bucket[] | null) => void;
}

const EMPTY: Bucket = { bucketArn: "", baseAssetsPrefix: "/", defaultSyncDatabaseId: "" };

/**
 * Editor for app.assetBuckets.externalAssetBuckets. Emits `null` when empty
 * (matching the template convention — config.ts treats null/[] differently:
 * createNewBucket=false with null externals is an error).
 */
export default function ExternalBucketsField({ field, value, onChange }: Props) {
    const buckets: Bucket[] = Array.isArray(value) ? value : [];

    const update = (next: Bucket[]) => onChange(next.length === 0 ? null : next);

    const updateField = (index: number, key: keyof Bucket, next: string) => {
        const copy = buckets.map((b) => ({ ...b }));
        copy[index][key] = next;
        update(copy);
    };

    const removeAt = (index: number) => update(buckets.filter((_, i) => i !== index));

    const add = () => update([...buckets, { ...EMPTY }]);

    return (
        <div className={styles.field}>
            <label className={styles.fieldLabel}>{field.label}</label>
            {buckets.map((bucket, index) => (
                <div className={styles.bucketCard} key={index}>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>Bucket ARN</label>
                        <input
                            className={styles.input}
                            placeholder="arn:aws:s3:::my-bucket"
                            value={bucket.bucketArn}
                            onChange={(e) => updateField(index, "bucketArn", e.target.value)}
                        />
                    </div>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>Base assets prefix</label>
                        <input
                            className={styles.input}
                            placeholder="/"
                            value={bucket.baseAssetsPrefix}
                            onChange={(e) => updateField(index, "baseAssetsPrefix", e.target.value)}
                        />
                    </div>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>Default sync database ID</label>
                        <input
                            className={styles.input}
                            value={bucket.defaultSyncDatabaseId}
                            onChange={(e) =>
                                updateField(index, "defaultSyncDatabaseId", e.target.value)
                            }
                        />
                    </div>
                    <button
                        type="button"
                        className={styles.iconButton}
                        onClick={() => removeAt(index)}
                    >
                        Remove bucket
                    </button>
                </div>
            ))}
            <button type="button" className={styles.addButton} onClick={add}>
                + Add external bucket
            </button>
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}
