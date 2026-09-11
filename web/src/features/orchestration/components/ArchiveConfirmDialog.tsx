/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Dialog from "./Dialog";

interface ArchiveConfirmDialogProps {
    entityName: string;
    open: boolean;
    onConfirm: () => void;
    onCancel: () => void;
}

const ArchiveConfirmDialog: React.FC<ArchiveConfirmDialogProps> = ({
    entityName,
    open,
    onConfirm,
    onCancel,
}) => {
    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onCancel()}
            title={`Archive ${entityName}`}
            footer={
                <>
                    <button
                        onClick={onCancel}
                        className="px-4 py-2 bg-gray-200 text-gray-800 rounded hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={onConfirm}
                        className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600"
                    >
                        Archive
                    </button>
                </>
            }
        >
            <p className="text-text-primary">
                Are you sure you want to archive <strong>{entityName}</strong>? This action can be
                undone by including archived items and unarchiving.
            </p>
        </Dialog>
    );
};

export default ArchiveConfirmDialog;
