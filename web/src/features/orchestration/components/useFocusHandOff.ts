/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useRef } from "react";

/**
 * A ref for a control, or a form, that the answer to its own request can take off the page (a Save
 * the form replaces, a Launch the wizard swaps for Close, a Load more the last page retires). The
 * browser moves focus from a removed element to the page body; when the element leaves while it, or
 * an element inside it, holds focus, focus moves instead to the element `target` names, looked up
 * once the commit that removed the element has placed what replaced it. It names none, and focus is
 * left alone, when the answer also closes the surface (a dialog returning focus itself, a page
 * navigated away). `onLeave` sees the element while it is still on the page.
 */
export function useFocusHandOff<T extends HTMLElement>(
    target: () => HTMLElement | null | undefined,
    onLeave?: (leaving: T) => void
): (node: T | null) => void {
    const targetRef = useRef(target);
    targetRef.current = target;
    const onLeaveRef = useRef(onLeave);
    onLeaveRef.current = onLeave;
    const nodeRef = useRef<T | null>(null);
    // React detaches a ref before it removes the element, so the element still holds focus here.
    return useCallback((node: T | null) => {
        const leaving = nodeRef.current;
        nodeRef.current = node;
        if (node || !leaving || !leaving.contains(document.activeElement)) return;
        onLeaveRef.current?.(leaving);
        queueMicrotask(() => {
            const next = targetRef.current();
            if (next?.isConnected) next.focus();
        });
    }, []);
}
