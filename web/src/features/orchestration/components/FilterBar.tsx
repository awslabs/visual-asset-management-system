/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

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

    return (
        <div className="flex gap-2 items-center p-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded">
            <input
                type="text"
                value={value.searchText}
                onChange={handleSearchChange}
                placeholder="Search..."
                className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-500 dark:placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {facets.map((facet) => (
                <select
                    key={facet.key}
                    value={value.facets[facet.key] || ""}
                    onChange={(e) => handleFacetChange(facet.key, e.target.value)}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
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
