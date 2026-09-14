/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Z } from "./zLayers";

/**
 * How long a typed term settles before it is reported to the caller's search.
 *
 * `onQueryChange` feeds a TanStack query key, and each distinct key is a separate server search — so
 * reporting per keystroke made request volume a function of characters typed rather than of searches
 * performed, with every intermediate response discarded. One typing burst now costs one request.
 * Enter still reports at once, which is what the field's placeholder advertises.
 */
export const QUERY_REPORT_DEBOUNCE_MS = 300;

export interface SelectOption {
    value: string;
    label: string;
    /** Optional secondary text shown under the label. */
    detail?: string;
}

interface SearchableSelectProps {
    options: SelectOption[];
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    loading?: boolean;
    ariaLabel?: string;
    /** Shown as the first, always-present option (e.g. a "whole asset" sentinel). */
    leadingOption?: SelectOption;
    /**
     * Supply this to resolve matches on the SERVER instead of filtering locally. The component reports
     * the typed text (and reports it again on Enter) and renders whatever `options` it is then given, so
     * a picker can back onto a search API rather than needing every option up front. Omit it to keep the
     * client-side filtering, which is right when the caller already holds the full list.
     */
    onQueryChange?: (query: string) => void;
    /** Message under the list, e.g. "showing 100 of 4,312 — refine the search". */
    footerNote?: string;
}

/**
 * Where a panel opened from `trigger` mounts. A Radix Dialog blocks wheel and touch scrolling everywhere
 * outside its own panel (`role="dialog"`) while it is open, so a list portalled to body could not be
 * scrolled by mouse or touch from the execute dialog — only the arrow keys reached the options past the
 * first screen. Inside the dialog panel the lock treats the list as the dialog's own scrollable region; a
 * sibling of the panel's scrolling body, it is still not clipped by it. Outside a dialog: body, as a
 * Portal does by default.
 */
const portalContainerFor = (trigger: HTMLElement | null): HTMLElement | undefined =>
    trigger?.closest<HTMLElement>('[role="dialog"]') ?? undefined;

/**
 * A type-to-filter single-select combobox for the orchestration (Tailwind) module. Used where a
 * plain <select> would be unwieldy — e.g. picking one asset/file out of many.
 *
 * By default it filters the supplied option list client-side. Pass `onQueryChange` to have the CALLER
 * resolve matches (a search API) instead: the component then renders the options it is given verbatim,
 * which is what lets a picker work against thousands of records without loading them all.
 */
