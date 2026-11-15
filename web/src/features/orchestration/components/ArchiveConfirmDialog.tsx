/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Dialog from "./Dialog";
import { BusyButton } from "./BusyControls";
import type { ReturnFocusTo } from "./useReturnFocus";

interface ArchiveConfirmDialogProps {
    entityName: string;
    open: boolean;
    /** Whether the archive request is in flight: the dialog stays open and its buttons keep focus. */
    busy?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
    /**
     * Where focus returns as the dialog closes: the row's actions button, since the menu item that
     * opened the dialog has closed with its menu, or a control the list keeps once the archive took
     * the row off it (see `useReturnFocus`).
     */
    returnFocusTo?: ReturnFocusTo;
}

const ArchiveConfirmDialog: React.FC<ArchiveConfirmDialogProps> = ({
    entityName,
    open,
    busy = false,
    onConfirm,
    onCancel,
    returnFocusTo,
}) => {
    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onCancel()}
            title={`Archive ${entityName}`}
            busy={busy}
            returnFocusTo={returnFocusTo}
            footer={
                <>
                    <BusyButton
                        busy={busy}
                        onClick={onCancel}
                        className="px-4 py-2 bg-gray-200 text-gray-800 rounded hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600"
                    >
                        Cancel
                    </BusyButton>
                    <BusyButton
                        busy={busy}
                        busyLabel="Archiving..."
                        onClick={onConfirm}
                        className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600"
                    >
                        Archive
                    </BusyButton>
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
