/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as RadixDialog from "@radix-ui/react-dialog";

interface DrawerProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    children: React.ReactNode;
    side?: "left" | "right";
    /** Max-width utility for the panel. Override when the content is wide (long ids, table columns). */
    maxWidthClass?: string;
}

const Drawer: React.FC<DrawerProps> = ({
    open,
    onOpenChange,
    title,
    children,
    side = "right",
    maxWidthClass = "max-w-md",
}) => {
    const sideClasses = side === "left" ? "left-0 top-0 h-full" : "right-0 top-0 h-full";

    return (
        <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
            <RadixDialog.Portal>
                {/* z-index sits ABOVE the app's fixed TopNavigation header (z-index 2000 in
                    header.scss); at a lower z the drawer rendered UNDER the header bar. */}
                <RadixDialog.Overlay className="fixed inset-0 bg-black/50 dark:bg-black/70 z-[3000]" />
                <RadixDialog.Content
                    className={`orchestration-root fixed ${sideClasses} bg-surface-container shadow-xl w-full ${maxWidthClass} overflow-auto z-[3001] p-6`}
                >
                    <RadixDialog.Title className="text-xl font-semibold text-text-primary mb-4">
                        {title}
                    </RadixDialog.Title>
                    <div className="text-text-primary">{children}</div>
                    <RadixDialog.Close className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
                        <span className="text-xl">×</span>
                    </RadixDialog.Close>
                </RadixDialog.Content>
            </RadixDialog.Portal>
        </RadixDialog.Root>
    );
};

export default Drawer;
