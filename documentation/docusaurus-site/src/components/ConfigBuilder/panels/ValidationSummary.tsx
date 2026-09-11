/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import clsx from "clsx";
import type { Rule } from "../types";
import styles from "../styles.module.css";

interface Props {
    activeRules: Rule[];
    /** Invoked when the user accepts the GovCloud-safe defaults shortcut. */
    onApplyGovCloudDefaults: () => void;
}

/**
 * Whether the GovCloud-safe shortcut would help: a GovCloud-specific error
 * (VPC off, CloudFront on, or Location Service on) is active. These rules also
 * cover EU Sovereign Cloud, which sets app.govCloud.enabled to true.
 */
const GOVCLOUD_RULE_IDS = [
    "govcloud-requires-vpc",
    "govcloud-no-cloudfront",
    "govcloud-no-location",
];
function showGovCloudShortcut(activeRules: Rule[]): boolean {
    return activeRules.some((r) => GOVCLOUD_RULE_IDS.includes(r.id));
}

export default function ValidationSummary({ activeRules, onApplyGovCloudDefaults }: Props) {
    const errors = activeRules.filter((r) => r.severity === "error");
    const warnings = activeRules.filter((r) => r.severity === "warning");

    if (errors.length === 0 && warnings.length === 0) {
        return (
            <div className={clsx(styles.summary, styles.summaryOk)}>
                <p className={styles.summaryTitle}>✓ No configuration issues detected</p>
                <small className={styles.fieldHelp}>
                    This matches the deploy-time checks in <code>getConfig()</code>. Always run{" "}
                    <code>cdk synth</code> before deploying.
                </small>
            </div>
        );
    }

    return (
        <div className={clsx(styles.summary, errors.length > 0 && styles.summaryError)}>
            <p className={styles.summaryTitle}>
                {errors.length > 0
                    ? `${errors.length} ${
                          errors.length === 1 ? "error" : "errors"
                      } to fix before deploying`
                    : `${warnings.length} ${warnings.length === 1 ? "warning" : "warnings"}`}
                {errors.length > 0 && warnings.length > 0 && ` · ${warnings.length} warning(s)`}
            </p>
            <ul className={styles.summaryList}>
                {errors.map((rule) => (
                    <li key={rule.id} className={styles.summaryItemError}>
                        {rule.message}
                    </li>
                ))}
                {warnings.map((rule) => (
                    <li key={rule.id} className={styles.summaryItemWarn}>
                        {rule.message}
                    </li>
                ))}
            </ul>
            {showGovCloudShortcut(activeRules) && (
                <button
                    type="button"
                    className={styles.govButton}
                    onClick={onApplyGovCloudDefaults}
                >
                    Apply GovCloud / EU Sovereign-safe front-end defaults
                </button>
            )}
            <small className={styles.fieldHelp} style={{ marginTop: "0.5rem" }}>
                You can still download or copy the config to keep editing — this builder is a
                helper, not a gate.
            </small>
        </div>
    );
}
