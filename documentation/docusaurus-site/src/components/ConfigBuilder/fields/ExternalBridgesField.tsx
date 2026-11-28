/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { FieldMeta } from "../types";
import styles from "../styles.module.css";

interface Bridge {
    accountId: string;
    roleArnPattern: string;
    invokeTargets?: string[];
}

interface Props {
    field: FieldMeta;
    value: Bridge[] | null;
    onChange: (value: Bridge[]) => void;
}

const EMPTY: Bridge = {
    accountId: "",
    roleArnPattern: "",
};

/**
 * Editor for app.pipelines.externalPipelineBridges. Emits `[]` when empty (the template
 * convention — config.ts treats the empty list as "no bridge").
 */
export default function ExternalBridgesField({ field, value, onChange }: Props) {
    const bridges: Bridge[] = Array.isArray(value) ? value : [];

    const update = (next: Bridge[]) => onChange(next);

    const updateField = (index: number, key: "accountId" | "roleArnPattern", next: string) => {
        const copy = bridges.map((b) => ({ ...b }));
        copy[index][key] = next;
        update(copy);
    };

    // An empty target list is deleted rather than emitted as [], matching the optional field.
    const updateTargets = (index: number, next: string[]) => {
        const copy = bridges.map((b) => ({ ...b }));
        if (next.length === 0) {
            delete copy[index].invokeTargets;
        } else {
            copy[index].invokeTargets = next;
        }
        update(copy);
    };

    const updateTargetAt = (index: number, targetIndex: number, next: string) => {
        const targets = [...(bridges[index].invokeTargets ?? [])];
        targets[targetIndex] = next;
        updateTargets(index, targets);
    };

    const removeTargetAt = (index: number, targetIndex: number) =>
        updateTargets(
            index,
            (bridges[index].invokeTargets ?? []).filter((_, i) => i !== targetIndex)
        );

    const addTarget = (index: number) =>
        updateTargets(index, [...(bridges[index].invokeTargets ?? []), ""]);

    const removeAt = (index: number) => update(bridges.filter((_, i) => i !== index));

    const add = () => update([...bridges, { ...EMPTY }]);

    return (
        <div className={styles.field}>
            <label className={styles.fieldLabel}>{field.label}</label>
            {bridges.map((bridge, index) => (
                <div className={styles.bucketCard} key={index}>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>Bridge account ID</label>
                        <input
                            className={styles.input}
                            placeholder="111122223333"
                            value={bridge.accountId}
                            onChange={(e) => updateField(index, "accountId", e.target.value)}
                        />
                    </div>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>
                            Role ARN pattern (role or role-name prefix allowed to publish)
                        </label>
                        <input
                            className={styles.input}
                            placeholder="arn:aws:iam::111122223333:role/my-bridge-*"
                            value={bridge.roleArnPattern}
                            onChange={(e) => updateField(index, "roleArnPattern", e.target.value)}
                        />
                    </div>
                    <div className={styles.field}>
                        <label className={styles.fieldLabel}>
                            Invoke targets (exact Lambda, SQS or EventBridge bus ARNs the workflow
                            role may call)
                        </label>
                        {(bridge.invokeTargets ?? []).map((target, targetIndex) => (
                            <div className={styles.listRow} key={targetIndex}>
                                <input
                                    className={`${styles.input} ${styles.listInput}`}
                                    placeholder="arn:aws:events:us-east-1:111122223333:event-bus/my-bridge"
                                    value={target}
                                    onChange={(e) =>
                                        updateTargetAt(index, targetIndex, e.target.value)
                                    }
                                />
                                <button
                                    type="button"
                                    className={styles.iconButton}
                                    onClick={() => removeTargetAt(index, targetIndex)}
                                    aria-label="Remove"
                                >
                                    Remove
                                </button>
                            </div>
                        ))}
                        <button
                            type="button"
                            className={styles.addButton}
                            onClick={() => addTarget(index)}
                        >
                            + Add invoke target
                        </button>
                    </div>
                    <button
                        type="button"
                        className={styles.iconButton}
                        onClick={() => removeAt(index)}
                    >
                        Remove bridge
                    </button>
                </div>
            ))}
            <button type="button" className={styles.addButton} onClick={add}>
                + Add external pipeline bridge
            </button>
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}
