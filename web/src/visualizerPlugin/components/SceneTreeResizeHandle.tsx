/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { GRIP_ICON_SVG } from "./GripIconSvg";
import styles from "./SceneTreeResizeHandle.module.css";

// The tree keeps at least MIN_TREE_HEIGHT and the details below the handle at least MIN_DETAILS_HEIGHT
const MIN_TREE_HEIGHT = 150;
const MIN_DETAILS_HEIGHT = 200;
// Height change per ArrowUp / ArrowDown key press, in pixels
const KEYBOARD_STEP = 20;
// Tree height the divider takes on its own while something is selected
const SELECTED_TREE_HEIGHT = 300;
// Room the divider leaves the details below the handle on its own while nothing is selected; it
// holds their two-line help text, 45px tall at the panel's width
const UNSELECTED_DETAILS_HEIGHT = 47;

/** Sizes the handle measures in its column, in the units of the tree's style height */
export interface SceneTreeRoom {
    /** Tallest tree whose handle still ends inside the column */
    fit: number;
    /** Tallest tree a drag allows, so the details below the handle keep MIN_DETAILS_HEIGHT */
    limit: number;
}

/**
 * Tree height the divider takes on its own until the user sizes the tree: SELECTED_TREE_HEIGHT
 * within the drag's limit while something is selected, else the tallest tree that leaves the
 * details UNSELECTED_DETAILS_HEIGHT below the handle, so a handle that is taller to touch takes its
 * room from the tree. Never below MIN_TREE_HEIGHT, the shortest tree the drag and the keys allow.
 */
export const automaticTreeHeight = (room: SceneTreeRoom, hasSelection: boolean): number =>
    hasSelection
        ? Math.min(SELECTED_TREE_HEIGHT, room.limit)
        : Math.max(MIN_TREE_HEIGHT, room.fit - UNSELECTED_DETAILS_HEIGHT);

/**
 * A tree height the user picked, within the bound that applies now: the drag's limit while
 * something is selected, so the details keep MIN_DETAILS_HEIGHT; else the automatic height with
 * nothing selected, so the details keep UNSELECTED_DETAILS_HEIGHT for the help text. A bound that
 * grows leaves the height as it is. The drag's limit is at or below the automatic height with
 * nothing selected, as MIN_DETAILS_HEIGHT is the larger room, so clearing a selection never lowers
 * a height.
 */
export const pickedTreeHeight = (
    height: number,
    room: SceneTreeRoom,
    hasSelection: boolean
): number => Math.min(height, hasSelection ? room.limit : automaticTreeHeight(room, false));

// Padding and border the tree draws in addition to its style height
const treeFrame = (tree: HTMLElement): number => {
    const style = getComputedStyle(tree);
    if (style.boxSizing === "border-box") return 0;
    const px = (value: string) => parseFloat(value) || 0;
    return (
        px(style.paddingTop) +
        px(style.paddingBottom) +
        px(style.borderTopWidth) +
        px(style.borderBottomWidth)
    );
};

// Height of the tree as drawn, in the units of its style height
const drawnTreeHeight = (tree: HTMLElement): number =>
    tree.getBoundingClientRect().height - treeFrame(tree);

// The column's sizes: the room from the tree's top to the column's bottom, less the tree's padding
// and border and the handle, is the tallest tree that fits; less the details' minimum, it is the
// tallest a drag allows
const measureRoom = (handle: HTMLElement, tree: HTMLElement): SceneTreeRoom | null => {
    const column = handle.parentElement;
    if (!column) return null;
    const columnBox = column.getBoundingClientRect();
    const fit =
        columnBox.bottom -
        tree.getBoundingClientRect().top -
        treeFrame(tree) -
        handle.getBoundingClientRect().height;
    return {
        fit,
        limit: Math.max(MIN_TREE_HEIGHT, fit - MIN_DETAILS_HEIGHT),
    };
};

// Tallest tree a drag allows
const dragLimit = (handle: HTMLElement, tree: HTMLElement): number =>
    measureRoom(handle, tree)?.limit ?? MIN_TREE_HEIGHT;

interface SceneTreeResizeHandleProps {
    /** Height of the scene tree above the handle, in pixels */
    height: number;
    /** Called with the new tree height when a drag or a key changes it */
    onResize: (height: number) => void;
    /**
     * Called with the column's sizes when they change, as the column or the header above the tree
     * changes size
     */
    onRoomChange?: (room: SceneTreeRoom) => void;
    /** id of the scene tree element the handle sizes */
    treeId: string;
}

