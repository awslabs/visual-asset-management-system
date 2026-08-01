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
}

/**
 * A type-to-filter single-select combobox for the orchestration (Tailwind) module. Used where a
 * plain <select> would be unwieldy — e.g. picking one asset/file out of many. Filters the provided
 * option list client-side (the caller supplies options from a search API or a full list).
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
        const q = query.trim().toLowerCase();
        if (!q) return allOptions;
        return allOptions.filter(
            (o) =>
                o.label.toLowerCase().includes(q) ||
                (o.detail || "").toLowerCase().includes(q) ||
                o.value.toLowerCase().includes(q)
        );
    }, [allOptions, query]);

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
                className="w-full flex items-center justify-between gap-2 px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary text-left disabled:opacity-50"
            >
                <span className={selectedLabel ? "" : "text-text-secondary"}>
                    {loading ? "Loading…" : selectedLabel || placeholder || "Select…"}
                </span>
                <span aria-hidden className="text-text-secondary">
                    ▾
                </span>
            </button>
            {open && !disabled && (
                <div className="absolute z-50 mt-1 w-full rounded border border-border-default bg-surface-container shadow-lg">
                    <input
                        autoFocus
                        type="text"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Type to search…"
                        className="w-full px-3 py-2 border-b border-border-default bg-surface-input text-text-primary focus:outline-none"
                    />
                    {/* role="option" elements are direct children of the listbox: an intervening
                        <li> would break the owned-element relationship. */}
                    <div className="max-h-60 overflow-auto py-1" role="listbox">
                        {filtered.length === 0 ? (
                            <div className="px-3 py-2 text-sm text-text-secondary">No matches</div>
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
                </div>
            )}
        </div>
    );
};

export default SearchableSelect;
