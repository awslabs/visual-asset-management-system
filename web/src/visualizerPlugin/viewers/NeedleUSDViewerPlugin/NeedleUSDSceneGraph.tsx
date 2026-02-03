/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState, useRef } from "react";
import NeedleUSDTransformControls from "./NeedleUSDTransformControls";
import NeedleUSDObjectMaterialAssignment from "./NeedleUSDObjectMaterialAssignment";
import { MaterialLibraryItem } from "./NeedleUSDMaterialLibrary";

interface SceneNode {
    id: string;
    name: string;
    type: string;
    object: any;
    children: SceneNode[];
    visible: boolean;
    isFileRoot?: boolean;
}

interface TransformState {
    position: { x: number; y: number; z: number };
    rotation: { x: number; y: number; z: number };
    scale: { x: number; y: number; z: number };
    worldPosition?: { x: number; y: number; z: number };
    worldRotation?: { x: number; y: number; z: number };
    worldScale?: { x: number; y: number; z: number };
    isTopLevel?: boolean;
}

interface UndoState {
    objectId: string;
    transform: TransformState;
    timestamp: number;
}

interface NeedleUSDSceneGraphProps {
    scene: any;
    camera: any;
    usdRoot: any; // Can be single group or array of groups
    controls: any;
    selectedObjects?: any[];
    onSelectObjects?: (objects: any[]) => void;
    onClose?: () => void;
    originalTransforms?: Map<string, TransformState>;
    materialLibrary: Map<string, MaterialLibraryItem>;
    onAssignMaterial: (objectUuid: string, materialId: string) => void;
    onMakeUnique: (objectUuid: string) => void;
    onCreateAndAssign: (objectUuid: string) => void;
    onEditMaterial: (materialId: string) => void;
    animationPaused?: boolean;
}

