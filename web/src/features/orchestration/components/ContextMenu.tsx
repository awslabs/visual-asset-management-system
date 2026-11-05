/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Z } from "./zLayers";
import { DisabledReasonTooltip } from "./DisabledReason";

export interface ContextMenuItem {
    label: string;
    onSelect: () => void;
    disabled?: boolean;
    /**
     * Why the item is disabled; shown in a tooltip on hover and focus and read out with the item,
     * which then stays reachable by keyboard. Ignored when enabled.
     */
    disabledReason?: string;
    hidden?: boolean;
    danger?: boolean;
    /**
     * Secondary text under the label, exposed as the item's accessible description: why the item is
     * disabled, or what it needs. The label alone stays the item's name.
     */
    hint?: string;
}

interface ContextMenuProps {
    items: ContextMenuItem[];
    trigger: React.ReactNode;
}

/**
 * Kebab action menu: the trigger control opens the menu on click. Backed by Radix
 * DropdownMenu so the three-dot trigger (RowActionsButton) behaves like a normal button.
 */
const ContextMenu: React.FC<ContextMenuProps> = ({ items, trigger }) => {
    const visibleItems = items.filter((item) => !item.hidden);
    const hintIdBase = useId();

    return (
        <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
            <DropdownMenu.Portal>
                <DropdownMenu.Content
                    align="end"
                    sideOffset={4}
                    // Shaded rather than the container background, which is the same colour as the
                    // page and row surfaces the menu opens over — it read as part of the table instead
                    // of as a floating layer. surface-secondary is the category-header grey, and
                    // resolves per theme through the same Cloudscape token (light #f6f6f9 / dark
                    // #1b232d), so no dark: variant is needed.
                    // Portalled to body, so a menu opened from inside a dialog or drawer is its
                    // SIBLING, not its child — z-index alone decides the order.
                    style={{ zIndex: Z.tooltip }}
                    className="orch-outline min-w-[200px] bg-surface-secondary rounded-md shadow-lg border border-border-default p-1"
                >
                    {visibleItems.map((item, idx) => {
                        // A disabled item that carries a reason stays in the keyboard order: Radix's
                        // `disabled` skips an item, which would leave its reason unreachable. It is
                        // marked aria-disabled instead, and selecting it does nothing and keeps the
                        // menu open.
                        const reason = item.disabled ? item.disabledReason : undefined;
                        const hintId = item.hint ? `${hintIdBase}-hint-${idx}` : undefined;
                        return (
                            <DisabledReasonTooltip key={idx} reason={reason}>
                                <DropdownMenu.Item
                                    onSelect={
                                        reason
                                            ? (event: Event) => event.preventDefault()
                                            : item.onSelect
                                    }
                                    disabled={item.disabled && !reason}
                                    aria-disabled={item.disabled || undefined}
                                    data-disabled={item.disabled ? "" : undefined}
                                    aria-description={reason}
                                    aria-label={item.hint ? item.label : undefined}
                                    aria-describedby={hintId}
                                    className={`px-3 py-2 text-sm rounded cursor-pointer outline-none ${
                                        item.disabled
                                            ? "text-text-disabled cursor-not-allowed"
                                            : item.danger
                                            ? "text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
                                            : // The overlay hover, not the normal one: against the shaded
                                              // menu background the normal hover is only ~1.03:1 and
                                              // reads as no hover at all.
                                              "text-text-primary hover:bg-surface-overlay-hover"
                                    }`}
                                >
                                    {item.label}
                                    {item.hint && (
                                        <span
                                            id={hintId}
                                            className={`block text-xs font-normal ${
                                                item.disabled
                                                    ? "text-text-disabled"
                                                    : "text-text-secondary"
                                            }`}
                                        >
                                            {item.hint}
                                        </span>
                                    )}
                                </DropdownMenu.Item>
                            </DisabledReasonTooltip>
                        );
                    })}
                </DropdownMenu.Content>
            </DropdownMenu.Portal>
        </DropdownMenu.Root>
    );
};

export default ContextMenu;
