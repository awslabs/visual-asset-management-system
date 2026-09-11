/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useState } from "react";
import { IfcViewerInstance, SpatialNode } from "../types";
import styles from "../ThatOpenWebIfcPanel.module.css";

interface ModelTreeProps {
    instance: IfcViewerInstance;
    tree: SpatialNode | null;
    /** Local IDs currently selected in the 3D view (drives row highlight). */
    selectedLocalIds: number[];
    /** Select these local IDs in the 3D view (driven by clicking a tree row). */
    onSelectLocalIds: (localIds: number[]) => void;
}

/**
 * Rows materialized per expanded group before the "show more" affordance. A
 * single IFC category routinely holds tens of thousands of elements, and
 * committing one <div> per element freezes the tab for seconds.
 */
const ROWS_PER_PAGE = 200;

/**
 * Renders the IFC category tree and provides per-node visibility + isolate
 * controls plus two-way selection sync with the 3D view:
 *   - Clicking a row label selects that element/group in 3D (onSelectLocalIds).
 *   - When the 3D selection changes (selectedLocalIds), the matching rows are
 *     highlighted and their ancestor groups auto-expanded.
 * Visibility is driven directly on the Fragments model, which is the API that
 * actually controls what the 3D view shows:
 *   - model.setVisible(localIds, boolean)  — show/hide specific elements
 *   - model.setVisible(undefined, boolean) — show/hide ALL elements
 *   - model.resetVisible()                 — restore everything to visible
 * After any change we call fragments.core.update(true) to repaint.
 *
 * Keyboard: the tree is a single tab stop; Up/Down move between rows,
 * Right/Left expand and collapse, Enter/Space select in 3D.
 */
