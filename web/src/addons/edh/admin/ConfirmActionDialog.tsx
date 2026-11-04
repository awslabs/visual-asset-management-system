/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useLayoutEffect, useMemo, useRef } from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import {
    btnDanger,
    btnPrimary,
    btnSecondary,
} from "../../../features/orchestration/components/controlStyles";

/**
 * The attribute an EDH row's actions trigger carries, holding the row's id. A confirmation opened
 * from the row's menu finds the trigger by it, since the menu item that opened the dialog has closed
 * with its menu by then.
 */
const ROW_ACTIONS_FOR = "data-row-actions-for";

/** The props that mark a row's actions trigger as the one of row `rowId`. */
export const rowActionsFor = (rowId: string): { [ROW_ACTIONS_FOR]: string } => ({
    [ROW_ACTIONS_FOR]: rowId,
});

/** The actions trigger of row `rowId` inside `container`; null when that row is not on the page. */
export const rowActionsTrigger = (
    container: HTMLElement | null,
    rowId: string
): HTMLElement | null =>
    Array.from(container?.querySelectorAll<HTMLElement>(`[${ROW_ACTIONS_FOR}]`) ?? []).find(
        (element) => element.getAttribute(ROW_ACTIONS_FOR) === rowId
    ) ?? null;

/** The control inside `container` whose `aria-label` is `name`; null when there is none. */
export const controlNamed = (container: HTMLElement | null, name: string): HTMLElement | null =>
    Array.from(container?.querySelectorAll<HTMLElement>("[aria-label]") ?? []).find(
        (element) => element.getAttribute("aria-label") === name
    ) ?? null;

/** The element itself when focus can return to it: on the page, and not the page body. */
const onPage = (element: Element | null | undefined): HTMLElement | null =>
    element instanceof HTMLElement && element.isConnected && element !== document.body
        ? element
        : null;

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
    /**
     * Picks the element focus returns to when the dialog closes. It is given the element that
     * opened the dialog, or null once that element has left the page, as a row menu's item does
     * when its menu closes, and it is called only as the dialog closes, so it can answer from what
     * the action did: a row the action removed hands focus to a control the list keeps. Without
     * it, or when the element it names is not on the page, focus returns to the opener.
     */
    returnFocusTo?: (opener: HTMLElement | null) => HTMLElement | null | undefined;
}

/**
 * Confirmation for an admin action (delete an image, stop or deactivate a session, kill a job). The
 * dialogs open from a controlled `open`, with no Radix trigger to restore focus to, so the dialog
 * notes the element that had focus as it opened and returns focus there as it closes, whether by
 * Escape, Cancel, the close button or the caller once the action is answered.
 */
const ConfirmActionDialog: React.FC<ConfirmActionDialogProps> = ({
    open,
    title,
    message,
    confirmLabel,
    busy = false,
    destructive = true,
    onConfirm,
    onCancel,
    returnFocusTo,
}) => {
    const openerRef = useRef<HTMLElement | null>(null);
    const returnFocusToRef = useRef(returnFocusTo);
    returnFocusToRef.current = returnFocusTo;
    // A layout effect runs before the dialog takes focus, so the opener still holds it.
    useLayoutEffect(() => {
        if (open) openerRef.current = onPage(document.activeElement);
    }, [open]);
    // Dialog reads `current` once, as the dialog closes, so the answer reflects the page as the
    // caller has re-rendered it.
    const returnFocusRef = useMemo<React.RefObject<HTMLElement>>(
        () => ({
            get current() {
                const opener = onPage(openerRef.current);
                return onPage(returnFocusToRef.current?.(opener)) ?? opener;
            },
        }),
        []
    );
    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onCancel()}
            title={title}
            returnFocusRef={returnFocusRef}
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
};

export default ConfirmActionDialog;
