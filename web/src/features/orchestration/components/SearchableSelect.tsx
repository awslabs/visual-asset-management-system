/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useRef, useState } from "react";

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
    const containerRef = useRef<HTMLDivElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);

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
        setOpen(false);
        setQuery("");
    };

    // Arrow keys walk the rendered option buttons; focus stays where it lands so Enter/Space
    // activates through the button itself.
    const moveFocus = (delta: 1 | -1) => {
        const optionEls = Array.from(
            containerRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') || []
        );
        if (optionEls.length === 0) return;
        const current = optionEls.indexOf(document.activeElement as HTMLButtonElement);
        const next = current === -1 ? (delta === 1 ? 0 : optionEls.length - 1) : current + delta;
        optionEls[Math.max(0, Math.min(optionEls.length - 1, next))].focus();
    };

    // Close on outside click.
    React.useEffect(() => {
        if (!open) return;
        const onDocClick = (e: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
                setOpen(false);
                setQuery("");
            }
        };
        document.addEventListener("mousedown", onDocClick);
        return () => document.removeEventListener("mousedown", onDocClick);
    }, [open]);

    return (
        <div
            ref={containerRef}
            className="relative"
            onKeyDown={(e) => {
                if (!open) return;
                if (e.key === "Escape") {
                    e.stopPropagation();
                    close();
                    triggerRef.current?.focus();
                } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    moveFocus(e.key === "ArrowDown" ? 1 : -1);
                }
            }}
        >
            <button
                ref={triggerRef}
                type="button"
                aria-label={ariaLabel}
                aria-haspopup="listbox"
                aria-expanded={open}
                disabled={disabled}
                onClick={() => setOpen((o) => !o)}
                className="orch-outline w-full flex items-center justify-between gap-2 px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary text-left disabled:opacity-50"
            >
                <span className={selectedLabel ? "" : "text-text-secondary"}>
                    {loading ? "Loading…" : selectedLabel || placeholder || "Select…"}
                </span>
                <span aria-hidden className="text-text-secondary">
                    ▾
                </span>
            </button>
            {open && !disabled && (
                <div className="orch-outline absolute z-50 mt-1 w-full rounded border border-border-default bg-surface-container shadow-lg">
                    <input
                        autoFocus
                        type="text"
                        value={query}
                        onChange={(e) => {
                            setQuery(e.target.value);
                            onQueryChange?.(e.target.value);
                        }}
                        onKeyDown={(e) => {
                            // Enter re-runs the search, matching how the rest of the app's asset search
                            // behaves (press Enter to search).
                            if (e.key === "Enter" && onQueryChange) {
                                e.preventDefault();
                                onQueryChange(query);
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
                </div>
            )}
        </div>
    );
};

export default SearchableSelect;
