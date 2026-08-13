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
                {/* z-index sits ABOVE the app's fixed TopNavigation header (z-index 2000 in
                    header.scss); at a lower z the dialog rendered UNDER the header bar. */}
                <RadixDialog.Overlay className="fixed inset-0 bg-black/50 dark:bg-black/70 z-[3000]" />
                <RadixDialog.Content className="orchestration-root fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-surface-container rounded-lg shadow-xl max-w-2xl w-full max-h-[85vh] overflow-auto z-[3001] p-6">
                    <RadixDialog.Title className="text-xl font-semibold text-text-primary mb-4">
                        {title}
                    </RadixDialog.Title>
                    <div className="text-text-primary">{children}</div>
                    {footer && <div className="mt-6 flex justify-end gap-2">{footer}</div>}
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
