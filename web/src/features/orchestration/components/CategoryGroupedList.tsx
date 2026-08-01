/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo } from "react";

interface CategoryGroupedListProps<T> {
    items: T[];
    groupBy: (item: T) => string;
    renderItem: (item: T) => React.ReactNode;
    getKey?: (item: T) => string;
}

function CategoryGroupedList<T>({
    items,
    groupBy,
    renderItem,
    getKey,
}: CategoryGroupedListProps<T>) {
    const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

    // Group items by category and sort
    const { grouped, categories } = useMemo(() => {
        const groupedItems = items.reduce((acc, item) => {
            const category = groupBy(item);
            if (!acc[category]) {
                acc[category] = [];
            }
            acc[category].push(item);
            return acc;
        }, {} as Record<string, T[]>);

        const sortedCategories = Object.keys(groupedItems).sort();

        return { grouped: groupedItems, categories: sortedCategories };
    }, [items, groupBy]);

    const toggleCategory = (category: string) => {
        setCollapsed((prev) => ({
            ...prev,
            [category]: !prev[category],
        }));
    };

    return (
        <div className="space-y-2">
            {categories.map((category) => (
                <div
                    key={category}
                    className="orch-outline border border-border-default rounded-lg bg-surface-container overflow-hidden"
                >
                    <button
                        onClick={() => toggleCategory(category)}
                        className="orch-outline w-full px-4 py-2 flex items-center justify-between border-b border-border-default bg-surface-secondary hover:bg-surface-hover text-text-primary font-bold"
                    >
                        <span>{category}</span>
                        <span className="text-text-secondary">
                            {collapsed[category] ? "▶" : "▼"}
                        </span>
                    </button>
                    {!collapsed[category] && (
                        <div className="p-2 space-y-1">
                            {grouped[category].map((item, index) => (
                                <div key={getKey ? getKey(item) : index}>{renderItem(item)}</div>
                            ))}
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

export default CategoryGroupedList;
