/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";

interface CesiumSceneGraphProps {
    tilesets: any[];
    selectedTiles: any[];
    // Increments whenever per-tile visibility or selection changes in the parent
    sceneVersion: number;
    isTileHidden: (tile: any) => boolean;
    onTileClick: (tile: any, ctrlKey: boolean) => void;
    onClearSelection: () => void;
    onToggleTileVisibility: (tile: any) => void;
    onSetAllVisibility: (visible: boolean) => void;
    onZoomToTile: (tile: any) => void;
}

// Maximum children rendered per node to keep large streamed tilesets responsive
const MAX_CHILDREN_RENDERED = 100;

// Stable ids for Cesium3DTile objects (tiles have no public uuid)
let nextNodeId = 1;
const nodeIds = new WeakMap<object, number>();
const getNodeId = (obj: object): number => {
    let id = nodeIds.get(obj);
    if (id === undefined) {
        id = nextNodeId++;
        nodeIds.set(obj, id);
    }
    return id;
};

const basename = (uri: any): string | undefined =>
    uri ? String(uri).split("?")[0].split("/").pop() : undefined;

// All content URIs of a tile — a tile may carry a single content or a contents array
export const getTileContentUris = (tile: any): string[] => {
    const header = tile._header;
    if (!header) return [];
    if (Array.isArray(header.contents)) {
        return header.contents.map((c: any) => c?.uri || c?.url).filter(Boolean);
    }
    const uri = header.content?.uri || header.content?.url;
    return uri ? [uri] : [];
};

// Human-readable label for a tile from its content URI(s)
const getTileLabel = (tile: any): string => {
    const names = getTileContentUris(tile).map(basename).filter(Boolean) as string[];
    if (names.length > 0) return names.join(", ");
    return tile.hasRenderableContent ? "Tile" : "Group";
};

const getTileIcon = (tile: any): string => {
    if (tile.hasTilesetContent) return "🗂️";
    if (!tile.hasRenderableContent) return "📁";
    return "📐";
};

