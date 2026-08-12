/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import clsx from "clsx";
import type { ConfigShape, FieldMeta, Rule, Section } from "../types";
import FieldRenderer from "../fields/FieldRenderer";
import styles from "../styles.module.css";

interface Props {
    section: Section;
    fields: FieldMeta[];
    config: ConfigShape;
    /** Active rules, indexed elsewhere; we filter per field here. */
    activeRules: Rule[];
    /** Count of error/warning rules touching this section, for the header badge. */
    errorCount: number;
    warningCount: number;
    /** Force the section + its advanced area open (e.g. a hidden field has an error). */
    forceOpen?: boolean;
    onChange: (path: string, value: unknown) => void;
}

export default function SectionPanel({
    section,
    fields,
    config,
    activeRules,
    errorCount,
    warningCount,
    forceOpen,
    onChange,
}: Props) {
    const [open, setOpen] = useState(!section.advanced);
    const [showAdvanced, setShowAdvanced] = useState(false);

    const isOpen = open || !!forceOpen;
    const advancedShown = showAdvanced || !!forceOpen;

    // Visible fields, split into essential vs advanced.
    const visible = fields.filter((f) => !f.visibleWhen || f.visibleWhen(config));
    const essential = visible.filter((f) => !f.advanced);
    const advanced = visible.filter((f) => f.advanced);

    const errorsByPath = (path: string): Rule[] =>
        activeRules.filter((r) => r.severity === "error" && r.fieldPaths.includes(path));

    const renderField = (field: FieldMeta) => (
        <FieldRenderer
            key={field.path}
            field={field}
            config={config}
            fieldErrors={errorsByPath(field.path)}
            onChange={onChange}
        />
    );

    return (
        <div className={styles.section}>
            <button
                type="button"
                className={styles.sectionHeader}
                onClick={() => setOpen((o) => !o)}
                aria-expanded={isOpen}
            >
                <span className={clsx(styles.sectionCaret, isOpen && styles.sectionCaretOpen)}>
                    ▶
                </span>
                {section.label}
                {errorCount > 0 && (
                    <span className={styles.sectionBadge}>
                        {errorCount} {errorCount === 1 ? "error" : "errors"}
                    </span>
                )}
                {errorCount === 0 && warningCount > 0 && (
                    <span className={clsx(styles.sectionBadge, styles.sectionBadgeWarn)}>
                        {warningCount} {warningCount === 1 ? "warning" : "warnings"}
                    </span>
                )}
            </button>

            {isOpen && (
                <div className={styles.sectionBody}>
                    {section.description && (
                        <p className={styles.sectionDescription}>{section.description}</p>
                    )}

                    {essential.map(renderField)}

                    {advanced.length > 0 && (
                        <>
                            {!forceOpen && (
                                <button
                                    type="button"
                                    className={styles.advancedToggle}
                                    onClick={() => setShowAdvanced((s) => !s)}
                                >
                                    {advancedShown
                                        ? "− Hide advanced options"
                                        : `+ Show advanced options (${advanced.length})`}
                                </button>
                            )}
                            {advancedShown && (
                                <div className={styles.advancedArea}>
                                    {advanced.map(renderField)}
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
