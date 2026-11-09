/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef } from "react";
import { BusyButton } from "./BusyControls";
import { useFocusHandOff } from "./useFocusHandOff";

const TABBABLE = 'a[href], button, input:not([type="hidden"]), select, textarea, [tabindex]';

/** The elements the Tab key reaches, in document order. */
const tabbables = (): HTMLElement[] =>
    Array.from(document.querySelectorAll<HTMLElement>(TABBABLE)).filter(
        (element) =>
            element.tabIndex >= 0 &&
            !element.matches(":disabled") &&
            !element.closest("[hidden], [inert], [aria-hidden='true']")
    );

/** The element the Tab key reaches before `element`; null when there is none. */
const tabbableBefore = (element: HTMLElement): HTMLElement | null => {
    const all = tabbables();
    const index = all.indexOf(element);
    return index > 0 ? all[index - 1] : null;
};

/** The element the Tab key reaches after `anchor`, outside it; null when there is none. */
const tabbableAfter = (anchor: HTMLElement): HTMLElement | null =>
    tabbables().find(
        (element) =>
            !anchor.contains(element) &&
            !!(anchor.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
    ) ?? null;

interface LoadMoreButtonProps {
    onClick: () => void;
    /** Whether the next page is being read. */
    busy: boolean;
    /** Unavailable for a reason other than a read in flight. */
    disabled?: boolean;
    className?: string;
    /** What the button reads while busy; "Loading more..." without it. */
    busyLabel?: string;
    "data-testid"?: string;
    children?: React.ReactNode;
}

/**
 * "Load more" for a paged list. While the next page is read it keeps focus (see `BusyButton`). The
 * list renders it only while a page remains, so the answer with the last page takes it off the page;
 * when it holds focus then, focus moves to what the Tab key reaches after the control that preceded
 * it at the press (the first row loaded, when rows take focus, or else the control after the list)
 * rather than to the page body. A list that re-creates that control with the answer leaves focus to
 * the browser.
 */
const LoadMoreButton: React.FC<LoadMoreButtonProps> = ({
    onClick,
    busy,
    disabled,
    className,
    busyLabel = "Loading more...",
    "data-testid": testId,
    children = "Load more",
}) => {
    // The control before the button at the last press, which the rows the press loads follow.
    const anchorRef = useRef<HTMLElement | null>(null);
    const ref = useFocusHandOff<HTMLButtonElement>(
        () => {
            const anchor = anchorRef.current;
            return anchor?.isConnected ? tabbableAfter(anchor) ?? anchor : null;
        },
        (leaving) => {
            if (!anchorRef.current?.isConnected) anchorRef.current = tabbableBefore(leaving);
        }
    );
    return (
        <BusyButton
            ref={ref}
            busy={busy}
            busyLabel={busyLabel}
            disabled={disabled}
            onClick={(event) => {
                anchorRef.current = tabbableBefore(event.currentTarget);
                onClick();
            }}
            className={className}
            data-testid={testId}
        >
            {children}
        </BusyButton>
    );
};

export default LoadMoreButton;
