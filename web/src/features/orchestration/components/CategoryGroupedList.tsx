/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";

interface CategoryGroupedListProps<T> {
    items: T[];
    groupBy: (item: T) => string;
    renderItem: (item: T) => React.ReactNode;
}

function CategoryGroupedList<T>({ items, groupBy, renderItem }: CategoryGroupedListProps<T>) {
    const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

    // Group items by category
    const grouped = items.reduce(
        (acc, item) => {
            const category = groupBy(item);
            if (!acc[category]) {
                acc[category] = [];
            }
            acc[category].push(item);
            return acc;
        },
        {} as Record<string, T[]>
    );

    const categories = Object.keys(grouped).sort();

    const toggleCategory = (category: string) => {
        setCollapsed((prev) => ({
            ...prev,
            [category]: !prev[category],
        }));
    };

    return (
        <div className="space-y-2">
            {categories.map((category) => (
                <div key={category} className="border border-gray-300 dark:border-gray-700 rounded">
                    <button
                        onClick={() => toggleCategory(category)}
                        className="w-full px-4 py-2 flex items-center justify-between bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100 font-semibold"
                    >
                        <span>{category}</span>
                        <span className="text-gray-500 dark:text-gray-400">
                            {collapsed[category] ? "▶" : "▼"}
                        </span>
                    </button>
                    {!collapsed[category] && (
                        <div className="p-2 space-y-1">
                            {grouped[category].map((item, index) => (
                                <div key={index}>{renderItem(item)}</div>
                            ))}
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

export default CategoryGroupedList;
