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
}

const Drawer: React.FC<DrawerProps> = ({ open, onOpenChange, title, children, side = "right" }) => {
    const sideClasses =
        side === "left"
            ? "left-0 top-0 h-full"
            : "right-0 top-0 h-full";

    return (
        <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
            <RadixDialog.Portal>
                <RadixDialog.Overlay className="fixed inset-0 bg-black/50 dark:bg-black/70 z-40" />
                <RadixDialog.Content
                    className={`fixed ${sideClasses} bg-white dark:bg-gray-900 shadow-xl w-full max-w-md overflow-auto z-50 p-6`}
                >
                    <RadixDialog.Title className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">
                        {title}
                    </RadixDialog.Title>
                    <div className="text-gray-700 dark:text-gray-300">{children}</div>
                    <RadixDialog.Close className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
                        <span className="text-xl">×</span>
                    </RadixDialog.Close>
                </RadixDialog.Content>
            </RadixDialog.Portal>
        </RadixDialog.Root>
    );
};

export default Drawer;