/**
 * The divider between the scene tree and the details below it in the Three.js and Needle USD scene
 * graphs. It sits directly below the tree, in one column with the header above the tree and the
 * details, and measures its limits from the tree's top to the column's bottom.
 *
 * Pointer events cover mouse, pen and touch; capturing the pointer keeps the drag on the handle when
 * the pointer moves off it, and the handle stays where the pointer took hold of it. As a focusable
 * separator, ArrowUp / ArrowDown move it by KEYBOARD_STEP and Home / End move it to its limits. A
 * tree taller than a drag allows (with nothing selected, the automatic height and a height the user
 * picked can be) keeps its height as the upper limit of the keys and of a drag that starts there, so
 * they can only make it shorter.
 */
const SceneTreeResizeHandle: React.FC<SceneTreeResizeHandleProps> = ({
    height,
    onResize,
    onRoomChange,
    treeId,
}) => {
    const handleRef = useRef<HTMLDivElement>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [limit, setLimit] = useState(MIN_TREE_HEIGHT);
    const roomRef = useRef<SceneTreeRoom | null>(null);
    // Latest tree height, also between a resize and the render that applies it
    const heightRef = useRef(height);
    // Pointer position and drawn tree height when the drag started
    const dragStartRef = useRef({ pointerY: 0, treeHeight: 0 });

    const getTree = useCallback(
        () => handleRef.current?.ownerDocument.getElementById(treeId) ?? null,
        [treeId]
    );

    // Measures the column and reports a change to aria-valuemax and onRoomChange
    const measure = useCallback(() => {
        const handle = handleRef.current;
        const tree = getTree();
        if (!handle || !tree) return;
        const next = measureRoom(handle, tree);
        const last = roomRef.current;
        if (!next || (last && next.fit === last.fit)) return;
        roomRef.current = next;
        setLimit(next.limit);
        onRoomChange?.(next);
    }, [getTree, onRoomChange]);

    // After every render, since the header above the tree changes with the selection
    useLayoutEffect(() => {
        heightRef.current = height;
        measure();
    });

    // And when the column changes size, for example with the window
    useLayoutEffect(() => {
        const column = handleRef.current?.parentElement;
        if (!column || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(() => measure());
        observer.observe(column);
        return () => observer.disconnect();
    }, [measure]);

    // Reports a height only when it differs from the current one
    const resize = useCallback(
        (next: number) => {
            if (next === heightRef.current) return;
            heightRef.current = next;
            onResize(next);
        },
        [onResize]
    );

    const handlePointerDown = useCallback(
        (e: React.PointerEvent<HTMLDivElement>) => {
            // Only the primary button drags (a mouse's left button, a touch, a pen tip); the others
            // keep their own use, such as the context menu
            if (e.button !== 0) return;
            const tree = getTree();
            if (!tree) return;
            e.preventDefault();
            e.currentTarget.setPointerCapture?.(e.pointerId);
            dragStartRef.current = { pointerY: e.clientY, treeHeight: drawnTreeHeight(tree) };
            setIsDragging(true);
        },
        [getTree]
    );

    // The tree's bottom edge, and the handle below it, move as far as the pointer has moved, up to
    // the drag's limit or the height the drag started from, whichever is taller
    const handlePointerMove = useCallback(
        (e: React.PointerEvent<HTMLDivElement>) => {
            const handle = handleRef.current;
            const tree = getTree();
            if (!isDragging || !handle || !tree) return;
            const start = dragStartRef.current;
            const next = start.treeHeight + (e.clientY - start.pointerY);
            const maxHeight = Math.max(dragLimit(handle, tree), start.treeHeight);
            resize(Math.max(MIN_TREE_HEIGHT, Math.min(next, maxHeight)));
        },
        [getTree, isDragging, resize]
    );

    const handlePointerEnd = useCallback(() => {
        setIsDragging(false);
    }, []);

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLDivElement>) => {
            const handle = handleRef.current;
            const tree = getTree();
            if (!handle || !tree) return;
            const current = heightRef.current;
            const maxHeight = Math.max(dragLimit(handle, tree), current);
            let next: number;
            switch (e.key) {
                case "ArrowUp":
                    next = current - KEYBOARD_STEP;
                    break;
                case "ArrowDown":
                    next = current + KEYBOARD_STEP;
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
            resize(Math.max(MIN_TREE_HEIGHT, Math.min(next, maxHeight)));
        },
        [getTree, resize]
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
            aria-valuemax={Math.round(Math.max(limit, height))}
            tabIndex={0}
            onKeyDown={handleKeyDown}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerEnd}
            onPointerCancel={handlePointerEnd}
            onLostPointerCapture={handlePointerEnd}
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
            {/* The grip's line box is taller than the bar; it takes no pointer events, so the hit
                area is the handle's own box and not the edge of the tree or the details */}
            <span
                style={{
                    fontSize: "0.6em",
                    color: "rgba(255, 255, 255, 0.5)",
                    pointerEvents: "none",
                }}
            >
                {GRIP_ICON_SVG}
            </span>
        </div>
    );
};

export default SceneTreeResizeHandle;