const CesiumSceneGraph: React.FC<CesiumSceneGraphProps> = ({
    tilesets,
    selectedTiles,
    sceneVersion,
    isTileHidden,
    onTileClick,
    onClearSelection,
    onToggleTileVisibility,
    onSetAllVisibility,
    onZoomToTile,
}) => {
    const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
    const [hoveredNode, setHoveredNode] = useState<number | null>(null);
    // Tick to re-render the tree as tiles stream in/out (the tile tree is read live)
    const [, setRefreshTick] = useState(0);
    const [tilesetVisible, setTilesetVisible] = useState<Record<number, boolean>>({});

    // Refresh the tree as tiles load and expand tileset roots by default
    useEffect(() => {
        const removers: Array<() => void> = [];

        tilesets.forEach((tileset) => {
            const onTilesLoaded = () => setRefreshTick((t) => t + 1);
            tileset.initialTilesLoaded.addEventListener(onTilesLoaded);
            tileset.allTilesLoaded.addEventListener(onTilesLoaded);
            removers.push(() => {
                tileset.initialTilesLoaded.removeEventListener(onTilesLoaded);
                tileset.allTilesLoaded.removeEventListener(onTilesLoaded);
            });
        });

        setExpandedNodes(new Set(tilesets.map((tileset) => getNodeId(tileset))));
        const visibility: Record<number, boolean> = {};
        tilesets.forEach((tileset, index) => {
            visibility[index] = tileset.show !== false;
        });
        setTilesetVisible(visibility);

        return () => removers.forEach((remove) => remove());
    }, [tilesets]);

    // Auto-expand ancestors of tiles selected from the 3D scene
    useEffect(() => {
        if (selectedTiles.length === 0) return;
        setExpandedNodes((prev) => {
            const next = new Set(prev);
            selectedTiles.forEach((tile) => {
                let parent = tile.parent;
                while (parent) {
                    next.add(getNodeId(parent));
                    parent = parent.parent;
                }
            });
            tilesets.forEach((tileset) => next.add(getNodeId(tileset)));
            return next;
        });
    }, [selectedTiles, tilesets]);

    const toggleExpand = useCallback((nodeId: number) => {
        setExpandedNodes((prev) => {
            const next = new Set(prev);
            if (next.has(nodeId)) {
                next.delete(nodeId);
            } else {
                next.add(nodeId);
            }
            return next;
        });
    }, []);

    const toggleTilesetVisibility = useCallback((tileset: any, index: number) => {
        tileset.show = !tileset.show;
        setTilesetVisible((prev) => ({ ...prev, [index]: tileset.show }));
    }, []);

    const setAllVisibility = useCallback(
        (visible: boolean) => {
            onSetAllVisibility(visible);
            setTilesetVisible(() => {
                const next: Record<number, boolean> = {};
                tilesets.forEach((_, index) => {
                    next[index] = visible;
                });
                return next;
            });
        },
        [tilesets, onSetAllVisibility]
    );

    const renderTileNode = (tile: any, depth: number): React.ReactNode => {
        const nodeId = getNodeId(tile);
        const children = tile.children || [];
        const hasChildren = children.length > 0;
        const isExpanded = expandedNodes.has(nodeId);
        const isSelected = selectedTiles.includes(tile);
        const isHidden = isTileHidden(tile);
        const isHovered = hoveredNode === nodeId;

        return (
            <div key={nodeId}>
                <div
                    data-tile-node-id={nodeId}
                    style={{
                        padding: "3px 6px",
                        paddingLeft: `${depth * 14 + 6}px`,
                        cursor: "pointer",
                        backgroundColor: isSelected
                            ? "rgba(76, 175, 80, 0.3)"
                            : isHovered
                            ? "rgba(255, 255, 255, 0.1)"
                            : "transparent",
                        borderRadius: "4px",
                        marginBottom: "1px",
                        display: "flex",
                        alignItems: "center",
                        fontSize: "0.8em",
                        opacity: isHidden ? 0.5 : 1,
                    }}
                    onClick={(e) => onTileClick(tile, e.ctrlKey)}
                    onDoubleClick={() => onZoomToTile(tile)}
                    onMouseEnter={() => setHoveredNode(nodeId)}
                    onMouseLeave={() => setHoveredNode(null)}
                >
                    <span
                        onClick={(e) => {
                            e.stopPropagation();
                            if (hasChildren) toggleExpand(nodeId);
                        }}
                        style={{
                            marginRight: "4px",
                            width: "12px",
                            display: "inline-block",
                            userSelect: "none",
                        }}
                    >
                        {hasChildren ? (isExpanded ? "▼" : "▶") : ""}
                    </span>
                    <span
                        onClick={(e) => {
                            e.stopPropagation();
                            onToggleTileVisibility(tile);
                        }}
                        style={{ marginRight: "5px", cursor: "pointer", fontSize: "0.9em" }}
                        title={isHidden ? "Show" : "Hide"}
                    >
                        {isHidden ? "👁️‍🗨️" : "👁️"}
                    </span>
                    <span style={{ marginRight: "5px", fontSize: "0.9em" }}>
                        {getTileIcon(tile)}
                    </span>
                    <span
                        style={{
                            flex: 1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}
                        title={getTileLabel(tile)}
                    >
                        {getTileLabel(tile)}
                    </span>
                    {hasChildren && (
                        <span style={{ fontSize: "0.75em", color: "#999", marginLeft: "4px" }}>
                            ({children.length})
                        </span>
                    )}
                </div>
                {hasChildren && isExpanded && (
                    <div>
                        {children
                            .slice(0, MAX_CHILDREN_RENDERED)
                            .map((child: any) => renderTileNode(child, depth + 1))}
                        {children.length > MAX_CHILDREN_RENDERED && (
                            <div
                                style={{
                                    paddingLeft: `${(depth + 1) * 14 + 6}px`,
                                    fontSize: "0.75em",
                                    color: "#999",
                                    padding: "3px 6px",
                                }}
                            >
                                … {children.length - MAX_CHILDREN_RENDERED} more tiles not shown
                            </div>
                        )}
                    </div>
                )}
            </div>
        );
    };

    const renderTilesetNode = (tileset: any, index: number): React.ReactNode => {
        const nodeId = getNodeId(tileset);
        const isExpanded = expandedNodes.has(nodeId);
        const isVisible = tilesetVisible[index] !== false;
        const root = tileset.root;
        const label = tileset.vamsFileKey
            ? String(tileset.vamsFileKey).split("/").pop()
            : `Tileset ${index + 1}`;
        const stats = tileset.statistics;

        return (
            <div key={nodeId}>
                <div
                    style={{
                        padding: "4px 6px",
                        cursor: "pointer",
                        backgroundColor: "rgba(33, 150, 243, 0.15)",
                        borderLeft: "3px solid #2196F3",
                        borderRadius: "4px",
                        marginBottom: "2px",
                        display: "flex",
                        alignItems: "center",
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        opacity: isVisible ? 1 : 0.5,
                    }}
                    onClick={() => toggleExpand(nodeId)}
                >
                    <span style={{ marginRight: "4px", width: "12px", userSelect: "none" }}>
                        {root ? (isExpanded ? "▼" : "▶") : ""}
                    </span>
                    <span
                        onClick={(e) => {
                            e.stopPropagation();
                            toggleTilesetVisibility(tileset, index);
                        }}
                        style={{ marginRight: "5px", cursor: "pointer" }}
                        title={isVisible ? "Hide tileset" : "Show tileset"}
                    >
                        {isVisible ? "👁️" : "👁️‍🗨️"}
                    </span>
                    <span style={{ marginRight: "5px" }}>📄</span>
                    <span
                        style={{
                            flex: 1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}
                        title={tileset.vamsFileKey || label}
                    >
                        {label}
                    </span>
                    {stats && (
                        <span
                            style={{ fontSize: "0.75em", color: "#03A9F4", marginLeft: "4px" }}
                            title="Tiles loaded / total tiles known"
                        >
                            {stats.numberOfLoadedTilesTotal}/{stats.numberOfTilesTotal}
                        </span>
                    )}
                </div>
                {root && isExpanded && renderTileNode(root, 1)}
            </div>
        );
    };

    const selectedTile = selectedTiles.length === 1 ? selectedTiles[0] : null;
    const selectedTileContents = selectedTile ? getTileContentUris(selectedTile) : [];

    return (
        <div
            style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
            }}
            data-scene-version={sceneVersion}
        >
            {/* Actions */}
            <div
                style={{
                    padding: "8px 12px",
                    borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px",
                }}
            >
                {selectedTiles.length > 0 && (
                    <button
                        onClick={onClearSelection}
                        style={{
                            background: "rgba(156, 39, 176, 0.3)",
                            border: "1px solid rgba(156, 39, 176, 0.5)",
                            color: "white",
                            padding: "5px 8px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                            fontWeight: "bold",
                        }}
                    >
                        ✕ Clear Selection ({selectedTiles.length})
                    </button>
                )}
                <div style={{ display: "flex", gap: "6px" }}>
                    <button
                        onClick={() => setAllVisibility(true)}
                        style={{
                            flex: 1,
                            background: "rgba(76, 175, 80, 0.3)",
                            border: "1px solid rgba(76, 175, 80, 0.5)",
                            color: "white",
                            padding: "5px 8px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                        }}
                    >
                        👁️ Show All
                    </button>
                    <button
                        onClick={() => setAllVisibility(false)}
                        style={{
                            flex: 1,
                            background: "rgba(244, 67, 54, 0.3)",
                            border: "1px solid rgba(244, 67, 54, 0.5)",
                            color: "white",
                            padding: "5px 8px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                        }}
                    >
                        👁️‍🗨️ Hide All
                    </button>
                    <button
                        onClick={() => setRefreshTick((t) => t + 1)}
                        style={{
                            background: "rgba(255, 255, 255, 0.1)",
                            border: "1px solid rgba(255, 255, 255, 0.2)",
                            color: "white",
                            padding: "5px 8px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                        }}
                        title="Refresh tree (tiles stream in as the camera moves)"
                    >
                        🔄
                    </button>
                </div>
            </div>

            {/* Tree */}
            <div
                style={{
                    flex: 1,
                    overflowY: "auto",
                    overflowX: "hidden",
                    padding: "8px",
                    scrollbarWidth: "thin",
                    scrollbarColor: "rgba(255, 255, 255, 0.5) transparent",
                }}
            >
                {tilesets.length > 0 ? (
                    tilesets.map((tileset, index) => renderTilesetNode(tileset, index))
                ) : (
                    <div style={{ padding: "16px", textAlign: "center", color: "#999" }}>
                        No tilesets loaded
                    </div>
                )}
            </div>

            {/* Selected tile details */}
            {selectedTile && (
                <div
                    style={{
                        borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                        padding: "10px 12px",
                        maxHeight: "40%",
                        overflowY: "auto",
                        backgroundColor: "rgba(0, 0, 0, 0.3)",
                        fontSize: "0.8em",
                        lineHeight: "1.7",
                    }}
                >
                    <h5 style={{ margin: "0 0 8px 0", fontSize: "1em", color: "#4CAF50" }}>
                        📋 Tile Details
                    </h5>
                    {selectedTileContents.length > 1 ? (
                        <div>
                            <strong>Contents ({selectedTileContents.length}):</strong>
                            {selectedTileContents.map((uri) => (
                                <div key={uri} style={{ paddingLeft: "8px", color: "#ccc" }}>
                                    {basename(uri)}
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div>
                            <strong>Content:</strong> {getTileLabel(selectedTile)}
                        </div>
                    )}
                    <div>
                        <strong>Children:</strong> {selectedTile.children?.length || 0}
                    </div>
                    <div>
                        <strong>Renderable:</strong>{" "}
                        {selectedTile.hasRenderableContent ? "Yes" : "No"}
                    </div>
                    <div>
                        <strong>Content loaded:</strong> {selectedTile.contentReady ? "Yes" : "No"}
                    </div>
                    {Number.isFinite(selectedTile.geometricError) && (
                        <div>
                            <strong>Geometric error:</strong>{" "}
                            {selectedTile.geometricError.toFixed(2)}
                        </div>
                    )}
                    {selectedTile.boundingSphere && (
                        <div>
                            <strong>Bounds radius:</strong>{" "}
                            {selectedTile.boundingSphere.radius.toFixed(2)}m
                        </div>
                    )}
                    <button
                        onClick={() => onZoomToTile(selectedTile)}
                        style={{
                            marginTop: "8px",
                            width: "100%",
                            background: "#2196F3",
                            border: "none",
                            color: "white",
                            padding: "6px 10px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.9em",
                        }}
                    >
                        🔍 Zoom to Tile
                    </button>
                </div>
            )}

            {/* Multi-selection info */}
            {selectedTiles.length > 1 && (
                <div
                    style={{
                        borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                        padding: "10px 12px",
                        backgroundColor: "rgba(76, 175, 80, 0.15)",
                        fontSize: "0.8em",
                    }}
                >
                    ✓ {selectedTiles.length} tiles selected
                </div>
            )}

            {/* Help text */}
            <div
                style={{
                    padding: "6px 12px",
                    borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                    fontSize: "0.7em",
                    color: "#999",
                }}
            >
                Click: Select (scene or tree) | Ctrl+Click: Multi-select | Double-click: Zoom | 👁️:
                Toggle visibility
            </div>
        </div>
    );
};

export default CesiumSceneGraph;
