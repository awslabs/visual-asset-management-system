/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useLayoutEffect, useState } from "react";

type Box = Pick<DOMRectReadOnly, "left" | "right" | "top" | "bottom">;

/** Whether two boxes share some area; boxes that only touch, or have no area, do not overlap */
export const boxesOverlap = (a: Box, b: Box): boolean =>
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

/**
 * Whether two elements overlap on screen, measured from their rectangles. It is measured after every
 * render of the caller, when either element or the element it sits in changes size, and when the
 * window does, so it follows the container's width (the window, the wide and fullscreen modes) and
 * the elements' own content. False while either element is not mounted.
 *
 * Pass elements held in state, set by a callback ref (`ref={setElement}`), so the measuring starts
 * when an element mounts and stops when it unmounts.
 */
export function useOverlap(first: Element | null, second: Element | null): boolean {
    const [overlap, setOverlap] = useState(false);

    const measure = useCallback(() => {
        setOverlap(
            !!first &&
                !!second &&
                boxesOverlap(first.getBoundingClientRect(), second.getBoundingClientRect())
        );
    }, [first, second]);

    // After every render, since the caller's content can move or resize either element
    useLayoutEffect(() => {
        measure();
    });

    useLayoutEffect(() => {
        if (!first || !second) return;
        window.addEventListener("resize", measure);
        let observer: ResizeObserver | null = null;
        if (typeof ResizeObserver !== "undefined") {
            const resizeObserver = new ResizeObserver(() => measure());
            [first, second, first.parentElement, second.parentElement].forEach((element) => {
                if (element) resizeObserver.observe(element);
            });
            observer = resizeObserver;
        }
        return () => {
            window.removeEventListener("resize", measure);
            observer?.disconnect();
        };
    }, [first, second, measure]);

    return overlap;
}

/**
 * A viewer's side panel and its info box (viewer name, controls hint and scene stats). The info box
 * is drawn only while the open panel does not overlap it, so on a narrow container the panel reads on
 * its own, and the info box shows when there is room beside the panel or the panel is hidden. Put
 * `panelRef` on the panel's root element and `infoBoxRef` and `infoBoxVisibility` on the info box.
 * A hidden info box keeps its place in the layout, so it is still measured.
 */
export function useInfoBoxClearOfPanel() {
    const [panel, setPanel] = useState<HTMLElement | null>(null);
    const [infoBox, setInfoBox] = useState<HTMLElement | null>(null);
    const covered = useOverlap(panel, infoBox);
    return {
        panelRef: setPanel,
        infoBoxRef: setInfoBox,
        infoBoxVisibility: covered ? ("hidden" as const) : ("visible" as const),
    };
}