const ModelTree: React.FC<ModelTreeProps> = ({
    instance,
    tree,
    selectedLocalIds,
    onSelectLocalIds,
}) => {
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    // Track which group nodes the user has hidden so each row reflects state.
    const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(new Set());
    const [busy, setBusy] = useState(false);
    // How many children of each expanded group are currently materialized.
    const [pageSizes, setPageSizes] = useState<Record<string, number>>({});
    // Row that owns the tree's tab stop (roving tabindex).
    const [activeKey, setActiveKey] = useState<string | null>(null);
    const bodyRef = useRef<HTMLDivElement>(null);

    // Fast lookup of the current 3D selection for row highlighting.
    const selectedSet = React.useMemo(() => new Set(selectedLocalIds), [selectedLocalIds]);

    const groups = tree?.children ?? [];
    const rootKeyPrefix = `root/${tree?.name ?? "Categories"}`;

    /**
     * Descendant local ids per group node, computed once per tree instead of on
     * every render. Leaves are omitted — their single id is the node's own.
     */
    const idsByKey = React.useMemo(() => {
        const map = new Map<string, number[]>();
        if (!tree) return map;
        // Appends into an accumulator rather than spreading a returned array: a
        // single IFC category holds tens of thousands of elements, and
        // `push(...ids)` at that length overflows the argument list.
        const walk = (node: SpatialNode, keyPrefix: string, out: number[]): void => {
            const key = `${keyPrefix}/${node.name}`;
            if (node.localId !== null) {
                out.push(node.localId);
                return;
            }
            const ids: number[] = [];
            node.children.forEach((child) => walk(child, key, ids));
            map.set(key, ids);
            for (const id of ids) out.push(id);
        };
        tree.children.forEach((group) => walk(group, rootKeyPrefix, []));
        return map;
    }, [tree, rootKeyPrefix]);

    /**
     * Group rows that contain part of the 3D selection. Recomputed when the
     * selection changes, not once per rendered row.
     */
    const selectedGroupKeys = React.useMemo(() => {
        const keys = new Set<string>();
        if (selectedSet.size === 0) return keys;
        idsByKey.forEach((ids, key) => {
            if (ids.some((id) => selectedSet.has(id))) keys.add(key);
        });
        return keys;
    }, [idsByKey, selectedSet]);

    const idsOf = (node: SpatialNode, key: string): number[] =>
        node.localId !== null ? [node.localId] : idsByKey.get(key) ?? [];

    // Auto-expand any group that contains a selected element so the highlight
    // is visible without manual expansion (3D click -> tree reveals the node).
    React.useEffect(() => {
        if (selectedLocalIds.length === 0 || !tree) return;
        const selSet = new Set(selectedLocalIds);
        setExpanded((prev) => {
            const next = new Set(prev);
            for (const group of tree.children) {
                const groupKey = `root/${tree.name}/${group.name}`;
                const hasSelected = group.children.some(
                    (child) => child.localId !== null && selSet.has(child.localId)
                );
                if (hasSelected) next.add(groupKey);
            }
            return next;
        });
    }, [selectedLocalIds, tree]);

    // A new model means new keys: drop the paging and tab-stop state.
    React.useEffect(() => {
        setPageSizes({});
        setActiveKey(null);
    }, [tree]);

    const toggleExpand = (key: string) => {
        // Keep the tab stop on a row that stays rendered when a subtree closes.
        setActiveKey(key);
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(key)) {
                next.delete(key);
            } else {
                next.add(key);
            }
            return next;
        });
    };

    const setExpandedState = (key: string, open: boolean) => {
        setActiveKey(key);
        setExpanded((prev) => {
            const next = new Set(prev);
            if (open) {
                next.add(key);
            } else {
                next.delete(key);
            }
            return next;
        });
    };

    // Repaint the Fragments scene after a visibility change.
    const refresh = async () => {
        try {
            await instance.fragments?.core?.update?.(true);
        } catch (err) {
            console.warn("ThatOpenWebIfc: fragments update failed:", err);
        }
    };

    const topLevelKey = (groupName: string) => `root/${tree?.name}/${groupName}`;

    const setNodeVisible = async (node: SpatialNode, key: string, visible: boolean) => {
        const ids = idsOf(node, key);
        if (ids.length === 0) return;
        setBusy(true);
        try {
            await instance.model.setVisible(ids, visible);
            await refresh();
            setHiddenKeys((prev) => {
                const next = new Set(prev);
                if (visible) {
                    next.delete(key);
                } else {
                    next.add(key);
                }
                return next;
            });
        } catch (err) {
            console.warn("ThatOpenWebIfc: setVisible failed:", err);
        } finally {
            setBusy(false);
        }
    };

    // Isolate = hide everything, then show only this node's items.
    const isolateNode = async (node: SpatialNode, key: string) => {
        const ids = idsOf(node, key);
        if (ids.length === 0) return;
        setBusy(true);
        try {
            await instance.model.setVisible(undefined, false); // hide all
            await instance.model.setVisible(ids, true); // show this subtree
            await refresh();
            // Mark every other top-level group hidden for the UI.
            setHiddenKeys(() => {
                const next = new Set<string>();
                tree?.children.forEach((group) => {
                    if (group !== node) next.add(topLevelKey(group.name));
                });
                return next;
            });
        } catch (err) {
            console.warn("ThatOpenWebIfc: isolate failed:", err);
        } finally {
            setBusy(false);
        }
    };

    const showAll = async () => {
        setBusy(true);
        try {
            await instance.model.resetVisible();
            await refresh();
            setHiddenKeys(new Set());
        } catch (err) {
            console.warn("ThatOpenWebIfc: show all failed:", err);
        } finally {
            setBusy(false);
        }
    };

    const hideAll = async () => {
        setBusy(true);
        try {
            await instance.model.setVisible(undefined, false);
            await refresh();
            setHiddenKeys(() => {
                const next = new Set<string>();
                tree?.children.forEach((group) => next.add(topLevelKey(group.name)));
                return next;
            });
        } catch (err) {
            console.warn("ThatOpenWebIfc: hide all failed:", err);
        } finally {
            setBusy(false);
        }
    };

    // Move the tab stop and the focus to the row `delta` positions away in
    // document order — the rendered rows are the visible ones.
    const moveFocus = (from: HTMLElement, delta: number) => {
        const rows = Array.from(
            bodyRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []
        );
        const index = rows.indexOf(from);
        const next = rows[index + delta];
        if (!next) return;
        setActiveKey(next.dataset.nodeKey ?? null);
        next.focus();
    };

    const handleRowKeyDown = (
        event: React.KeyboardEvent<HTMLDivElement>,
        node: SpatialNode,
        key: string,
        hasChildren: boolean,
        isOpen: boolean
    ) => {
        const row = event.currentTarget;
        switch (event.key) {
            case "Enter":
            case " ":
                event.preventDefault();
                onSelectLocalIds(idsOf(node, key));
                break;
            case "ArrowDown":
                event.preventDefault();
                moveFocus(row, 1);
                break;
            case "ArrowUp":
                event.preventDefault();
                moveFocus(row, -1);
                break;
            case "ArrowRight":
                if (hasChildren && !isOpen) {
                    event.preventDefault();
                    setExpandedState(key, true);
                } else if (hasChildren) {
                    event.preventDefault();
                    moveFocus(row, 1);
                }
                break;
            case "ArrowLeft":
                if (hasChildren && isOpen) {
                    event.preventDefault();
                    setExpandedState(key, false);
                } else {
                    event.preventDefault();
                    moveFocus(row, -1);
                }
                break;
            default:
                break;
        }
    };

    const firstRowKey = groups.length > 0 ? `${rootKeyPrefix}/${groups[0].name}` : null;
    const tabStopKey = activeKey ?? firstRowKey;

    const renderNode = (node: SpatialNode, keyPrefix: string, depth: number) => {
        const key = `${keyPrefix}/${node.name}`;
        const hasChildren = node.children.length > 0;
        const isOpen = expanded.has(key);
        const isHidden = hiddenKeys.has(key);
        const childCount = node.children.length;

        // A row is "selected" when it (an element) or any of its descendants
        // (a group) is in the current 3D selection.
        const isSelected =
            node.localId !== null ? selectedSet.has(node.localId) : selectedGroupKeys.has(key);

        const shown = pageSizes[key] ?? ROWS_PER_PAGE;
        const remaining = childCount - shown;

        return (
            // role="none" keeps this layout wrapper out of the tree structure so
            // the rows read as direct children of role="tree".
            <div key={key} role="none">
                <div
                    className={styles.treeRow}
                    role="treeitem"
                    aria-expanded={hasChildren ? isOpen : undefined}
                    aria-selected={isSelected}
                    tabIndex={key === tabStopKey ? 0 : -1}
                    data-node-key={key}
                    onFocus={() => setActiveKey(key)}
                    onKeyDown={(event) => handleRowKeyDown(event, node, key, hasChildren, isOpen)}
                    style={{
                        paddingLeft: `${10 + depth * 14}px`,
                        opacity: isHidden ? 0.45 : 1,
                        background: isSelected ? "rgba(74,166,255,0.25)" : undefined,
                    }}
                >
                    {hasChildren ? (
                        <span
                            className={styles.treeCaret}
                            onClick={() => toggleExpand(key)}
                            aria-hidden="true"
                        >
                            {isOpen ? "▾" : "▸"}
                        </span>
                    ) : (
                        <span className={styles.treeCaretSpacer} />
                    )}

                    <span
                        className={styles.treeLabel}
                        title={`${node.name} — click to select in 3D`}
                        onClick={() => onSelectLocalIds(idsOf(node, key))}
                        style={{ cursor: "pointer" }}
                    >
                        {node.name}
                        {childCount > 0 && <span className={styles.treeCount}>{childCount}</span>}
                    </span>

                    <button
                        type="button"
                        className={styles.iconButton}
                        onClick={() => isolateNode(node, key)}
                        title="Isolate (show only this)"
                        aria-label={`Isolate ${node.name} (show only this)`}
                        disabled={busy}
                    >
                        <span aria-hidden="true">🎯</span>
                    </button>
                    <button
                        type="button"
                        className={styles.iconButton}
                        onClick={() => setNodeVisible(node, key, isHidden)}
                        title={isHidden ? "Show" : "Hide"}
                        aria-label={`${isHidden ? "Show" : "Hide"} ${node.name}`}
                        disabled={busy}
                    >
                        <span aria-hidden="true">{isHidden ? "🙈" : "👁"}</span>
                    </button>
                </div>
                {hasChildren && isOpen && (
                    <div role="group">
                        {node.children.slice(0, shown).map((c) => renderNode(c, key, depth + 1))}
                    </div>
                )}
                {hasChildren && isOpen && remaining > 0 && (
                    <button
                        type="button"
                        className={styles.treeShowMore}
                        style={{ marginLeft: `${10 + (depth + 1) * 14}px` }}
                        onClick={() =>
                            setPageSizes((prev) => ({
                                ...prev,
                                [key]: shown + ROWS_PER_PAGE,
                            }))
                        }
                    >
                        Show {Math.min(ROWS_PER_PAGE, remaining).toLocaleString()} more of{" "}
                        {remaining.toLocaleString()}
                    </button>
                )}
            </div>
        );
    };

    return (
        <div className={styles.treeWrap}>
            <div className={styles.treeToolbar}>
                <button
                    type="button"
                    className={styles.smallButton}
                    onClick={showAll}
                    disabled={busy}
                    title="Show every element"
                >
                    <span aria-hidden="true">👁</span> Show All
                </button>
                <button
                    type="button"
                    className={styles.smallButton}
                    onClick={hideAll}
                    disabled={busy}
                    title="Hide every element"
                >
                    <span aria-hidden="true">🙈</span> Hide All
                </button>
                <button
                    type="button"
                    className={styles.smallButton}
                    style={{ gridColumn: "1 / -1" }}
                    onClick={() => onSelectLocalIds([])}
                    disabled={busy || selectedLocalIds.length === 0}
                    title="Clear the current selection"
                >
                    <span aria-hidden="true">✖</span> Clear Selection
                </button>
            </div>
            <div className={styles.treeBody} ref={bodyRef}>
                {groups.length === 0 ? (
                    <div className={styles.emptyHint}>No categories found for this model.</div>
                ) : (
                    <div role="tree" aria-label="Model tree">
                        {groups.map((group) => renderNode(group, rootKeyPrefix, 0))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default ModelTree;
