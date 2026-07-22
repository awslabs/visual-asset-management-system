/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";

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

/**
 * Kebab action menu: the trigger control opens the menu on click. Backed by Radix
 * DropdownMenu so the visible ⋮/⋯ affordance behaves like a normal button.
 */
const ContextMenu: React.FC<ContextMenuProps> = ({ items, trigger }) => {
    const visibleItems = items.filter((item) => !item.hidden);

    return (
        <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
            <DropdownMenu.Portal>
                <DropdownMenu.Content
                    align="end"
                    sideOffset={4}
                    className="min-w-[200px] bg-surface-container rounded-md shadow-lg border border-border-default p-1 z-50"
                >
                    {visibleItems.map((item, idx) => (
                        <DropdownMenu.Item
                            key={idx}
                            onSelect={item.onSelect}
                            disabled={item.disabled}
                            className={`px-3 py-2 text-sm rounded cursor-pointer outline-none ${
                                item.disabled
                                    ? "text-text-disabled cursor-not-allowed"
                                    : item.danger
                                    ? "text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
                                    : "text-text-primary hover:bg-surface-hover"
                            }`}
                        >
                            {item.label}
                        </DropdownMenu.Item>
                    ))}
                </DropdownMenu.Content>
            </DropdownMenu.Portal>
        </DropdownMenu.Root>
    );
};

export default ContextMenu;
