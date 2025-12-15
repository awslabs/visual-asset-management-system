/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";

interface SceneNode {
    id: string;
    name: string;
    type: string;
    object: any;
    children: SceneNode[];
    visible: boolean;
    veerumId?: string;
}

interface VeerumSceneGraphProps {
    viewerController: any;
    loadedModels: any[];
    initError: string | null;
    onClose?: () => void;
}

const VeerumSceneGraph: React.FC<VeerumSceneGraphProps> = ({
    viewerController,
    loadedModels,
    initError,
    onClose,
}) => {
    const [sceneTree, setSceneTree] = useState<SceneNode[]>([]);
    const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
    const [selectedObject, setSelectedObject] = useState<any>(null);
    const [hoveredObject, setHoveredObject] = useState<string | null>(null);

    // Build scene tree from Three.js scene
    const buildSceneTree = useCallback(() => {
        if (!viewerController) return [];

        try {
            const scene = viewerController.getScene();
            if (!scene) return [];

            const buildNode = (obj: any): SceneNode => {
                const node: SceneNode = {
                    id: obj.uuid,
                    name: obj.name || obj.type || "Unnamed",
                    type: obj.type,
                    object: obj,
                    children: [],
                    visible: obj.visible !== false,
                    veerumId: (obj as any).veerumId,
                };

                // Add children recursively
                if (obj.children && obj.children.length > 0) {
                    node.children = obj.children
                        .filter((child: any) => {
                            // Filter out cameras, lights, and helpers unless they're important
                            const type = child.type;
                            return (
                                !type.includes("Camera") &&
                                !type.includes("Light") &&
                                !type.includes("Helper")
                            );
                        })
                        .map((child: any) => buildNode(child));
                }

                return node;
            };

            // Build tree from loaded models
            const tree: SceneNode[] = loadedModels.map((model) => {
                return buildNode(model);
            });

            return tree;
        } catch (error) {
            console.error("Error building scene tree:", error);
            return [];
        }
    }, [viewerController, loadedModels]);

    // Refresh scene tree
    useEffect(() => {
        const tree = buildSceneTree();
        setSceneTree(tree);

        // Auto-expand root nodes
        const rootIds = new Set(tree.map((node) => node.id));
        setExpandedNodes(rootIds);
    }, [buildSceneTree]);

    // Toggle node expansion
    const toggleExpand = useCallback((nodeId: string) => {
        setExpandedNodes((prev) => {
            const newSet = new Set(prev);
            if (newSet.has(nodeId)) {
                newSet.delete(nodeId);
            } else {
                newSet.add(nodeId);
            }
            return newSet;
        });
    }, []);

    // Handle object selection (single click - highlight)
    const handleObjectClick = useCallback((node: SceneNode) => {
        setSelectedObject(node.object);
        console.log("Selected object:", node.name, node.object);

        // TODO: Highlight object in scene if Veerum supports it
        // This might require adding an outline or changing material
    }, []);

    // Handle object double-click (zoom to object)
    const handleObjectDoubleClick = useCallback(
        async (node: SceneNode) => {
            if (!viewerController) return;

            try {
                await viewerController.zoomCameraToObject(node.object);
                console.log("Zoomed to object:", node.name);
            } catch (error) {
                console.error("Error zooming to object:", error);
            }
        },
        [viewerController]
    );

    // Toggle object visibility
    const toggleObjectVisibility = useCallback(
        (node: SceneNode, event: React.MouseEvent) => {
            event.stopPropagation(); // Prevent triggering selection

            try {
                node.object.visible = !node.object.visible;
                // Force re-render
                setSceneTree(buildSceneTree());
                console.log(`Object ${node.name} visibility: ${node.object.visible}`);
            } catch (error) {
                console.error("Error toggling object visibility:", error);
            }
        },
        [buildSceneTree]
    );

    // Select/Deselect all objects
    const setAllVisibility = useCallback(
        (visible: boolean) => {
            try {
                const scene = viewerController.getScene();
                if (!scene) return;

                scene.traverse((obj: any) => {
                    obj.visible = visible;
                });

                setSceneTree(buildSceneTree());
                console.log(`All objects ${visible ? "shown" : "hidden"}`);
            } catch (error) {
                console.error("Error setting all visibility:", error);
            }
        },
        [viewerController, buildSceneTree]
    );

    // Render tree node recursively
    const renderNode = (node: SceneNode, depth: number = 0): React.ReactNode => {
        const isExpanded = expandedNodes.has(node.id);
        const hasChildren = node.children.length > 0;
        const isSelected = selectedObject?.uuid === node.id;
        const isHovered = hoveredObject === node.id;

        // Get icon based on type
        const getIcon = (type: string) => {
            if (type.includes("PointCloud")) return "🌫️";
            if (type.includes("Tile")) return "🎲";
            if (type.includes("Mesh")) return "📐";
            if (type.includes("Group")) return "📁";
            if (type.includes("Points")) return "⚫";
            if (type.includes("Line")) return "📏";
            return "📦";
        };

        return (
            <div key={node.id}>
                <div
                    style={{
                        padding: "4px 8px",
                        paddingLeft: `${depth * 16 + 8}px`,
                        cursor: "pointer",
                        backgroundColor: isSelected
                            ? "rgba(76, 175, 80, 0.3)"
                            : isHovered
                            ? "rgba(255, 255, 255, 0.1)"
                            : "transparent",
                        borderRadius: "4px",
                        marginBottom: "2px",
                        display: "flex",
                        alignItems: "center",
                        fontSize: "0.85em",
                    }}
                    onClick={() => handleObjectClick(node)}
                    onDoubleClick={() => handleObjectDoubleClick(node)}
                    onMouseEnter={() => setHoveredObject(node.id)}
                    onMouseLeave={() => setHoveredObject(null)}
                >
                    {/* Expand/Collapse Icon */}
                    {hasChildren && (
                        <span
                            onClick={(e) => {
                                e.stopPropagation();
                                toggleExpand(node.id);
                            }}
                            style={{
                                marginRight: "4px",
                                cursor: "pointer",
                                userSelect: "none",
                                width: "12px",
                                display: "inline-block",
                            }}
                        >
                            {isExpanded ? "▼" : "▶"}
                        </span>
                    )}
                    {!hasChildren && (
                        <span
                            style={{ marginRight: "4px", width: "12px", display: "inline-block" }}
                        ></span>
                    )}

                    {/* Visibility Icon */}
                    <span
                        onClick={(e) => toggleObjectVisibility(node, e)}
                        style={{
                            marginRight: "6px",
                            cursor: "pointer",
                            fontSize: "0.9em",
                        }}
                        title={node.visible ? "Hide object" : "Show object"}
                    >
                        {node.visible ? "👁️" : "👁️‍🗨️"}
                    </span>

                    {/* Type Icon */}
                    <span style={{ marginRight: "6px", fontSize: "0.9em" }}>
                        {getIcon(node.type)}
                    </span>

                    {/* Object Name */}
                    <span
                        style={{
                            flex: 1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}
                    >
                        {node.name}
                    </span>

                    {/* Child Count */}
                    {hasChildren && (
                        <span style={{ fontSize: "0.75em", color: "#999", marginLeft: "4px" }}>
                            ({node.children.length})
                        </span>
                    )}
                </div>

                {/* Render children if expanded */}
                {hasChildren && isExpanded && (
                    <div>{node.children.map((child) => renderNode(child, depth + 1))}</div>
                )}
            </div>
        );
    };

    if (!viewerController || loadedModels.length === 0) {
        return null;
    }

    return (
        <>
            {/* Select All/Deselect All Buttons */}
            <div
                style={{
                    padding: "8px 16px",
                    borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                    display: "flex",
                    gap: "8px",
                }}
            >
                <button
                    onClick={() => setAllVisibility(true)}
                    style={{
                        flex: 1,
                        background: "rgba(76, 175, 80, 0.3)",
                        border: "1px solid rgba(76, 175, 80, 0.5)",
                        color: "white",
                        padding: "6px 8px",
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
                        padding: "6px 8px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.8em",
                    }}
                >
                    👁️‍🗨️ Hide All
                </button>
            </div>

            {/* Scene Tree */}
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
                {sceneTree.length > 0 ? (
                    sceneTree.map((node) => renderNode(node, 0))
                ) : (
                    <div style={{ padding: "16px", textAlign: "center", color: "#999" }}>
                        No objects in scene
                    </div>
                )}
            </div>

            {/* Object Details Panel */}
            {selectedObject && (
                <div
                    style={{
                        borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                        padding: "12px 16px",
                        backgroundColor: "rgba(0, 0, 0, 0.3)",
                        maxHeight: "200px",
                        overflowY: "auto",
                    }}
                >
                    <h5
                        style={{
                            margin: "0 0 8px 0",
                            fontSize: "0.9em",
                            color: "#4CAF50",
                            textAlign: "center",
                        }}
                    >
                        📋 Object Details
                    </h5>
                    <div style={{ fontSize: "0.8em", lineHeight: "1.6", textAlign: "left" }}>
                        <div>
                            <strong>Name:</strong> {selectedObject.name || "Unnamed"}
                        </div>
                        <div>
                            <strong>Type:</strong> {selectedObject.type}
                        </div>
                        {selectedObject.veerumId && (
                            <div>
                                <strong>Veerum ID:</strong> {selectedObject.veerumId}
                            </div>
                        )}
                        <div>
                            <strong>UUID:</strong> {selectedObject.uuid.substring(0, 8)}...
                        </div>
                        <div>
                            <strong>Visible:</strong> {selectedObject.visible ? "Yes" : "No"}
                        </div>
                        <div>
                            <strong>Children:</strong> {selectedObject.children?.length || 0}
                        </div>

                        {selectedObject.position && (
                            <div>
                                <strong>Position:</strong> ({selectedObject.position.x.toFixed(2)},
                                {selectedObject.position.y.toFixed(2)},
                                {selectedObject.position.z.toFixed(2)})
                            </div>
                        )}

                        {selectedObject.rotation && (
                            <div>
                                <strong>Rotation:</strong> (
                                {((selectedObject.rotation.x * 180) / Math.PI).toFixed(1)}°,
                                {((selectedObject.rotation.y * 180) / Math.PI).toFixed(1)}°,
                                {((selectedObject.rotation.z * 180) / Math.PI).toFixed(1)}°)
                            </div>
                        )}

                        {selectedObject.scale && (
                            <div>
                                <strong>Scale:</strong> ({selectedObject.scale.x.toFixed(2)},
                                {selectedObject.scale.y.toFixed(2)},
                                {selectedObject.scale.z.toFixed(2)})
                            </div>
                        )}

                        {selectedObject.boundingSphere && (
                            <div>
                                <strong>Bounding Radius:</strong>{" "}
                                {selectedObject.boundingSphere.radius.toFixed(2)}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Help Text */}
            <div
                style={{
                    padding: "8px 16px",
                    borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                    fontSize: "0.7em",
                    color: "#999",
                }}
            >
                Click: Select | Double-click: Zoom | 👁️: Toggle visibility
            </div>
        </>
    );
};

export default VeerumSceneGraph;
