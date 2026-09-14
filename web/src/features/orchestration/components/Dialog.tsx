/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { createPortal } from "react-dom";
import * as RadixDialog from "@radix-ui/react-dialog";

interface DialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
    /** `md` is the confirm/form width; `lg` is the multi-column width the execute dialog uses. */
    size?: "md" | "lg";
}

const SIZE_CLASSES: Record<NonNullable<DialogProps["size"]>, string> = {
    md: "max-w-2xl",
    lg: "max-w-5xl",
};

const FooterSlotContext = React.createContext<HTMLDivElement | null>(null);

/**
 * Renders its children into the enclosing Dialog's fixed footer row. For a child whose footer
 * depends on its own state (a wizard's Back/Next/Launch); callers with a static footer pass the
 * `footer` prop instead. Renders nothing outside a Dialog.
 */
export const DialogFooter: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const node = React.useContext(FooterSlotContext);
    if (!node) return null;
    return createPortal(children, node);
};

const Dialog: React.FC<DialogProps> = ({
    open,
    onOpenChange,
    title,
    children,
    footer,
    size = "md",
}) => {
    // State rather than a ref: the slot node exists only after the first commit, and a state update
    // there re-renders the portal target in the same commit, so a child's footer is on the first paint.
    const [footerNode, setFooterNode] = React.useState<HTMLDivElement | null>(null);
    return (
        <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
            <RadixDialog.Portal>
                {/* z-index sits ABOVE the app's fixed TopNavigation header (z-index 2000 in
                    header.scss); at a lower z the dialog rendered UNDER the header bar. */}
                <RadixDialog.Overlay className="fixed inset-0 bg-black/50 dark:bg-black/70 z-[3000]" />
                {/* A column: fixed title, a body that takes the remaining height and scrolls, fixed
                    footer — so navigation buttons never scroll off screen with a long step. */}
                <RadixDialog.Content
                    className={`orchestration-root fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex max-h-[85vh] w-full flex-col rounded-lg bg-surface-container shadow-xl z-[3001] ${SIZE_CLASSES[size]}`}
                >
                    <RadixDialog.Title className="px-6 pt-6 pb-3 text-xl font-semibold text-text-primary">
                        {title}
                    </RadixDialog.Title>
                    <FooterSlotContext.Provider value={footerNode}>
                        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-4 text-text-primary">
                            {children}
                        </div>
                    </FooterSlotContext.Provider>
                    {/* Hidden while nothing renders into it, so a footer-less dialog has no empty row. */}
                    <div
                        ref={setFooterNode}
                        data-testid="dialog-footer"
                        className="flex justify-end gap-2 px-6 pb-6 pt-2 empty:hidden"
                    >
                        {footer}
                    </div>
                    <RadixDialog.Close
                        aria-label="Close dialog"
                        className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
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

export default Dialog;
