/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

export interface TabStripTab<K extends string> {
    key: K;
    label: string;
}

interface TabStripProps<K extends string> {
    tabs: ReadonlyArray<TabStripTab<K>>;
    active: K;
    onChange: (key: K) => void;
    ariaLabel: string;
    /** The DOM id of a tab button; the caller's panel names the active one in `aria-labelledby`. */
    tabId: (key: K) => string;
    /** The DOM id of the panel every tab controls. */
    panelId: string;
}

/**
 * A tablist styled after Cloudscape's Tabs: the strip sits on the container surface with a bottom
 * divider, the selected tab is lifted onto that surface with an accent underline. One roving tab
 * stop: Arrow/Home/End move focus and selection together (automatic activation), so Tab reaches the
 * panel instead of every tab in turn. The caller renders the selected panel.
 */
function TabStrip<K extends string>({
    tabs,
    active,
    onChange,
    ariaLabel,
    tabId,
    panelId,
}: TabStripProps<K>) {
    const step = (target: 1 | -1 | "first" | "last") => {
        const current = tabs.findIndex((t) => t.key === active);
        const index =
            target === "first"
                ? 0
                : target === "last"
                ? tabs.length - 1
                : (current + target + tabs.length) % tabs.length;
        const next = tabs[index]?.key;
        if (!next || next === active) return;
        onChange(next);
        // Every tab button is already mounted; only its tabIndex changes, so focus can move now.
        document.getElementById(tabId(next))?.focus();
    };

    return (
        <div className="orch-outline border-b border-border-default">
            <nav
                className="flex gap-1"
                role="tablist"
                aria-label={ariaLabel}
                onKeyDown={(e) => {
                    const move =
                        e.key === "ArrowRight"
                            ? (1 as const)
                            : e.key === "ArrowLeft"
                            ? (-1 as const)
                            : e.key === "Home"
                            ? ("first" as const)
                            : e.key === "End"
                            ? ("last" as const)
                            : null;
                    if (!move) return;
                    e.preventDefault();
                    step(move);
                }}
            >
                {tabs.map((t) => (
                    <button
                        key={t.key}
                        id={tabId(t.key)}
                        role="tab"
                        aria-selected={active === t.key}
                        aria-controls={panelId}
                        tabIndex={active === t.key ? 0 : -1}
                        onClick={() => onChange(t.key)}
                        className={`orch-outline px-3 py-2 -mb-px border-b-2 font-bold text-sm rounded-t transition-colors ${
                            active === t.key
                                ? "border-blue-600 dark:border-blue-400 text-blue-600 dark:text-blue-400"
                                : "border-transparent text-text-secondary hover:text-text-primary hover:bg-surface-hover"
                        }`}
                    >
                        {t.label}
                    </button>
                ))}
            </nav>
        </div>
    );
}

export default TabStrip;
