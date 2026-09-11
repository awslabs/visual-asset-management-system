/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from "react";
import type { ConfigShape, DerivedChange, Profile, Rule } from "./types";
import { makeDefaultConfig } from "./defaults";
import { getByPath, setByPath } from "./pathUtils";
import { applyDerived, applyGovCloudSafeDefaults } from "./derived";
import { evaluateRules } from "./validation";
import { toConfigJson } from "./serialize";
import { SECTIONS, FIELDS, fieldsForSection } from "./schema";
import SectionPanel from "./panels/SectionPanel";
import ValidationSummary from "./panels/ValidationSummary";
import OutputPanel from "./panels/OutputPanel";
import ProfileSwitcher from "./panels/ProfileSwitcher";
import styles from "./styles.module.css";

/** Map each field path to its section id (for attributing rules to sections). */
const PATH_TO_SECTION: Record<string, string> = (() => {
    const map: Record<string, string> = {};
    for (const field of FIELDS) {
        map[field.path] = field.section;
    }
    return map;
})();

/** Determine which section(s) a rule concerns via its fieldPaths. */
function sectionsForRule(rule: Rule): Set<string> {
    const result = new Set<string>();
    for (const path of rule.fieldPaths) {
        const section = PATH_TO_SECTION[path];
        if (section) result.add(section);
    }
    return result;
}

export default function ConfigBuilder() {
    const [profile, setProfile] = useState<Profile>("commercial");
    const [config, setConfig] = useState<ConfigShape>(() => makeDefaultConfig("commercial"));
    const [dirty, setDirty] = useState(false);
    const [derivedChanges, setDerivedChanges] = useState<DerivedChange[]>([]);

    const activeRules = useMemo(() => evaluateRules(config), [config]);
    const json = useMemo(() => toConfigJson(config), [config]);

    const errorCount = activeRules.filter((r) => r.severity === "error").length;

    // Soft, non-blocking reminders surfaced when the user downloads. These are
    // valid configs (config.ts has fallbacks) but worth flagging.
    const downloadWarnings = useMemo(() => {
        const warnings: string[] = [];
        const account = getByPath(config, "env.account");
        if (account == null || account === "" || account === "UNDEFINED") {
            warnings.push(
                "The AWS account ID (env.account) is empty. CDK will fall back to CDK_DEFAULT_ACCOUNT at deploy time — set it explicitly to target a specific account."
            );
        }
        return warnings;
    }, [config]);

    // Per-section error/warning counts for header badges + force-open behavior.
    const sectionCounts = useMemo(() => {
        const counts: Record<string, { errors: number; warnings: number }> = {};
        for (const section of SECTIONS) {
            counts[section.id] = { errors: 0, warnings: 0 };
        }
        for (const rule of activeRules) {
            for (const sectionId of sectionsForRule(rule)) {
                if (!counts[sectionId]) continue;
                if (rule.severity === "error") counts[sectionId].errors += 1;
                else counts[sectionId].warnings += 1;
            }
        }
        return counts;
    }, [activeRules]);

    const commitConfig = (next: ConfigShape) => {
        const { config: derived, changes } = applyDerived(next);
        setConfig(derived);
        setDirty(true);
        if (changes.length > 0) setDerivedChanges(changes);
    };

    const handleChange = (path: string, value: unknown) => {
        commitConfig(setByPath(config, path, value));
    };

    const handleProfile = (next: Profile) => {
        if (next === profile) return;
        if (
            dirty &&
            typeof window !== "undefined" &&
            !window.confirm(
                "Switching the starting template resets all fields to that template's defaults. Continue?"
            )
        ) {
            return;
        }
        setProfile(next);
        setConfig(makeDefaultConfig(next));
        setDirty(false);
        setDerivedChanges([]);
    };

    const handleGovCloudDefaults = () => {
        commitConfig(applyGovCloudSafeDefaults(config));
    };

    const handleReset = () => {
        if (
            dirty &&
            typeof window !== "undefined" &&
            !window.confirm("Reset all fields to the current template defaults?")
        ) {
            return;
        }
        setConfig(makeDefaultConfig(profile));
        setDirty(false);
        setDerivedChanges([]);
    };

    const orderedSections = [...SECTIONS].sort((a, b) => a.order - b.order);

    return (
        <div>
            <div className={styles.toolbar}>
                <ProfileSwitcher profile={profile} onSelect={handleProfile} />
                <span className={styles.toolbarSpacer} />
                <button
                    type="button"
                    className={styles.secondaryButton}
                    onClick={handleReset}
                    title="Reset all fields to the current template defaults"
                >
                    Reset
                </button>
            </div>

            {derivedChanges.length > 0 && (
                <div className={styles.derivedNotice}>
                    <span>
                        Auto-adjusted:{" "}
                        {derivedChanges.map((c, i) => (
                            <span key={c.path}>
                                {i > 0 ? "; " : ""}
                                {c.reason}
                            </span>
                        ))}
                    </span>
                    <button
                        type="button"
                        className={styles.derivedDismiss}
                        aria-label="Dismiss"
                        onClick={() => setDerivedChanges([])}
                    >
                        ×
                    </button>
                </div>
            )}

            <div className={styles.builder}>
                <div className={styles.formColumn}>
                    {orderedSections.map((section) => (
                        <SectionPanel
                            key={section.id}
                            section={section}
                            fields={fieldsForSection(section.id)}
                            config={config}
                            activeRules={activeRules}
                            errorCount={sectionCounts[section.id]?.errors ?? 0}
                            warningCount={sectionCounts[section.id]?.warnings ?? 0}
                            forceOpen={(sectionCounts[section.id]?.errors ?? 0) > 0}
                            onChange={handleChange}
                        />
                    ))}
                </div>

                <div className={styles.outputColumn}>
                    <ValidationSummary
                        activeRules={activeRules}
                        onApplyGovCloudDefaults={handleGovCloudDefaults}
                    />
                    <OutputPanel json={json} downloadWarnings={downloadWarnings} />
                    {errorCount > 0 && (
                        <small className={styles.fieldHelp}>
                            {errorCount} {errorCount === 1 ? "error" : "errors"} present — fix
                            before deploying.
                        </small>
                    )}
                </div>
            </div>
        </div>
    );
}
