/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import * as RadixDialog from "@radix-ui/react-dialog";

interface DialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
}

const Dialog: React.FC<DialogProps> = ({ open, onOpenChange, title, children, footer }) => {
    return (
        <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
            <RadixDialog.Portal>
                <RadixDialog.Overlay className="fixed inset-0 bg-black/50 dark:bg-black/70 z-40" />
                <RadixDialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-2xl w-full max-h-[85vh] overflow-auto z-50 p-6">
                    <RadixDialog.Title className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">
                        {title}
                    </RadixDialog.Title>
                    <div className="text-gray-700 dark:text-gray-300">{children}</div>
                    {footer && <div className="mt-6 flex justify-end gap-2">{footer}</div>}
                    <RadixDialog.Close className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
                        <span className="text-xl">×</span>
                    </RadixDialog.Close>
                </RadixDialog.Content>
            </RadixDialog.Portal>
        </RadixDialog.Root>
    );
};

export default Dialog;
