/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import { btnDanger, btnSecondary } from "../../../features/orchestration/components/controlStyles";
import { BusyButton } from "../../../features/orchestration/components/BusyControls";
import {
    useReturnFocus,
    type ReturnFocusTo,
} from "../../../features/orchestration/components/useReturnFocus";

interface DeleteWorkstationDialogProps {
    workstationName: string;
    /** Whether the row carries an EDH session; a session-less launch has no desktop to destroy. */
    hasSession: boolean;
    open: boolean;
    /**
     * Whether the delete request is in flight: the dialog stays open, its buttons keep focus and
     * ignore a click, and Delete reads "Deleting...".
     */
    busy?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
    /** Picks the element focus returns to as the dialog closes; the opener without it. */
    returnFocusTo?: ReturnFocusTo;
}

const DeleteWorkstationDialog: React.FC<DeleteWorkstationDialogProps> = ({
    workstationName,
    hasSession,
    open,
    busy = false,
    onConfirm,
    onCancel,
    returnFocusTo,
}) => {
    const returnFocusRef = useReturnFocus(open, returnFocusTo);
    // Deleting a row with a session destroys its desktop; deleting a launch that never received one
    // (ORPHANED, or a CREATING row the service has given up on) removes only the launch record.
    const consequence = hasSession
        ? "The workstation and its root disk are destroyed."
        : "This launch has no EDH session; the launch record is removed.";

    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onCancel()}
            busy={busy}
            title={`Delete ${workstationName}`}
            returnFocusRef={returnFocusRef}
            footer={
                <>
                    <BusyButton busy={busy} onClick={onCancel} className={btnSecondary}>
                        Cancel
                    </BusyButton>
                    <BusyButton
                        busy={busy}
                        busyLabel="Deleting..."
                        onClick={onConfirm}
                        className={btnDanger}
                    >
                        Delete
                    </BusyButton>
                </>
            }
        >
            <p className="text-text-primary">
                Are you sure you want to delete <strong>{workstationName}</strong>? {consequence}{" "}
                This cannot be undone.
            </p>
        </Dialog>
    );
};

export default DeleteWorkstationDialog;
