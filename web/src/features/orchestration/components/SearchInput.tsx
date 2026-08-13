/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

interface SearchInputProps {
    value: string;
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
    ariaLabel?: string;
    className?: string;
}

/**
 * Fixed-width search box with a leading magnifier icon (no text label), matching the search fields
 * used elsewhere in the app. Reused by FilterBar and DataTable so the orchestration pages share one
 * search affordance.
 */
const SearchInput: React.FC<SearchInputProps> = ({
    value,
    onChange,
    ariaLabel = "Search",
    className = "w-64",
}) => (
    <div className={`relative ${className}`}>
        <svg
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-secondary"
            viewBox="0 0 16 16"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
        >
            <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" />
            <line
                x1="11"
                y1="11"
                x2="14"
                y2="14"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
            />
        </svg>
        <input
            type="text"
            aria-label={ariaLabel}
            value={value}
            onChange={onChange}
            className="w-full pl-9 pr-3 py-1.5 text-sm border border-border-input rounded-lg bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
    </div>
);

export default SearchInput;
