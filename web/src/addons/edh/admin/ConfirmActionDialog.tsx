/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import {
    btnDanger,
    btnPrimary,
    btnSecondary,
} from "../../../features/orchestration/components/controlStyles";

interface ConfirmActionDialogProps {
    open: boolean;
    title: string;
    message: React.ReactNode;
    confirmLabel: string;
    busy?: boolean;
    /** Whether the action destroys something; red is reserved for those, a reversible one confirms in the affirmative blue. */
    destructive?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
}

/** Confirmation for an admin action (delete an image, stop or deactivate a session, kill a job). */
const ConfirmActionDialog: React.FC<ConfirmActionDialogProps> = ({
    open,
    title,
    message,
    confirmLabel,
    busy = false,
    destructive = true,
    onConfirm,
    onCancel,
}) => (
    <Dialog
        open={open}
        onOpenChange={(isOpen) => !isOpen && onCancel()}
        title={title}
        footer={
            <>
                <button type="button" onClick={onCancel} className={btnSecondary}>
                    Cancel
                </button>
                <button
                    type="button"
                    onClick={onConfirm}
                    disabled={busy}
                    className={destructive ? btnDanger : btnPrimary}
                >
                    {confirmLabel}
                </button>
            </>
        }
    >
        <div className="text-text-primary">{message}</div>
    </Dialog>
);

export default ConfirmActionDialog;
