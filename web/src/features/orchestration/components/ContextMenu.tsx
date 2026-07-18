/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as RadixContextMenu from "@radix-ui/react-context-menu";

export interface ContextMenuItem {
    label: string;
    onSelect: () => void;
    disabled?: boolean;
    hidden?: boolean;
    danger?: boolean;
}

interface ContextMenuProps {
    items: ContextMenuItem[];
    trigger: React.ReactNode;
}

const ContextMenu: React.FC<ContextMenuProps> = ({ items, trigger }) => {
    const visibleItems = items.filter((item) => !item.hidden);

    return (
        <RadixContextMenu.Root>
            <RadixContextMenu.Trigger asChild>{trigger}</RadixContextMenu.Trigger>
            <RadixContextMenu.Portal>
                <RadixContextMenu.Content className="min-w-[200px] bg-white dark:bg-gray-800 rounded-md shadow-lg border border-gray-200 dark:border-gray-700 p-1 z-50">
                    {visibleItems.map((item, idx) => (
                        <RadixContextMenu.Item
                            key={idx}
                            onSelect={item.onSelect}
                            disabled={item.disabled}
                            className={`px-3 py-2 text-sm rounded cursor-pointer outline-none ${
                                item.disabled
                                    ? "text-gray-400 dark:text-gray-600 cursor-not-allowed"
                                    : item.danger
                                      ? "text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
                                      : "text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                            }`}
                        >
                            {item.label}
                        </RadixContextMenu.Item>
                    ))}
                </RadixContextMenu.Content>
            </RadixContextMenu.Portal>
        </RadixContextMenu.Root>
    );
};

export default ContextMenu;
