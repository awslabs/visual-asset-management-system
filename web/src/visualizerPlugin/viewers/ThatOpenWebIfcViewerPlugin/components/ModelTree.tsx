/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
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

    // Fast lookup of the current 3D selection for row highlighting.
    const selectedSet = React.useMemo(() => new Set(selectedLocalIds), [selectedLocalIds]);

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

    const toggleExpand = (key: string) => {
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

    // Repaint the Fragments scene after a visibility change.
    const refresh = async () => {
        try {
            await instance.fragments?.core?.update?.(true);
        } catch (err) {
            console.warn("ThatOpenWebIfc: fragments update failed:", err);
        }
    };

    // Collect all localIds under a node (group node → all element children).
    const collectIds = (node: SpatialNode): number[] => {
        const ids: number[] = [];
        if (node.localId !== null) ids.push(node.localId);
        node.children.forEach((c) => ids.push(...collectIds(c)));
        return ids;
    };

    const topLevelKey = (groupName: string) => `root/${tree?.name}/${groupName}`;

    const setNodeVisible = async (node: SpatialNode, key: string, visible: boolean) => {
        const ids = collectIds(node);
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
    const isolateNode = async (node: SpatialNode) => {
        const ids = collectIds(node);
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

    const renderNode = (node: SpatialNode, keyPrefix: string, depth: number) => {
        const key = `${keyPrefix}/${node.name}`;
        const hasChildren = node.children.length > 0;
        const isOpen = expanded.has(key);
        const isHidden = hiddenKeys.has(key);
        const childCount = node.children.length;

        // A row is "selected" when it (an element) or any of its descendants
        // (a group) is in the current 3D selection.
        const isSelected =
            node.localId !== null
                ? selectedSet.has(node.localId)
                : collectIds(node).some((id) => selectedSet.has(id));

        return (
            <div key={key}>
                <div
                    className={styles.treeRow}
                    style={{
                        paddingLeft: `${10 + depth * 14}px`,
                        opacity: isHidden ? 0.45 : 1,
                        background: isSelected ? "rgba(74,166,255,0.25)" : undefined,
                    }}
                >
                    {hasChildren ? (
                        <span className={styles.treeCaret} onClick={() => toggleExpand(key)}>
                            {isOpen ? "▾" : "▸"}
                        </span>
                    ) : (
                        <span className={styles.treeCaretSpacer} />
                    )}

                    <span
                        className={styles.treeLabel}
                        title={`${node.name} — click to select in 3D`}
                        onClick={() => onSelectLocalIds(collectIds(node))}
                        style={{ cursor: "pointer" }}
                    >
                        {node.name}
                        {childCount > 0 && <span className={styles.treeCount}>{childCount}</span>}
                    </span>

                    <button
                        className={styles.iconButton}
                        onClick={() => isolateNode(node)}
                        title="Isolate (show only this)"
                        disabled={busy}
                    >
                        🎯
                    </button>
                    <button
                        className={styles.iconButton}
                        onClick={() => setNodeVisible(node, key, isHidden)}
                        title={isHidden ? "Show" : "Hide"}
                        disabled={busy}
                    >
                        {isHidden ? "🙈" : "👁"}
                    </button>
                </div>
                {hasChildren && isOpen && node.children.map((c) => renderNode(c, key, depth + 1))}
            </div>
        );
    };

    const groups = tree?.children ?? [];
    const rootKeyPrefix = `root/${tree?.name ?? "Categories"}`;

    return (
        <div className={styles.treeWrap}>
            <div className={styles.treeToolbar}>
                <button
                    className={styles.smallButton}
                    onClick={showAll}
                    disabled={busy}
                    title="Show every element"
                >
                    👁 Show All
                </button>
                <button
                    className={styles.smallButton}
                    onClick={hideAll}
                    disabled={busy}
                    title="Hide every element"
                >
                    🙈 Hide All
                </button>
                <button
                    className={styles.smallButton}
                    style={{ gridColumn: "1 / -1" }}
                    onClick={() => onSelectLocalIds([])}
                    disabled={busy || selectedLocalIds.length === 0}
                    title="Clear the current selection"
                >
                    ✖ Clear Selection
                </button>
            </div>
            <div className={styles.treeBody}>
                {groups.length === 0 ? (
                    <div className={styles.emptyHint}>No categories found for this model.</div>
                ) : (
                    groups.map((group) => renderNode(group, rootKeyPrefix, 0))
                )}
            </div>
        </div>
    );
};

export default ModelTree;
