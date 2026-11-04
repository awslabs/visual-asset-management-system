/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { GRIP_ICON_SVG } from "./GripIconSvg";
import styles from "./SceneTreeResizeHandle.module.css";

// The tree keeps at least MIN_TREE_HEIGHT and the details below it at least MIN_DETAILS_HEIGHT
const MIN_TREE_HEIGHT = 150;
const MIN_DETAILS_HEIGHT = 200;
// Height change per ArrowUp / ArrowDown key press, in pixels
const KEYBOARD_STEP = 20;

// Tallest tree a drag allows in a column of the given height
const dragLimit = (columnHeight: number) =>
    Math.max(MIN_TREE_HEIGHT, columnHeight - MIN_DETAILS_HEIGHT);

interface SceneTreeResizeHandleProps {
    /** Height of the scene tree above the handle, in pixels */
    height: number;
    /** Called with the new tree height while the handle is dragged or moved with the keyboard */
    onResize: (height: number) => void;
    /** id of the scene tree element the handle sizes */
    treeId: string;
}

/**
 * The divider between the scene tree and the details below it in the Three.js and Needle USD scene
 * graphs. It sits in one column with the tree and the details and takes its limits from that column
 * (its parent element).
 *
 * Pointer events cover mouse, pen and touch; capturing the pointer keeps the drag on the handle when
 * the pointer moves off it. As a focusable separator, ArrowUp / ArrowDown move it by KEYBOARD_STEP
 * and Home / End move it to its limits. A tree the selection made taller than a drag allows keeps
 * that height as the upper limit until the handle moves it down.
 */
const SceneTreeResizeHandle: React.FC<SceneTreeResizeHandleProps> = ({
    height,
    onResize,
    treeId,
}) => {
    const handleRef = useRef<HTMLDivElement>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [columnHeight, setColumnHeight] = useState(0);

    // Measures the column and keeps its height for aria-valuemax
    const measureColumn = useCallback((): DOMRect | null => {
        const column = handleRef.current?.parentElement;
        if (!column) return null;
        const rect = column.getBoundingClientRect();
        setColumnHeight(rect.height);
        return rect;
    }, []);

    useLayoutEffect(() => {
        measureColumn();
        const column = handleRef.current?.parentElement;
        if (!column || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(() => measureColumn());
        observer.observe(column);
        return () => observer.disconnect();
    }, [measureColumn]);

    const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        setIsDragging(true);
    }, []);

    const handlePointerMove = useCallback(
        (e: React.PointerEvent<HTMLDivElement>) => {
            if (!isDragging) return;
            const rect = measureColumn();
            if (!rect) return;
            const pointerY = e.clientY - rect.top;
            onResize(Math.max(MIN_TREE_HEIGHT, Math.min(pointerY, dragLimit(rect.height))));
        },
        [isDragging, measureColumn, onResize]
    );

    const handlePointerEnd = useCallback(() => {
        setIsDragging(false);
    }, []);

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLDivElement>) => {
            const rect = measureColumn();
            if (!rect) return;
            const maxHeight = Math.max(dragLimit(rect.height), height);
            let next: number;
            switch (e.key) {
                case "ArrowUp":
                    next = height - KEYBOARD_STEP;
                    break;
                case "ArrowDown":
                    next = height + KEYBOARD_STEP;
                    break;
                case "Home":
                    next = MIN_TREE_HEIGHT;
                    break;
                case "End":
                    next = maxHeight;
                    break;
                default:
                    return;
            }
            e.preventDefault();
            onResize(Math.max(MIN_TREE_HEIGHT, Math.min(next, maxHeight)));
        },
        [height, measureColumn, onResize]
    );

    // Keep the resize cursor and suppress text selection page-wide while dragging
    useEffect(() => {
        if (!isDragging) return;
        document.body.style.cursor = "ns-resize";
        document.body.style.userSelect = "none";
        return () => {
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        };
    }, [isDragging]);

    const valueNow = Math.round(height);

    return (
        <div
            ref={handleRef}
            className={styles.handle}
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize tree view"
            aria-controls={treeId}
            aria-valuenow={valueNow}
            aria-valuetext={`${valueNow} pixels`}
            aria-valuemin={MIN_TREE_HEIGHT}
            aria-valuemax={Math.round(Math.max(dragLimit(columnHeight), height))}
            tabIndex={0}
            onKeyDown={handleKeyDown}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerEnd}
            onPointerCancel={handlePointerEnd}
            style={{
                height: "8px",
                backgroundColor: isDragging
                    ? "rgba(33, 150, 243, 0.5)"
                    : "rgba(255, 255, 255, 0.1)",
                cursor: "ns-resize",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                transition: isDragging ? "none" : "background-color 0.2s",
                userSelect: "none",
                // The handle takes touch drags itself instead of letting them scroll the panel
                touchAction: "none",
            }}
            onMouseEnter={(e) => {
                if (!isDragging) {
                    e.currentTarget.style.backgroundColor = "rgba(33, 150, 243, 0.3)";
                }
            }}
            onMouseLeave={(e) => {
                if (!isDragging) {
                    e.currentTarget.style.backgroundColor = "rgba(255, 255, 255, 0.1)";
                }
            }}
            title="Drag to resize tree view"
        >
            <span style={{ fontSize: "0.6em", color: "rgba(255, 255, 255, 0.5)" }}>
                {GRIP_ICON_SVG}
            </span>
        </div>
    );
};

export default SceneTreeResizeHandle;
