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
    isDefault?: boolean;
    bucketAccountId?: string;
    bucketRegion?: string;
    bucketKmsKeyArn?: string;
}

/** Optional keys omitted from the emitted config when left blank. */
type OptionalKey = "bucketAccountId" | "bucketRegion" | "bucketKmsKeyArn";

interface Props {
    field: FieldMeta;
    value: Bucket[] | null;
    onChange: (value: Bucket[] | null) => void;
}

const EMPTY: Bucket = {
    bucketArn: "",
    baseAssetsPrefix: "/",
    defaultSyncDatabaseId: "",
    isDefault: false,
};

/**
 * Editor for app.assetBuckets.externalAssetBuckets. Emits `null` when empty
 * (matching the template convention — config.ts treats null/[] differently:
 * createNewBucket=false with null externals is an error).
 */
export default function ExternalBucketsField({ field, value, onChange }: Props) {
    const buckets: Bucket[] = Array.isArray(value) ? value : [];

    const update = (next: Bucket[]) => onChange(next.length === 0 ? null : next);

    const updateField = (
        index: number,
        key: "bucketArn" | "baseAssetsPrefix" | "defaultSyncDatabaseId",
        next: string
    ) => {
        const copy = buckets.map((b) => ({ ...b }));
        copy[index][key] = next;
        update(copy);
    };

    // A blank optional value is deleted rather than emitted as "", which getConfig() would otherwise
    // read as a supplied-but-invalid account id, Region, or key ARN.
    const updateOptionalField = (index: number, key: OptionalKey, next: string) => {
        const copy = buckets.map((b) => ({ ...b }));
        if (next.trim() === "") {
            delete copy[index][key];
        } else {
            copy[index][key] = next;
        }
        update(copy);
    };

    // Only one bucket may be the default; setting one clears the rest.
    const setDefault = (index: number, next: boolean) => {
        const copy = buckets.map((b, i) => ({ ...b, isDefault: next && i === index }));
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
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>
                            <input
                                type="checkbox"
                                checked={!!bucket.isDefault}
                                onChange={(e) => setDefault(index, e.target.checked)}
                            />{" "}
                            Default asset bucket (houses pipeline template + run I/O data)
                        </label>
                    </div>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>
                            Bucket account ID (leave blank for the deployment account)
                        </label>
                        <input
                            className={styles.input}
                            placeholder="111122223333"
                            value={bucket.bucketAccountId ?? ""}
                            onChange={(e) =>
                                updateOptionalField(index, "bucketAccountId", e.target.value)
                            }
                        />
                    </div>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>
                            Bucket Region (leave blank for the deployment Region)
                        </label>
                        <input
                            className={styles.input}
                            placeholder="us-east-1"
                            value={bucket.bucketRegion ?? ""}
                            onChange={(e) =>
                                updateOptionalField(index, "bucketRegion", e.target.value)
                            }
                        />
                    </div>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>
                            Bucket KMS key ARN (required for a customer managed key)
                        </label>
                        <input
                            className={styles.input}
                            placeholder="arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000"
                            value={bucket.bucketKmsKeyArn ?? ""}
                            onChange={(e) =>
                                updateOptionalField(index, "bucketKmsKeyArn", e.target.value)
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