const SearchableSelect: React.FC<SearchableSelectProps> = ({
    options,
    value,
    onChange,
    placeholder,
    disabled,
    loading,
    ariaLabel,
    leadingOption,
    onQueryChange,
    footerNote,
}) => {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    // Resolved on open, once the trigger is in the DOM.
    const [portalContainer, setPortalContainer] = useState<HTMLElement | undefined>(undefined);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const reportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // Read through a ref so a settled report never fires through a stale callback.
    const onQueryChangeRef = useRef(onQueryChange);
    onQueryChangeRef.current = onQueryChange;

    const cancelPendingReport = () => {
        if (reportTimerRef.current !== null) {
            clearTimeout(reportTimerRef.current);
            reportTimerRef.current = null;
        }
    };

    /** Report the term once typing settles, replacing any report still waiting. */
    const scheduleReport = (term: string) => {
        if (!onQueryChange) return;
        cancelPendingReport();
        reportTimerRef.current = setTimeout(() => {
            reportTimerRef.current = null;
            onQueryChangeRef.current?.(term);
        }, QUERY_REPORT_DEBOUNCE_MS);
    };

    const reportNow = (term: string) => {
        cancelPendingReport();
        onQueryChangeRef.current?.(term);
    };

    // An unmount while a report is pending would otherwise fire a search for a picker that is gone.
    useEffect(
        () => () => {
            if (reportTimerRef.current !== null) clearTimeout(reportTimerRef.current);
        },
        []
    );

    const allOptions = useMemo(
        () => (leadingOption ? [leadingOption, ...options] : options),
        [leadingOption, options]
    );

    // A value with no matching option (archived asset, truncated list) falls back to its raw value so
    // a committed selection is never displayed as the placeholder.
    const selectedLabel = useMemo(() => {
        const found = allOptions.find((o) => o.value === value);
        if (found) return found.label;
        return value || "";
    }, [allOptions, value]);

    const filtered = useMemo(() => {
        // Server-query mode: the caller already resolved the matches for this query, so filtering again
        // here would hide results the server deliberately returned (a metadata or fuzzy match whose
        // label does not contain the typed text).
        if (onQueryChange) return allOptions;
        const q = query.trim().toLowerCase();
        if (!q) return allOptions;
        return allOptions.filter(
            (o) =>
                o.label.toLowerCase().includes(q) ||
                (o.detail || "").toLowerCase().includes(q) ||
                o.value.toLowerCase().includes(q)
        );
    }, [allOptions, query, onQueryChange]);

    const close = () => {
        cancelPendingReport();
        setOpen(false);
        setQuery("");
    };

    // Arrow keys walk the rendered option buttons; focus stays where it lands so Enter/Space
    // activates through the button itself. The options live in the portalled content, so the
    // lookup is scoped to it rather than to the trigger's wrapper.
    const contentRef = useRef<HTMLDivElement>(null);
    const moveFocus = (delta: 1 | -1) => {
        const optionEls = Array.from(
            contentRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') || []
        );
        if (optionEls.length === 0) return;
        const current = optionEls.indexOf(document.activeElement as HTMLButtonElement);
        const next = current === -1 ? (delta === 1 ? 0 : optionEls.length - 1) : current + delta;
        optionEls[Math.max(0, Math.min(optionEls.length - 1, next))].focus();
    };

    return (
        // Radix owns open/close: the trigger's click, Escape, an outside pointer-down and focus
        // return all come through onOpenChange. A Radix Popover nests inside a Radix Dialog, whose
        // focus trap defers to it — a plain portal does not, and lost the input's focus on open.
        <Popover.Root
            open={open && !disabled}
            onOpenChange={(next) => {
                if (!next) {
                    close();
                    return;
                }
                setPortalContainer(portalContainerFor(triggerRef.current));
                setOpen(true);
            }}
        >
            <Popover.Trigger asChild>
                <button
                    ref={triggerRef}
                    type="button"
                    aria-label={ariaLabel}
                    aria-haspopup="listbox"
                    aria-expanded={open}
                    disabled={disabled}
                    className="orch-outline w-full flex items-center justify-between gap-2 px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary text-left disabled:opacity-50"
                >
                    <span className={selectedLabel ? "" : "text-text-secondary"}>
                        {loading ? "Loading…" : selectedLabel || placeholder || "Select…"}
                    </span>
                    <span aria-hidden className="text-text-secondary">
                        ▾
                    </span>
                </button>
            </Popover.Trigger>
            <Popover.Portal container={portalContainer}>
                <Popover.Content
                    ref={contentRef}
                    role="presentation"
                    align="start"
                    sideOffset={4}
                    // Inside a dialog panel this only has to clear the panel's own children. Portalled
                    // to body it is a SIBLING of any dialog rather than a child — z-index alone decides
                    // the order there, and Tailwind's z-50 would paint this underneath the dialog.
                    style={{ zIndex: Z.tooltip }}
                    // `orchestration-root` re-scopes the module's input/border resets onto the portal.
                    // At least as wide as the trigger, never wider than the viewport.
                    className="orchestration-root orch-outline min-w-[var(--radix-popover-trigger-width)] max-w-[90vw] rounded border border-border-default bg-surface-container shadow-lg"
                    onKeyDown={(e) => {
                        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                            e.preventDefault();
                            moveFocus(e.key === "ArrowDown" ? 1 : -1);
                        }
                    }}
                >
                    <input
                        autoFocus
                        type="text"
                        value={query}
                        onChange={(e) => {
                            // The field itself is uncontrolled by the search: the text lands
                            // immediately and only the REPORT to the caller waits for typing to
                            // settle, so client-side filtering stays per-keystroke.
                            setQuery(e.target.value);
                            scheduleReport(e.target.value);
                        }}
                        onKeyDown={(e) => {
                            // Enter re-runs the search, matching how the rest of the app's asset search
                            // behaves (press Enter to search).
                            if (e.key === "Enter" && onQueryChange) {
                                e.preventDefault();
                                reportNow(query);
                            }
                        }}
                        placeholder={
                            onQueryChange ? "Type to search, Enter to refresh…" : "Type to search…"
                        }
                        className="orch-outline w-full px-3 py-2 border-b border-border-default bg-surface-input text-text-primary focus:outline-none"
                    />
                    {/* role="option" elements are direct children of the listbox: an intervening
                        <li> would break the owned-element relationship. */}
                    <div className="max-h-60 overflow-auto py-1" role="listbox">
                        {filtered.length === 0 ? (
                            <div className="px-3 py-2 text-sm text-text-secondary">
                                {loading ? "Searching…" : "No matches"}
                            </div>
                        ) : (
                            filtered.map((o) => (
                                <button
                                    key={o.value}
                                    type="button"
                                    role="option"
                                    aria-selected={o.value === value}
                                    onClick={() => {
                                        onChange(o.value);
                                        close();
                                    }}
                                    className={`block w-full text-left px-3 py-2 hover:bg-surface-hover ${
                                        o.value === value ? "bg-surface-secondary" : ""
                                    }`}
                                >
                                    <span className="block text-sm text-text-primary">
                                        {o.label}
                                    </span>
                                    {o.detail && (
                                        <span className="block text-xs text-text-secondary">
                                            {o.detail}
                                        </span>
                                    )}
                                </button>
                            ))
                        )}
                    </div>
                    {footerNote && (
                        <div className="orch-outline border-t border-border-default px-3 py-1.5 text-xs text-text-secondary">
                            {footerNote}
                        </div>
                    )}
                </Popover.Content>
            </Popover.Portal>
        </Popover.Root>
    );
};

export default SearchableSelect;
