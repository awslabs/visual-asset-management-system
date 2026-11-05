/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useLayoutEffect, useMemo, useRef, type MutableRefObject, type RefObject } from "react";

/**
 * The attribute a row's actions trigger carries, holding the row's id. A dialog opened from the
 * row's menu finds the trigger by it, since the menu item that opened the dialog has closed with its
 * menu by then.
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

/**
 * The element itself when focus can move to it: on the page, not the page body, and not disabled
 * (focusing a disabled control does nothing).
 */
const focusable = (element: Element | null | undefined): HTMLElement | null =>
    element instanceof HTMLElement &&
    element.isConnected &&
    element !== document.body &&
    !element.matches(":disabled")
        ? element
        : null;

/**
 * Picks the element focus returns to as a dialog closes. It is given the element that opened the
 * dialog, or null once that element has left the page, as a row menu's item does when its menu
 * closes, and it is called only as the dialog closes, so it can answer from what the dialog did: a
 * row the action removed hands focus to a control the list keeps.
 */
export type ReturnFocusTo = (opener: HTMLElement | null) => HTMLElement | null | undefined;

/**
 * The note a row's dialog keeps of the row its action removed from the list, or may have removed
 * when the service did not confirm it, so that its `returnFocusTo` sends focus to a control the list
 * keeps rather than to the row's trigger. The handler sets `current` to the row's key as the action
 * is answered. `target` is what the dialog is open on: each time it names a row the note is
 * cleared, so a dialog reopened on a row the last action left listed returns focus to that row.
 */
export function useRemovedRowRef<K>(target: unknown): MutableRefObject<K | null> {
    const removedRef = useRef<K | null>(null);
    // A layout effect clears the note in the commit that opens the dialog, before it can close.
    useLayoutEffect(() => {
        if (target) removedRef.current = null;
    }, [target]);
    return removedRef;
}

/**
 * The `returnFocusRef` of a dialog opened from a controlled `open`, which has no Radix trigger to
 * restore focus to. It notes the element that had focus as the dialog opens, and as the dialog
 * closes (Escape, its Cancel or Close button, the close button, or the caller once the action is
 * answered) it names `returnFocusTo`'s choice, or the opener when there is no choice or the element
 * chosen cannot take focus. `Dialog` and `Drawer` call it themselves for a caller that passes no
 * `returnFocusRef`.
 */
export function useReturnFocus(
    open: boolean,
    returnFocusTo?: ReturnFocusTo
): RefObject<HTMLElement> {
    const openerRef = useRef<HTMLElement | null>(null);
    const returnFocusToRef = useRef(returnFocusTo);
    returnFocusToRef.current = returnFocusTo;
    // A layout effect runs before the dialog takes focus, so the opener still holds it.
    useLayoutEffect(() => {
        if (open) openerRef.current = focusable(document.activeElement);
    }, [open]);
    // The dialog reads `current` once, as it closes, so the answer reflects the page as the caller
    // has re-rendered it.
    return useMemo<RefObject<HTMLElement>>(
        () => ({
            get current() {
                const opener = focusable(openerRef.current);
                return focusable(returnFocusToRef.current?.(opener)) ?? opener;
            },
        }),
        []
    );
}
