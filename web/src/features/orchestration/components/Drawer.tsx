/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as RadixDialog from "@radix-ui/react-dialog";
import { useReturnFocus, type ReturnFocusTo } from "./useReturnFocus";

interface DrawerProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    children: React.ReactNode;
    side?: "left" | "right";
    /** Max-width utility for the panel. Override when the content is wide (long ids, table columns). */
    maxWidthClass?: string;
    /**
     * Picks the element focus returns to when the panel closes and its opener cannot take focus back
     * (a row menu's item, which has closed with its menu). Without it focus returns to the opener.
     */
    returnFocusTo?: ReturnFocusTo;
}

const Drawer: React.FC<DrawerProps> = ({
    open,
    onOpenChange,
    title,
    children,
    side = "right",
    maxWidthClass = "max-w-md",
    returnFocusTo,
}) => {
    const sideClasses = side === "left" ? "left-0 top-0 h-full" : "right-0 top-0 h-full";
    const contentRef = React.useRef<HTMLDivElement>(null);
    // Opened from a controlled `open`, the panel has no Radix trigger to restore focus to, so it
    // returns focus to the element that had it as the panel opened (see `useReturnFocus`).
    const returnFocusRef = useReturnFocus(open, returnFocusTo);

    return (
        <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
            <RadixDialog.Portal>
                {/* z-index sits ABOVE the app's fixed TopNavigation header (z-index 2000 in
                    header.scss); at a lower z the drawer rendered UNDER the header bar. */}
                <RadixDialog.Overlay className="fixed inset-0 bg-black/50 dark:bg-black/70 z-[3000]" />
                <RadixDialog.Content
                    ref={contentRef}
                    tabIndex={-1}
                    // Radix would focus the panel's first tabbable element. In these read-only
                    // panels that is often an info-tooltip trigger, and a tooltip opens on focus —
                    // as the topmost dismissable layer it would then take the first Escape, leaving
                    // the panel open until a second press. Focusing the panel itself keeps focus
                    // inside the dialog without opening anything.
                    onOpenAutoFocus={(event) => {
                        event.preventDefault();
                        contentRef.current?.focus();
                    }}
                    onCloseAutoFocus={(event) => {
                        const target = returnFocusRef.current;
                        if (!target) return;
                        event.preventDefault();
                        target.focus();
                    }}
                    className={`orchestration-root fixed ${sideClasses} bg-surface-container shadow-xl w-full ${maxWidthClass} overflow-auto z-[3001] p-6 focus:outline-none`}
                >
                    <RadixDialog.Title className="text-xl font-semibold text-text-primary mb-4">
                        {title}
                    </RadixDialog.Title>
                    <div className="text-text-primary">{children}</div>
                    <RadixDialog.Close
                        aria-label="Close panel"
                        className="absolute top-4 right-4 text-text-secondary hover:text-text-primary"
                    >
                        <span aria-hidden="true" className="text-xl">
                            ×
                        </span>
                    </RadixDialog.Close>
                </RadixDialog.Content>
            </RadixDialog.Portal>
        </RadixDialog.Root>
    );
};

export default Drawer;