const NeedleUSDSceneGraph: React.FC<NeedleUSDSceneGraphProps> = ({
    scene,
    camera,
    usdRoot,
    controls,
    selectedObjects: externalSelectedObjects = [],
    onSelectObjects,
    onClose,
    originalTransforms: externalOriginalTransforms,
    materialLibrary,
    onAssignMaterial,
    onMakeUnique,
    onCreateAndAssign,
    onEditMaterial,
    animationPaused = true,
}) => {
    const [sceneTree, setSceneTree] = useState<SceneNode[]>([]);
    const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
    const [hoveredObject, setHoveredObject] = useState<string | null>(null);
    const [undoStack, setUndoStack] = useState<UndoState[]>([]);
    const maxUndoStackSize = 20;
    const sceneTreeContainerRef = useRef<HTMLDivElement>(null);
    const [detailsTab, setDetailsTab] = useState<"transform" | "material" | "details">("details");
    const lastActiveTabRef = useRef<"transform" | "material" | "details">("details");

    // Resizable divider state
    const [treeHeight, setTreeHeight] = useState<number>(300); // Default height in pixels
    const [isDragging, setIsDragging] = useState(false);
    const dividerRef = useRef<HTMLDivElement>(null);
    const containerHeightRef = useRef<number>(0);

    const THREE = (window as any).THREE;

    // Build scene tree from Three.js scene
    const buildSceneTree = useCallback(() => {
        if (!usdRoot) return [];

        try {
            const buildNode = (obj: any, isFileRoot: boolean = false): SceneNode => {
                const node: SceneNode = {
                    id: obj.uuid,
                    name: obj.name || obj.type || "Unnamed",
                    type: obj.type,
                    object: obj,
                    children: [],
                    visible: obj.visible !== false,
                    isFileRoot,
                };

                // Add children recursively
                if (obj.children && obj.children.length > 0) {
                    node.children = obj.children
                        .filter((child: any) => {
                            // Filter out cameras and lights
                            const type = child.type;
                            return !type.includes("Camera") && !type.includes("Light");
                        })
                        .map((child: any) => buildNode(child, false));
                }

                return node;
            };

            // Handle both single group and array of groups
            const fileGroups = Array.isArray(usdRoot) ? usdRoot : [usdRoot];

            // Build tree with file roots
            return fileGroups.map((group) => buildNode(group, true));
        } catch (error) {
            console.error("Error building scene tree:", error);
            return [];
        }
    }, [usdRoot]);

    // Refresh scene tree
    useEffect(() => {
        const tree = buildSceneTree();
        setSceneTree(tree);

        // Auto-expand all file root nodes
        if (tree.length > 0) {
            const rootIds = tree.map((node) => node.id);
            setExpandedNodes(new Set(rootIds));
        }
    }, [buildSceneTree]);

    // Auto-expand parent nodes and scroll to selected objects when selection changes
    useEffect(() => {
        if (externalSelectedObjects.length > 0) {
            // Find all parent nodes that need to be expanded
            const nodesToExpand = new Set<string>();

            const findParentNodes = (tree: SceneNode[], targetUuid: string): boolean => {
                for (const node of tree) {
                    if (node.object.uuid === targetUuid) {
                        return true;
                    }
                    if (node.children.length > 0) {
                        if (findParentNodes(node.children, targetUuid)) {
                            nodesToExpand.add(node.id);
                            return true;
                        }
                    }
                }
                return false;
            };

            // Find parents for all selected objects
            externalSelectedObjects.forEach((obj) => {
                findParentNodes(sceneTree, obj.uuid);
            });

            // Expand all parent nodes
            if (nodesToExpand.size > 0) {
                setExpandedNodes((prev) => {
                    const newSet = new Set(prev);
                    nodesToExpand.forEach((id) => newSet.add(id));
                    return newSet;
                });
                console.log(`Scene Graph: Auto-expanded ${nodesToExpand.size} parent nodes`);
            }

            // Auto-scroll to first selected object
            if (sceneTreeContainerRef.current) {
                // Small delay to ensure DOM is updated after expansion
                setTimeout(() => {
                    const firstSelectedUuid = externalSelectedObjects[0].uuid;
                    const selectedElement = sceneTreeContainerRef.current?.querySelector(
                        `[data-node-id="${firstSelectedUuid}"]`
                    );

                    if (selectedElement) {
                        selectedElement.scrollIntoView({
                            behavior: "smooth",
                            block: "center",
                        });
                        console.log("Scene Graph: Scrolled to selected object");
                    }
                }, 150);
            }
        }
    }, [externalSelectedObjects, sceneTree]);

    // Auto-adjust divider position based on selection
    useEffect(() => {
        if (!dividerRef.current) return;

        const container = dividerRef.current.parentElement;
        if (!container) return;

        const containerRect = container.getBoundingClientRect();

        if (externalSelectedObjects.length === 0) {
            // No selection: Move divider to bottom (maximize tree view)
            const newHeight = containerRect.height - 100; // Leave small space at bottom
            setTreeHeight(Math.max(300, newHeight));
        } else {
            // Has selection: Move divider up to show controls
            setTreeHeight(300); // Default height to show controls
        }
    }, [externalSelectedObjects.length]);

    // Remember last active tab and restore it when switching objects
    useEffect(() => {
        if (externalSelectedObjects.length === 1) {
            // Restore last active tab when selecting an object
            setDetailsTab(lastActiveTabRef.current);
        }
    }, [externalSelectedObjects]);

    // Update last active tab ref when tab changes
    useEffect(() => {
        lastActiveTabRef.current = detailsTab;
    }, [detailsTab]);

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

    // Handle divider drag
    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isDragging || !dividerRef.current) return;

            const container = dividerRef.current.parentElement;
            if (!container) return;

            const containerRect = container.getBoundingClientRect();
            const mouseY = e.clientY - containerRect.top;

            // Calculate new tree height with constraints
            const minTreeHeight = 150;
            const minDetailsHeight = 200;
            const maxTreeHeight = containerRect.height - minDetailsHeight;

            const newHeight = Math.max(minTreeHeight, Math.min(mouseY, maxTreeHeight));
            setTreeHeight(newHeight);
        };

        const handleMouseUp = () => {
            if (isDragging) {
                setIsDragging(false);
                document.body.style.cursor = "";
                document.body.style.userSelect = "";
            }
        };

        if (isDragging) {
            document.body.style.cursor = "ns-resize";
            document.body.style.userSelect = "none";
            document.addEventListener("mousemove", handleMouseMove);
            document.addEventListener("mouseup", handleMouseUp);
        }

        return () => {
            document.removeEventListener("mousemove", handleMouseMove);
            document.removeEventListener("mouseup", handleMouseUp);
        };
    }, [isDragging]);

    // Handle object selection - only select the clicked object, not its children
    const handleObjectClick = useCallback(
        (node: SceneNode, event: React.MouseEvent) => {
            if (!onSelectObjects) {
                console.warn("Scene Graph: onSelectObjects callback is missing!");
                return;
            }

            // Only select the clicked object itself, not its children
            const objectToSelect = node.object;

            // Check if this object is already selected
            const isSelected = externalSelectedObjects.some(
                (selected) => selected.uuid === objectToSelect.uuid
            );

            if (event.ctrlKey) {
                // Ctrl+Click: Add/remove from selection
                if (isSelected) {
                    // Remove from selection
                    const newSelection = externalSelectedObjects.filter(
                        (obj) => obj.uuid !== objectToSelect.uuid
                    );
                    console.log(`Scene Graph: Removing from selection`, node.name);
                    onSelectObjects(newSelection);
                } else {
                    // Add to selection
                    console.log(`Scene Graph: Adding to selection`, node.name);
                    onSelectObjects([...externalSelectedObjects, objectToSelect]);
                }
            } else {
                // Regular click: Toggle or replace selection
                if (isSelected && externalSelectedObjects.length === 1) {
                    // Already selected and only selection - deselect
                    console.log("Scene Graph: Deselecting", node.name);
                    onSelectObjects([]);
                    setUndoStack([]);
                } else {
                    // Select this object only
                    console.log(`Scene Graph: Selecting`, node.name);
                    onSelectObjects([objectToSelect]);
                    setUndoStack([]);
                }
            }
        },
        [externalSelectedObjects, onSelectObjects]
    );

    // Handle object double-click (zoom to object)
    const handleObjectDoubleClick = useCallback(
        (node: SceneNode) => {
            if (!camera || !controls || !THREE) return;

            try {
                const box = new THREE.Box3().setFromObject(node.object);
                const size = box.getSize(new THREE.Vector3());
                const center = box.getCenter(new THREE.Vector3());

                const maxSize = Math.max(size.x, size.y, size.z);
                const distance = maxSize * 2;

                camera.position.set(
                    center.x + distance * 0.5,
                    center.y + distance * 0.5,
                    center.z + distance * 0.5
                );
                camera.lookAt(center);
                controls.setTarget(center.x, center.y, center.z);

                console.log("Zoomed to object:", node.name);
            } catch (error) {
                console.error("Error zooming to object:", error);
            }
        },
        [camera, controls, THREE]
    );

    // Toggle object visibility
    const toggleObjectVisibility = useCallback(
        (node: SceneNode, event: React.MouseEvent) => {
            event.stopPropagation();

            try {
                node.object.visible = !node.object.visible;
                setSceneTree(buildSceneTree());
                console.log(`Object ${node.name} visibility: ${node.object.visible}`);
            } catch (error) {
                console.error("Error toggling object visibility:", error);
            }
        },
        [buildSceneTree]
    );

    // Show/Hide all objects
    const setAllVisibility = useCallback(
        (visible: boolean) => {
            if (!usdRoot) return;

            try {
                const fileGroups = Array.isArray(usdRoot) ? usdRoot : [usdRoot];

                fileGroups.forEach((group: any) => {
                    group.traverse((obj: any) => {
                        obj.visible = visible;
                    });
                });

                setSceneTree(buildSceneTree());
                console.log(`All objects ${visible ? "shown" : "hidden"}`);
            } catch (error) {
                console.error("Error setting all visibility:", error);
            }
        },
        [usdRoot, buildSceneTree]
    );

    // Render tree node recursively
    const renderNode = (node: SceneNode, depth: number = 0): React.ReactNode => {
        const isExpanded = expandedNodes.has(node.id);
        const hasChildren = node.children.length > 0;
        // Check if this node's object is in the selected objects array
        const isSelected = externalSelectedObjects.some((obj) => obj.uuid === node.object.uuid);
        const isHovered = hoveredObject === node.id;

        // Get icon based on type
        const getIcon = (type: string, isFileRoot: boolean) => {
            if (isFileRoot) return "📄"; // File icon for root nodes
            if (type.includes("Mesh")) return "📐";
            if (type.includes("Group")) return "📁";
            if (type.includes("Points")) return "⚫";
            if (type.includes("Line")) return "📏";
            return "📦";
        };

        return (
            <div key={node.id}>
                <div
                    data-node-id={node.object.uuid}
                    style={{
                        padding: "4px 8px",
                        paddingLeft: `${depth * 16 + 8}px`,
                        cursor: "pointer",
                        backgroundColor: isSelected
                            ? "rgba(76, 175, 80, 0.3)"
                            : isHovered
                            ? "rgba(255, 255, 255, 0.1)"
                            : node.isFileRoot
                            ? "rgba(33, 150, 243, 0.15)"
                            : "transparent",
                        borderRadius: "4px",
                        marginBottom: "2px",
                        display: "flex",
                        alignItems: "center",
                        fontSize: "0.85em",
                        fontWeight: node.isFileRoot ? "bold" : "normal",
                        borderLeft: node.isFileRoot ? "3px solid #2196F3" : "none",
                    }}
                    onClick={(e) => handleObjectClick(node, e)}
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
                        {getIcon(node.type, node.isFileRoot || false)}
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

    if (!scene || !usdRoot) {
        return null;
    }

    return (
        <>
            {/* Action Buttons */}
            <div
                style={{
                    padding: "8px 16px",
                    borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px",
                }}
            >
                {/* Clear Selection Button */}
                {externalSelectedObjects.length > 0 && onSelectObjects && (
                    <button
                        onClick={() => {
                            console.log("Scene Graph: Clearing all selections");
                            onSelectObjects([]);
                            setUndoStack([]);
                        }}
                        style={{
                            background: "rgba(156, 39, 176, 0.3)",
                            border: "1px solid rgba(156, 39, 176, 0.5)",
                            color: "white",
                            padding: "6px 8px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                            fontWeight: "bold",
                        }}
                        title="Clear all selections"
                    >
                        ✕ Clear Selection ({externalSelectedObjects.length})
                    </button>
                )}

                {/* Show/Hide All Buttons */}
                <div style={{ display: "flex", gap: "6px" }}>
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
            </div>

            {/* Scene Tree */}
            <div
                ref={sceneTreeContainerRef}
                style={{
                    height: `${treeHeight}px`,
                    overflowY: "auto",
                    overflowX: "hidden",
                    padding: "8px",
                    scrollbarWidth: "thin",
                    scrollbarColor: "rgba(255, 255, 255, 0.5) transparent",
                }}
            >
                {sceneTree.length > 0 ? (
                    <>
                        {sceneTree.length > 1 && (
                            <div
                                style={{
                                    padding: "4px 8px",
                                    marginBottom: "8px",
                                    fontSize: "0.75em",
                                    color: "#4CAF50",
                                    textAlign: "center",
                                    backgroundColor: "rgba(76, 175, 80, 0.1)",
                                    borderRadius: "4px",
                                }}
                            >
                                📁 {sceneTree.length} USD files loaded
                            </div>
                        )}
                        {sceneTree.map((node) => renderNode(node, 0))}
                    </>
                ) : (
                    <div style={{ padding: "16px", textAlign: "center", color: "#999" }}>
                        No objects in scene
                    </div>
                )}
            </div>

            {/* Resizable Divider */}
            <div
                ref={dividerRef}
                onMouseDown={() => setIsDragging(true)}
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
                <span
                    style={{
                        fontSize: "0.6em",
                        color: "rgba(255, 255, 255, 0.5)",
                        letterSpacing: "2px",
                    }}
                >
                    ⋮⋮
                </span>
            </div>

            {/* Bottom Section Container (Details + Controls) */}
            <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
                {/* Sub-Tabs for Transform, Material, and Details */}
                {externalSelectedObjects.length === 1 && (
                    <>
                        {/* Sub-Tab Buttons */}
                        <div
                            style={{
                                borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                                display: "flex",
                                backgroundColor: "rgba(0, 0, 0, 0.2)",
                            }}
                        >
                            <button
                                onClick={() => setDetailsTab("transform")}
                                style={{
                                    flex: 1,
                                    background:
                                        detailsTab === "transform"
                                            ? "rgba(33, 150, 243, 0.3)"
                                            : "transparent",
                                    border: "none",
                                    borderBottom:
                                        detailsTab === "transform"
                                            ? "2px solid #2196F3"
                                            : "2px solid transparent",
                                    color: "white",
                                    padding: "8px 12px",
                                    cursor: "pointer",
                                    fontSize: "0.75em",
                                    fontWeight: detailsTab === "transform" ? "bold" : "normal",
                                }}
                            >
                                🎛️ Transform
                            </button>
                            <button
                                onClick={() => setDetailsTab("material")}
                                style={{
                                    flex: 1,
                                    background:
                                        detailsTab === "material"
                                            ? "rgba(233, 30, 99, 0.3)"
                                            : "transparent",
                                    border: "none",
                                    borderBottom:
                                        detailsTab === "material"
                                            ? "2px solid #E91E63"
                                            : "2px solid transparent",
                                    color: "white",
                                    padding: "8px 12px",
                                    cursor: "pointer",
                                    fontSize: "0.75em",
                                    fontWeight: detailsTab === "material" ? "bold" : "normal",
                                }}
                            >
                                🎨 Material
                            </button>
                            <button
                                onClick={() => setDetailsTab("details")}
                                style={{
                                    flex: 1,
                                    background:
                                        detailsTab === "details"
                                            ? "rgba(76, 175, 80, 0.3)"
                                            : "transparent",
                                    border: "none",
                                    borderBottom:
                                        detailsTab === "details"
                                            ? "2px solid #4CAF50"
                                            : "2px solid transparent",
                                    color: "white",
                                    padding: "8px 12px",
                                    cursor: "pointer",
                                    fontSize: "0.75em",
                                    fontWeight: detailsTab === "details" ? "bold" : "normal",
                                }}
                            >
                                📋 Details
                            </button>
                        </div>

                        {/* Transform Controls */}
                        {detailsTab === "transform" && (
                            <NeedleUSDTransformControls
                                selectedObject={externalSelectedObjects[0]}
                                onTransformChange={(object: any, transform: any) => {
                                    // Save current state to undo stack
                                    const currentState: UndoState = {
                                        objectId: object.uuid,
                                        transform: {
                                            position: {
                                                x: object.position.x,
                                                y: object.position.y,
                                                z: object.position.z,
                                            },
                                            rotation: {
                                                x: object.rotation.x,
                                                y: object.rotation.y,
                                                z: object.rotation.z,
                                            },
                                            scale: {
                                                x: object.scale.x,
                                                y: object.scale.y,
                                                z: object.scale.z,
                                            },
                                        },
                                        timestamp: Date.now(),
                                    };

                                    setUndoStack((prev) => {
                                        const newStack = [...prev, currentState];
                                        return newStack.slice(-maxUndoStackSize);
                                    });

                                    // Apply transform
                                    object.position.set(
                                        transform.position.x,
                                        transform.position.y,
                                        transform.position.z
                                    );
                                    object.rotation.set(
                                        (transform.rotation.x * Math.PI) / 180,
                                        (transform.rotation.y * Math.PI) / 180,
                                        (transform.rotation.z * Math.PI) / 180
                                    );
                                    object.scale.set(
                                        transform.scale.x,
                                        transform.scale.y,
                                        transform.scale.z
                                    );

                                    object.updateMatrix();
                                    object.updateMatrixWorld(true);
                                }}
                                onUndo={() => {
                                    if (
                                        undoStack.length === 0 ||
                                        externalSelectedObjects.length !== 1
                                    )
                                        return;

                                    const selectedObj = externalSelectedObjects[0];
                                    const lastState = undoStack[undoStack.length - 1];
                                    setUndoStack((prev) => prev.slice(0, -1));

                                    const t = lastState.transform;
                                    selectedObj.position.set(
                                        t.position.x,
                                        t.position.y,
                                        t.position.z
                                    );
                                    selectedObj.rotation.set(
                                        t.rotation.x,
                                        t.rotation.y,
                                        t.rotation.z
                                    );
                                    selectedObj.scale.set(t.scale.x, t.scale.y, t.scale.z);

                                    selectedObj.updateMatrix();
                                    selectedObj.updateMatrixWorld(true);
                                }}
                                onReset={() => {
                                    if (
                                        externalSelectedObjects.length !== 1 ||
                                        !externalOriginalTransforms
                                    )
                                        return;

                                    const selectedObj = externalSelectedObjects[0];
                                    const original = externalOriginalTransforms.get(
                                        selectedObj.uuid
                                    );
                                    if (!original) {
                                        console.warn(
                                            "No original transform found for object:",
                                            selectedObj.name
                                        );
                                        return;
                                    }

                                    console.log("Resetting object:", selectedObj.name);
                                    console.log("Is top-level:", original.isTopLevel);
                                    console.log(
                                        "Original local:",
                                        original.position,
                                        original.rotation,
                                        original.scale
                                    );
                                    console.log(
                                        "Original world:",
                                        original.worldPosition,
                                        original.worldRotation,
                                        original.worldScale
                                    );

                                    if (
                                        original.isTopLevel &&
                                        original.worldPosition &&
                                        original.worldRotation &&
                                        original.worldScale
                                    ) {
                                        // Top-level object: Use world coordinates
                                        // Need to convert world coordinates to local space relative to parent
                                        const parent = selectedObj.parent;
                                        if (parent) {
                                            // Get parent's inverse world matrix
                                            const parentWorldMatrix = new THREE.Matrix4();
                                            parent.updateMatrixWorld(true);
                                            parentWorldMatrix.copy(parent.matrixWorld);
                                            const parentInverse = new THREE.Matrix4()
                                                .copy(parentWorldMatrix)
                                                .invert();

                                            // Create target world transform
                                            const targetWorldPos = new THREE.Vector3(
                                                original.worldPosition.x,
                                                original.worldPosition.y,
                                                original.worldPosition.z
                                            );
                                            const targetWorldRot = new THREE.Euler(
                                                original.worldRotation.x,
                                                original.worldRotation.y,
                                                original.worldRotation.z
                                            );
                                            const targetWorldScale = new THREE.Vector3(
                                                original.worldScale.x,
                                                original.worldScale.y,
                                                original.worldScale.z
                                            );

                                            // Convert world transform to local space
                                            const worldMatrix = new THREE.Matrix4();
                                            worldMatrix.compose(
                                                targetWorldPos,
                                                new THREE.Quaternion().setFromEuler(targetWorldRot),
                                                targetWorldScale
                                            );

                                            const localMatrix = new THREE.Matrix4();
                                            localMatrix.multiplyMatrices(
                                                parentInverse,
                                                worldMatrix
                                            );

                                            // Extract local transform
                                            const localPos = new THREE.Vector3();
                                            const localQuat = new THREE.Quaternion();
                                            const localScale = new THREE.Vector3();
                                            localMatrix.decompose(localPos, localQuat, localScale);
                                            const localRot = new THREE.Euler().setFromQuaternion(
                                                localQuat
                                            );

                                            selectedObj.position.copy(localPos);
                                            selectedObj.rotation.copy(localRot);
                                            selectedObj.scale.copy(localScale);

                                            console.log(
                                                "Reset using world coordinates (converted to local)"
                                            );
                                        } else {
                                            // No parent, world = local
                                            selectedObj.position.set(
                                                original.worldPosition.x,
                                                original.worldPosition.y,
                                                original.worldPosition.z
                                            );
                                            selectedObj.rotation.set(
                                                original.worldRotation.x,
                                                original.worldRotation.y,
                                                original.worldRotation.z
                                            );
                                            selectedObj.scale.set(
                                                original.worldScale.x,
                                                original.worldScale.y,
                                                original.worldScale.z
                                            );
                                            console.log(
                                                "Reset using world coordinates (no parent)"
                                            );
                                        }
                                    } else {
                                        // Sub-object: Use local coordinates
                                        selectedObj.position.set(
                                            original.position.x,
                                            original.position.y,
                                            original.position.z
                                        );
                                        selectedObj.rotation.set(
                                            original.rotation.x,
                                            original.rotation.y,
                                            original.rotation.z
                                        );
                                        selectedObj.scale.set(
                                            original.scale.x,
                                            original.scale.y,
                                            original.scale.z
                                        );
                                        console.log("Reset using local coordinates");
                                    }

                                    selectedObj.updateMatrix();
                                    selectedObj.updateMatrixWorld(true);
                                    setUndoStack([]);
                                    console.log("Reset complete");
                                }}
                                canUndo={undoStack.length > 0}
                                animationPlaying={!animationPaused}
                            />
                        )}

                        {/* Material Assignment */}
                        {detailsTab === "material" && (
                            <NeedleUSDObjectMaterialAssignment
                                selectedObject={externalSelectedObjects[0]}
                                materialLibrary={materialLibrary}
                                onAssignMaterial={onAssignMaterial}
                                onMakeUnique={onMakeUnique}
                                onCreateAndAssign={onCreateAndAssign}
                                onEditMaterial={onEditMaterial}
                            />
                        )}

                        {/* Object Details */}
                        {detailsTab === "details" && (
                            <div
                                style={{
                                    flex: 1,
                                    overflowY: "auto",
                                    padding: "12px 16px",
                                    backgroundColor: "rgba(0, 0, 0, 0.3)",
                                }}
                            >
                                <h5
                                    style={{
                                        margin: "0 0 12px 0",
                                        fontSize: "0.9em",
                                        color: "#4CAF50",
                                    }}
                                >
                                    📋 Object Details
                                </h5>
                                <div
                                    style={{
                                        fontSize: "0.8em",
                                        lineHeight: "1.8",
                                        textAlign: "left",
                                    }}
                                >
                                    {/* Basic Info */}
                                    <div style={{ marginBottom: "12px" }}>
                                        <div
                                            style={{
                                                color: "#4CAF50",
                                                fontWeight: "bold",
                                                marginBottom: "6px",
                                            }}
                                        >
                                            Basic Info:
                                        </div>
                                        <div>
                                            <strong>Name:</strong>{" "}
                                            {externalSelectedObjects[0].name || "Unnamed"}
                                        </div>
                                        <div>
                                            <strong>Type:</strong> {externalSelectedObjects[0].type}
                                        </div>
                                        <div>
                                            <strong>UUID:</strong>{" "}
                                            {externalSelectedObjects[0].uuid.substring(0, 8)}...
                                        </div>
                                        <div>
                                            <strong>Visible:</strong>{" "}
                                            {externalSelectedObjects[0].visible ? "Yes" : "No"}
                                        </div>
                                        <div>
                                            <strong>Children:</strong>{" "}
                                            {externalSelectedObjects[0].children?.length || 0}
                                        </div>
                                    </div>

                                    {/* Geometry Info */}
                                    {(externalSelectedObjects[0].geometry ||
                                        (externalSelectedObjects[0].children &&
                                            externalSelectedObjects[0].children.length > 0)) && (
                                        <div style={{ marginBottom: "12px" }}>
                                            <div
                                                style={{
                                                    color: "#2196F3",
                                                    fontWeight: "bold",
                                                    marginBottom: "6px",
                                                }}
                                            >
                                                Geometry:
                                            </div>
                                            {externalSelectedObjects[0].geometry && (
                                                <>
                                                    {externalSelectedObjects[0].geometry.attributes
                                                        .position && (
                                                        <div>
                                                            <strong>Vertices:</strong>{" "}
                                                            {externalSelectedObjects[0].geometry.attributes.position.count.toLocaleString()}
                                                        </div>
                                                    )}
                                                    {externalSelectedObjects[0].geometry.index && (
                                                        <div>
                                                            <strong>Faces:</strong>{" "}
                                                            {Math.floor(
                                                                externalSelectedObjects[0].geometry
                                                                    .index.count / 3
                                                            ).toLocaleString()}
                                                        </div>
                                                    )}
                                                </>
                                            )}

                                            {/* Show total vertex count for groups with children */}
                                            {externalSelectedObjects[0].children &&
                                                externalSelectedObjects[0].children.length > 0 &&
                                                (() => {
                                                    let totalVertices = 0;
                                                    let totalFaces = 0;
                                                    externalSelectedObjects[0].traverse(
                                                        (child: any) => {
                                                            if (
                                                                child.geometry?.attributes?.position
                                                            ) {
                                                                totalVertices +=
                                                                    child.geometry.attributes
                                                                        .position.count;
                                                            }
                                                            if (child.geometry?.index) {
                                                                totalFaces += Math.floor(
                                                                    child.geometry.index.count / 3
                                                                );
                                                            }
                                                        }
                                                    );

                                                    return totalVertices > 0 ? (
                                                        <>
                                                            <div>
                                                                <strong>Total Vertices:</strong>{" "}
                                                                {totalVertices.toLocaleString()}
                                                            </div>
                                                            {totalFaces > 0 && (
                                                                <div>
                                                                    <strong>Total Faces:</strong>{" "}
                                                                    {totalFaces.toLocaleString()}
                                                                </div>
                                                            )}
                                                        </>
                                                    ) : null;
                                                })()}
                                        </div>
                                    )}

                                    {/* Transform Info */}
                                    {externalSelectedObjects[0].position && (
                                        <div style={{ marginBottom: "12px" }}>
                                            <div
                                                style={{
                                                    color: "#FF9800",
                                                    fontWeight: "bold",
                                                    marginBottom: "6px",
                                                }}
                                            >
                                                Transforms:
                                            </div>
                                            {(() => {
                                                const obj = externalSelectedObjects[0];
                                                // Get world position
                                                const worldPos = new THREE.Vector3();
                                                obj.getWorldPosition(worldPos);
                                                // Get world rotation
                                                const worldQuat = new THREE.Quaternion();
                                                obj.getWorldQuaternion(worldQuat);
                                                const worldEuler =
                                                    new THREE.Euler().setFromQuaternion(worldQuat);
                                                // Get world scale
                                                const worldScale = new THREE.Vector3();
                                                obj.getWorldScale(worldScale);

                                                return (
                                                    <>
                                                        <div>
                                                            <strong>Local Position:</strong> (
                                                            {obj.position.x.toFixed(2)},
                                                            {obj.position.y.toFixed(2)},
                                                            {obj.position.z.toFixed(2)})
                                                        </div>
                                                        <div>
                                                            <strong>World Position:</strong> (
                                                            {worldPos.x.toFixed(2)},
                                                            {worldPos.y.toFixed(2)},
                                                            {worldPos.z.toFixed(2)})
                                                        </div>
                                                        <div>
                                                            <strong>Local Rotation:</strong> (
                                                            {(
                                                                (obj.rotation.x * 180) /
                                                                Math.PI
                                                            ).toFixed(1)}
                                                            °,
                                                            {(
                                                                (obj.rotation.y * 180) /
                                                                Math.PI
                                                            ).toFixed(1)}
                                                            °,
                                                            {(
                                                                (obj.rotation.z * 180) /
                                                                Math.PI
                                                            ).toFixed(1)}
                                                            °)
                                                        </div>
                                                        <div>
                                                            <strong>World Rotation:</strong> (
                                                            {(
                                                                (worldEuler.x * 180) /
                                                                Math.PI
                                                            ).toFixed(1)}
                                                            °,
                                                            {(
                                                                (worldEuler.y * 180) /
                                                                Math.PI
                                                            ).toFixed(1)}
                                                            °,
                                                            {(
                                                                (worldEuler.z * 180) /
                                                                Math.PI
                                                            ).toFixed(1)}
                                                            °)
                                                        </div>
                                                        <div>
                                                            <strong>Local Scale:</strong> (
                                                            {obj.scale.x.toFixed(2)},
                                                            {obj.scale.y.toFixed(2)},
                                                            {obj.scale.z.toFixed(2)})
                                                        </div>
                                                        <div>
                                                            <strong>World Scale:</strong> (
                                                            {worldScale.x.toFixed(2)},
                                                            {worldScale.y.toFixed(2)},
                                                            {worldScale.z.toFixed(2)})
                                                        </div>
                                                    </>
                                                );
                                            })()}
                                        </div>
                                    )}

                                    {/* Metadata & Attributes */}
                                    {(() => {
                                        const obj = externalSelectedObjects[0];
                                        const userData = obj.userData || {};
                                        const geometryUserData = obj.geometry?.userData || {};
                                        const allMetadata = { ...userData, ...geometryUserData };

                                        // Filter out internal properties and empty objects
                                        const metadataEntries = Object.entries(allMetadata).filter(
                                            ([key, value]) =>
                                                !key.startsWith("_") &&
                                                value !== null &&
                                                value !== undefined
                                        );

                                        if (metadataEntries.length > 0) {
                                            return (
                                                <div style={{ marginBottom: "12px" }}>
                                                    <div
                                                        style={{
                                                            color: "#9C27B0",
                                                            fontWeight: "bold",
                                                            marginBottom: "6px",
                                                        }}
                                                    >
                                                        Metadata & Attributes:
                                                    </div>
                                                    {metadataEntries.map(([key, value]) => {
                                                        let displayValue: string;
                                                        if (
                                                            typeof value === "object" &&
                                                            value !== null
                                                        ) {
                                                            try {
                                                                displayValue = JSON.stringify(
                                                                    value,
                                                                    null,
                                                                    2
                                                                );
                                                            } catch {
                                                                displayValue = String(value);
                                                            }
                                                        } else {
                                                            displayValue = String(value);
                                                        }

                                                        return (
                                                            <div
                                                                key={key}
                                                                style={{ marginBottom: "4px" }}
                                                            >
                                                                <strong>{key}:</strong>{" "}
                                                                {displayValue.length > 100 ? (
                                                                    <div
                                                                        style={{
                                                                            marginTop: "4px",
                                                                            padding: "6px",
                                                                            backgroundColor:
                                                                                "rgba(255, 255, 255, 0.05)",
                                                                            borderRadius: "4px",
                                                                            fontSize: "0.9em",
                                                                            fontFamily: "monospace",
                                                                            whiteSpace: "pre-wrap",
                                                                            wordBreak: "break-word",
                                                                        }}
                                                                    >
                                                                        {displayValue}
                                                                    </div>
                                                                ) : (
                                                                    <span style={{ color: "#ccc" }}>
                                                                        {displayValue}
                                                                    </span>
                                                                )}
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            );
                                        }
                                        return null;
                                    })()}
                                </div>
                            </div>
                        )}
                    </>
                )}

                {/* Multi-Selection Info */}
                {externalSelectedObjects.length > 1 && (
                    <div
                        style={{
                            borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                            padding: "12px 16px",
                            backgroundColor: "rgba(76, 175, 80, 0.15)",
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
                            ✓ {externalSelectedObjects.length} Objects Selected
                        </h5>
                        <div
                            style={{
                                fontSize: "0.75em",
                                lineHeight: "1.6",
                                maxHeight: "120px",
                                overflowY: "auto",
                            }}
                        >
                            {externalSelectedObjects.map((obj, idx) => (
                                <div key={obj.uuid} style={{ padding: "2px 0", color: "#ccc" }}>
                                    {idx + 1}. {obj.name || "Unnamed"}
                                </div>
                            ))}
                        </div>
                        <div
                            style={{
                                marginTop: "8px",
                                padding: "8px",
                                backgroundColor: "rgba(255, 152, 0, 0.2)",
                                borderRadius: "4px",
                                fontSize: "0.7em",
                                color: "#FF9800",
                                textAlign: "center",
                            }}
                        >
                            🔒 Transform controls disabled for multi-selection
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
                    Click: Select | Ctrl+Click: Multi-select | Double-click: Zoom | 👁️: Toggle
                    visibility
                </div>
            </div>
        </>
    );
};

export default NeedleUSDSceneGraph;
