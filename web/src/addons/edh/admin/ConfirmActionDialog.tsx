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
import { BusyButton } from "../../../features/orchestration/components/BusyControls";
import {
    useReturnFocus,
    type ReturnFocusTo,
} from "../../../features/orchestration/components/useReturnFocus";

export {
    controlNamed,
    rowActionsFor,
    rowActionsTrigger,
} from "../../../features/orchestration/components/useReturnFocus";

interface ConfirmActionDialogProps {
    open: boolean;
    title: string;
    message: React.ReactNode;
    confirmLabel: string;
    /** Whether the confirmed action's request is in flight. */
    busy?: boolean;
    /** What the confirm button reads while busy, naming the action under way ("Deleting..."). */
    busyLabel?: string;
    /** Whether the action destroys something; red is reserved for those, a reversible one confirms in the affirmative blue. */
    destructive?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
    /**
     * Picks the element focus returns to when the dialog closes (see `ReturnFocusTo`). Without it,
     * or when the element it names is not on the page, focus returns to the opener.
     */
    returnFocusTo?: ReturnFocusTo;
}

/**
 * Confirmation for an admin action (delete an image, stop or deactivate a session, kill a job). The
 * dialogs open from a controlled `open`, with no Radix trigger to restore focus to, so the dialog
 * notes the element that had focus as it opened and returns focus there as it closes, whether by
 * Escape, Cancel, the close button or the caller once the action is answered. While the action is
 * in flight the dialog stays open, its buttons keep focus and the confirm button reads `busyLabel`.
 */
const ConfirmActionDialog: React.FC<ConfirmActionDialogProps> = ({
    open,
    title,
    message,
    confirmLabel,
    busy = false,
    busyLabel,
    destructive = true,
    onConfirm,
    onCancel,
    returnFocusTo,
}) => {
    const returnFocusRef = useReturnFocus(open, returnFocusTo);
    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onCancel()}
            busy={busy}
            title={title}
            returnFocusRef={returnFocusRef}
            footer={
                <>
                    <BusyButton busy={busy} onClick={onCancel} className={btnSecondary}>
                        Cancel
                    </BusyButton>
                    <BusyButton
                        busy={busy}
                        busyLabel={busyLabel}
                        onClick={onConfirm}
                        className={destructive ? btnDanger : btnPrimary}
                    >
                        {confirmLabel}
                    </BusyButton>
                </>
            }
        >
            <div className="text-text-primary">{message}</div>
        </Dialog>
    );
};

export default ConfirmActionDialog;
