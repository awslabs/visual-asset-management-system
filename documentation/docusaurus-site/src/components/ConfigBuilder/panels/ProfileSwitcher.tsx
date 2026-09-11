/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import clsx from "clsx";
import type { Profile } from "../types";
import styles from "../styles.module.css";

interface Props {
    profile: Profile;
    onSelect: (profile: Profile) => void;
}

const OPTIONS: { value: Profile; label: string }[] = [
    { value: "commercial", label: "Commercial" },
    { value: "govcloud", label: "GovCloud" },
    { value: "eusovereign", label: "EU Sovereign Cloud" },
];

export default function ProfileSwitcher({ profile, onSelect }: Props) {
    return (
        <>
            <span className={styles.toolbarLabel}>Starting template:</span>
            <div className={styles.profileGroup} role="group" aria-label="Configuration profile">
                {OPTIONS.map((opt) => (
                    <button
                        key={opt.value}
                        type="button"
                        className={clsx(
                            styles.profileButton,
                            profile === opt.value && styles.profileButtonActive
                        )}
                        aria-pressed={profile === opt.value}
                        onClick={() => onSelect(opt.value)}
                    >
                        {opt.label}
                    </button>
                ))}
            </div>
        </>
    );
}
