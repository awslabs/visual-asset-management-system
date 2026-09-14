/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

export interface VirtualListProps<T> {
    items: T[];
    /** Every row is exactly this tall, which is what lets the window be computed without measuring. */
    rowHeight: number;
    /** The scroll viewport's height; a list whose rows fit is shorter than this. */
    height: number;
    /** Rows rendered beyond each edge of the viewport, so a scroll never shows a blank strip. */
    overscan?: number;
    renderRow: (item: T, index: number) => React.ReactNode;
    rowKey: (item: T, index: number) => string | number;
    className?: string;
    /** ARIA on the scroll container; rows carry `listitem` when it is a `list`. */
    role?: "list" | "listbox" | "group";
    "aria-label"?: string;
    testId?: string;
}

/**
 * A fixed-row-height windowed list: only the rows inside the viewport (plus `overscan` on each side)
 * are mounted, whatever `items.length` is. Rows are absolutely positioned inside a spacer of the full
 * height, so the scrollbar reads the true length and a jump lands on the right row.
 *
 * The container is focusable so the keyboard scrolls it (arrows, Page Up/Down, Home/End) and the
 * controls inside the mounted rows stay reachable with Tab.
 */
function VirtualList<T>({
    items,
    rowHeight,
    height,
    overscan = 5,
    renderRow,
    rowKey,
    className = "",
    role = "list",
    "aria-label": ariaLabel,
    testId = "virtual-list",
}: VirtualListProps<T>) {
    const [scrollTop, setScrollTop] = React.useState(0);
    const containerRef = React.useRef<HTMLDivElement>(null);

    const total = items.length * rowHeight;
    const viewport = Math.min(height, total);
    const maxScroll = Math.max(0, total - viewport);

    // A shrinking list can leave the offset past its new end; pull both the state and the element back.
    React.useEffect(() => {
        if (scrollTop > maxScroll) {
            setScrollTop(maxScroll);
            if (containerRef.current) containerRef.current.scrollTop = maxScroll;
        }
    }, [scrollTop, maxScroll]);

    const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
    const end = Math.min(items.length, Math.ceil((scrollTop + height) / rowHeight) + overscan);
    const rowRole = role === "list" ? "listitem" : role === "listbox" ? "option" : undefined;

    return (
        <div
            ref={containerRef}
            role={role}
            aria-label={ariaLabel}
            data-testid={testId}
            tabIndex={0}
            onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
            style={{ height: viewport, overflowY: "auto" }}
            className={`relative focus:outline-none focus:ring-2 focus:ring-blue-500 ${className}`}
        >
            <div style={{ height: total, position: "relative" }}>
                {items.slice(start, end).map((item, offset) => {
                    const index = start + offset;
                    return (
                        <div
                            key={rowKey(item, index)}
                            role={rowRole}
                            data-testid={`${testId}-row`}
                            style={{
                                position: "absolute",
                                top: index * rowHeight,
                                height: rowHeight,
                                left: 0,
                                right: 0,
                            }}
                        >
                            {renderRow(item, index)}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

export default VirtualList;
