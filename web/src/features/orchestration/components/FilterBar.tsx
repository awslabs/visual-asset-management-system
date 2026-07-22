/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import SearchInput from "./SearchInput";

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
}

const FilterBar: React.FC<FilterBarProps> = ({ value, onChange, facets = [] }) => {
    const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        onChange({ ...value, searchText: e.target.value });
    };

    const handleFacetChange = (key: string, selectedValue: string) => {
        onChange({
            ...value,
            facets: { ...value.facets, [key]: selectedValue },
        });
    };

    // Search box (with a magnifier icon, no text label) followed by the facet dropdowns, matching
    // the filter-row layout used elsewhere in the app. The parent row places this on the left and
    // any page-specific controls (group-by, include-archived) on the right.
    return (
        <div className="flex flex-wrap gap-2 items-center">
            <SearchInput value={value.searchText} onChange={handleSearchChange} />
            {facets.map((facet) => (
                <select
                    key={facet.key}
                    value={value.facets[facet.key] || ""}
                    onChange={(e) => handleFacetChange(facet.key, e.target.value)}
                    className="px-3 py-1.5 text-sm border border-border-input rounded-lg bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                    <option value="">{facet.label}: All</option>
                    {facet.options.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                            {opt.label}
                        </option>
                    ))}
                </select>
            ))}
        </div>
    );
};

export default FilterBar;
