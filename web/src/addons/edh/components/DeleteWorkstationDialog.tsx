/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import { btnDanger, btnSecondary } from "../../../features/orchestration/components/controlStyles";

interface DeleteWorkstationDialogProps {
    workstationName: string;
    /** Whether the row carries an EDH session; a session-less launch has no desktop to destroy. */
    hasSession: boolean;
    open: boolean;
    /** Disables the confirm control while the delete request is in flight. */
    busy?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
}

const DeleteWorkstationDialog: React.FC<DeleteWorkstationDialogProps> = ({
    workstationName,
    hasSession,
    open,
    busy = false,
    onConfirm,
    onCancel,
}) => {
    // Deleting a row with a session destroys its desktop; deleting a launch that never received one
    // (ORPHANED, or a CREATING row the service has given up on) removes only the launch record.
    const consequence = hasSession
        ? "The workstation and its root disk are destroyed."
        : "This launch has no EDH session; the launch record is removed.";

    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onCancel()}
            title={`Delete ${workstationName}`}
            footer={
                <>
                    <button onClick={onCancel} className={btnSecondary}>
                        Cancel
                    </button>
                    <button onClick={onConfirm} disabled={busy} className={btnDanger}>
                        {busy ? "Deleting..." : "Delete"}
                    </button>
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
