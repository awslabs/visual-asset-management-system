/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";

interface CollapsibleSectionProps {
    title: React.ReactNode;
    /** Optional short description under the title. */
    description?: React.ReactNode;
    /** Whether the section starts open (default true). */
    defaultOpen?: boolean;
    children: React.ReactNode;
}

/**
 * A titled, expand/collapse section for the create/edit pipeline & workflow forms, so a long form
 * is broken into scannable groups instead of one flat wall of fields.
 */
const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
    title,
    description,
    defaultOpen = true,
    children,
}) => {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <section className="orch-outline border border-border-default rounded-lg overflow-hidden">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
                className="w-full flex items-center justify-between gap-2 px-4 py-3 bg-surface-secondary text-left hover:bg-surface-hover"
            >
                <span>
                    <span className="text-sm font-semibold text-text-primary">{title}</span>
                    {description && (
                        <span className="block text-xs text-text-secondary mt-0.5">
                            {description}
                        </span>
                    )}
                </span>
                <span
                    aria-hidden
                    className={`text-text-secondary transition-transform ${
                        open ? "rotate-90" : ""
                    }`}
                >
                    ▶
                </span>
            </button>
            {open && <div className="p-4 space-y-4">{children}</div>}
        </section>
    );
};

export default CollapsibleSection;
