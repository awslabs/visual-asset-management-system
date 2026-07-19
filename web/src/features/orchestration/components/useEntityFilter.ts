/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMemo } from "react";

interface UseEntityFilterOptions<T> {
    searchText: string;
    searchFields: (keyof T)[];
    customFilter?: (item: T, searchText: string, facets: Record<string, any>) => boolean;
}

/**
 * Generic filter hook for entity lists with text search and custom filtering logic.
 * @param items - Array of items to filter
 * @param options - Filter configuration (searchText, searchFields, customFilter)
 * @param facets - Facet values for filtering
 * @returns Filtered array
 */
export function useEntityFilter<T>(
    items: T[],
    options: UseEntityFilterOptions<T>,
    facets: Record<string, any>
): T[] {
    const { searchText, searchFields, customFilter } = options;

    return useMemo(() => {
        return items.filter((item) => {
            // Text search across specified fields
            if (searchText) {
                const searchLower = searchText.toLowerCase();
                const matchesSearch = searchFields.some((field) => {
                    const value = item[field];
                    if (value == null) return false;
                    return String(value).toLowerCase().includes(searchLower);
                });
                if (!matchesSearch) return false;
            }

            // Custom filter logic (allows domain-specific filtering)
            if (customFilter) {
                return customFilter(item, searchText, facets);
            }

            return true;
        });
    }, [items, searchText, searchFields, facets, customFilter]);
}
