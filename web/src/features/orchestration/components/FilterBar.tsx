/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import SearchInput from "./SearchInput";
import RefreshButton from "./RefreshButton";

export interface FilterFacet {
    key: string;
    label: string;
    options: { label: string; value: string }[];
}

export interface FilterValue {
    searchText: string;
    facets: Record<string, string>;
}

interface FilterBarProps {
    value: FilterValue;
    onChange: (value: FilterValue) => void;
    facets?: FilterFacet[];
    /** When provided, renders a refresh icon (next to the search box) that force-refetches the list. */
    onRefresh?: () => void;
    refreshing?: boolean;
}

const FilterBar: React.FC<FilterBarProps> = ({
    value,
    onChange,
    facets = [],
    onRefresh,
    refreshing = false,
}) => {
    const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        onChange({ ...value, searchText: e.target.value });
    };

    const handleFacetChange = (key: string, selectedValue: string) => {
        onChange({
            ...value,
            facets: { ...value.facets, [key]: selectedValue },
        });
    };

    // Left side of the filter row: the search box (magnifier icon, no text label) + the refresh
    // button. Facet dropdowns render on the RIGHT via <FilterFacets> (alongside group-by / archive),
    // so the row reads: search + refresh on the left, all filters + page controls on the right.
    return (
        <div className="flex flex-wrap gap-2 items-center">
            <SearchInput value={value.searchText} onChange={handleSearchChange} />
            {onRefresh && <RefreshButton onClick={onRefresh} busy={refreshing} />}
            {/* Facets can still be passed for backward compatibility, but the standard layout renders
                them on the right via <FilterFacets>. */}
            {facets.map((facet) => (
                <FacetSelect
                    key={facet.key}
                    facet={facet}
                    value={value.facets[facet.key] || ""}
                    onChange={(v) => handleFacetChange(facet.key, v)}
                />
            ))}
        </div>
    );
};

/** A single facet dropdown. Shared by FilterBar (legacy inline) and FilterFacets (right-aligned). */
export const FacetSelect: React.FC<{
    facet: FilterFacet;
    value: string;
    onChange: (value: string) => void;
}> = ({ facet, value, onChange }) => (
    <select
        aria-label={facet.label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="px-3 py-1.5 text-sm border border-border-input rounded-lg bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500"
    >
        <option value="">{facet.label}: All</option>
        {facet.options.map((opt) => (
            <option key={opt.value} value={opt.value}>
                {opt.label}
            </option>
        ))}
    </select>
);

/**
 * The facet dropdowns, rendered on the RIGHT of a filter row (next to group-by / include-archived).
 * Standardizes the "all filter dropdowns on the right" layout across the orchestration list pages.
 */
export const FilterFacets: React.FC<{
    value: FilterValue;
    onChange: (value: FilterValue) => void;
    facets: FilterFacet[];
}> = ({ value, onChange, facets }) => (
    <>
        {facets.map((facet) => (
            <FacetSelect
                key={facet.key}
                facet={facet}
                value={value.facets[facet.key] || ""}
                onChange={(v) =>
                    onChange({ ...value, facets: { ...value.facets, [facet.key]: v } })
                }
            />
        ))}
    </>
);

export default FilterBar;
