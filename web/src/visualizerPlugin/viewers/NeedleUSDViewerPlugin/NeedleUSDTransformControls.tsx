/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";

interface TransformState {
    position: { x: number; y: number; z: number };
    rotation: { x: number; y: number; z: number };
    scale: { x: number; y: number; z: number };
}

interface NeedleUSDTransformControlsProps {
    selectedObject: any;
    onTransformChange: (object: any, transform: TransformState) => void;
    onUndo: () => void;
    onReset: () => void;
    canUndo: boolean;
    animationPlaying?: boolean;
}

const NeedleUSDTransformControls: React.FC<NeedleUSDTransformControlsProps> = ({
    selectedObject,
    onTransformChange,
    onUndo,
    onReset,
    canUndo,
    animationPlaying = false,
}) => {
    const [transform, setTransform] = useState<TransformState>({
        position: { x: 0, y: 0, z: 0 },
        rotation: { x: 0, y: 0, z: 0 },
        scale: { x: 1, y: 1, z: 1 },
    });
    const [updateTrigger, setUpdateTrigger] = useState(0);
    const [coordinateSpace, setCoordinateSpace] = useState<"local" | "world">("local");

    const THREE = (window as any).THREE;

    // Update transform state when selected object changes or after reset
    useEffect(() => {
        if (selectedObject && THREE) {
            if (coordinateSpace === "local") {
                setTransform({
                    position: {
                        x: selectedObject.position.x,
                        y: selectedObject.position.y,
                        z: selectedObject.position.z,
                    },
                    rotation: {
                        x: (selectedObject.rotation.x * 180) / Math.PI,
                        y: (selectedObject.rotation.y * 180) / Math.PI,
                        z: (selectedObject.rotation.z * 180) / Math.PI,
                    },
                    scale: {
                        x: selectedObject.scale.x,
                        y: selectedObject.scale.y,
                        z: selectedObject.scale.z,
                    },
                });
            } else {
                // World space
                const worldPos = new THREE.Vector3();
                selectedObject.getWorldPosition(worldPos);
                
                const worldQuat = new THREE.Quaternion();
                selectedObject.getWorldQuaternion(worldQuat);
                const worldEuler = new THREE.Euler().setFromQuaternion(worldQuat);
                
                const worldScale = new THREE.Vector3();
                selectedObject.getWorldScale(worldScale);
                
                setTransform({
                    position: {
                        x: worldPos.x,
                        y: worldPos.y,
                        z: worldPos.z,
                    },
                    rotation: {
                        x: (worldEuler.x * 180) / Math.PI,
                        y: (worldEuler.y * 180) / Math.PI,
                        z: (worldEuler.z * 180) / Math.PI,
                    },
                    scale: {
                        x: worldScale.x,
                        y: worldScale.y,
                        z: worldScale.z,
                    },
                });
            }
        }
    }, [selectedObject, updateTrigger, coordinateSpace, THREE]);

    const updateTransform = (
        type: "position" | "rotation" | "scale",
        axis: "x" | "y" | "z",
        value: number
    ) => {
        const newTransform = {
            ...transform,
            [type]: {
                ...transform[type],
                [axis]: value,
            },
        };
        setTransform(newTransform);
        
        if (coordinateSpace === "local") {
            // Direct local space update
            onTransformChange(selectedObject, newTransform);
        } else {
            // World space update - need to convert to local
            // For now, just update local (world space editing is complex with parent transforms)
            // TODO: Implement proper world-to-local conversion
            onTransformChange(selectedObject, newTransform);
        }
    };

    if (!selectedObject) return null;

    return (
        <div
            style={{
                borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                padding: "12px 16px",
                backgroundColor: "rgba(33, 150, 243, 0.1)",
            }}
        >
            <h5
                style={{
                    margin: "0 0 12px 0",
                    fontSize: "0.9em",
                    color: "#2196F3",
                    textAlign: "center",
                }}
            >
                🎛️ Transform Controls
            </h5>

            {/* Coordinate Space Toggle */}
            <div style={{ marginBottom: "12px", display: "flex", gap: "4px" }}>
                <button
                    onClick={() => setCoordinateSpace("local")}
                    disabled={animationPlaying}
                    style={{
                        flex: 1,
                        background: coordinateSpace === "local" ? "rgba(33, 150, 243, 0.5)" : "rgba(255, 255, 255, 0.1)",
                        border: coordinateSpace === "local" ? "1px solid #2196F3" : "1px solid rgba(255, 255, 255, 0.2)",
                        color: "white",
                        padding: "6px 8px",
                        borderRadius: "4px",
                        cursor: animationPlaying ? "not-allowed" : "pointer",
                        fontSize: "0.7em",
                        fontWeight: coordinateSpace === "local" ? "bold" : "normal",
                    }}
                >
                    📍 Local
                </button>
                <button
                    onClick={() => setCoordinateSpace("world")}
                    disabled={animationPlaying}
                    style={{
                        flex: 1,
                        background: coordinateSpace === "world" ? "rgba(33, 150, 243, 0.5)" : "rgba(255, 255, 255, 0.1)",
                        border: coordinateSpace === "world" ? "1px solid #2196F3" : "1px solid rgba(255, 255, 255, 0.2)",
                        color: "white",
                        padding: "6px 8px",
                        borderRadius: "4px",
                        cursor: animationPlaying ? "not-allowed" : "pointer",
                        fontSize: "0.7em",
                        fontWeight: coordinateSpace === "world" ? "bold" : "normal",
                    }}
                >
                    🌍 World
                </button>
            </div>

            {/* Warning when animation is playing */}
            {animationPlaying && (
                <div style={{
                    marginBottom: "12px",
                    padding: "8px",
                    backgroundColor: "rgba(255, 152, 0, 0.2)",
                    borderRadius: "4px",
                    fontSize: "0.7em",
                    color: "#FF9800",
                    textAlign: "center",
                    border: "1px solid rgba(255, 152, 0, 0.3)"
                }}>
                    ⚠️ Transform controls disabled while animation is playing. Pause animation to enable.
                </div>
            )}

            {/* Position */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#ccc",
                    }}
                >
                    Position ({coordinateSpace === "local" ? "Local" : "World"})
                </div>
                {(["x", "y", "z"] as const).map((axis) => (
                    <div key={axis} style={{ marginBottom: "4px" }}>
                        <label
                            style={{
                                display: "flex",
                                alignItems: "center",
                                fontSize: "0.75em",
                            }}
                        >
                            <span style={{ width: "20px", color: "#2196F3" }}>
                                {axis.toUpperCase()}:
                            </span>
                            <input
                                type="number"
                                value={transform.position[axis].toFixed(2)}
                                onChange={(e) =>
                                    updateTransform("position", axis, parseFloat(e.target.value))
                                }
                                step="0.1"
                                disabled={animationPlaying}
                                style={{
                                    flex: 1,
                                    marginLeft: "8px",
                                    padding: "4px",
                                    borderRadius: "4px",
                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                    backgroundColor: "rgba(255, 255, 255, 0.1)",
                                    color: "white",
                                    fontSize: "0.85em",
                                }}
                            />
                        </label>
                    </div>
                ))}
            </div>

            {/* Rotation */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#ccc",
                    }}
                >
                    Rotation ({coordinateSpace === "local" ? "Local" : "World"}, degrees)
                </div>
                {(["x", "y", "z"] as const).map((axis) => (
                    <div key={axis} style={{ marginBottom: "4px" }}>
                        <label
                            style={{
                                display: "flex",
                                alignItems: "center",
                                fontSize: "0.75em",
                            }}
                        >
                            <span style={{ width: "20px", color: "#FF9800" }}>
                                {axis.toUpperCase()}:
                            </span>
                            <input
                                type="number"
                                value={transform.rotation[axis].toFixed(1)}
                                onChange={(e) =>
                                    updateTransform("rotation", axis, parseFloat(e.target.value))
                                }
                                step="1"
                                disabled={animationPlaying}
                                style={{
                                    flex: 1,
                                    marginLeft: "8px",
                                    padding: "4px",
                                    borderRadius: "4px",
                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                    backgroundColor: "rgba(255, 255, 255, 0.1)",
                                    color: "white",
                                    fontSize: "0.85em",
                                }}
                            />
                        </label>
                    </div>
                ))}
            </div>

            {/* Scale */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#ccc",
                    }}
                >
                    Scale ({coordinateSpace === "local" ? "Local" : "World"})
                </div>
                {(["x", "y", "z"] as const).map((axis) => (
                    <div key={axis} style={{ marginBottom: "4px" }}>
                        <label
                            style={{
                                display: "flex",
                                alignItems: "center",
                                fontSize: "0.75em",
                            }}
                        >
                            <span style={{ width: "20px", color: "#4CAF50" }}>
                                {axis.toUpperCase()}:
                            </span>
                            <input
                                type="number"
                                value={transform.scale[axis].toFixed(2)}
                                onChange={(e) =>
                                    updateTransform("scale", axis, parseFloat(e.target.value))
                                }
                                step="0.1"
                                min="0.01"
                                disabled={animationPlaying}
                                style={{
                                    flex: 1,
                                    marginLeft: "8px",
                                    padding: "4px",
                                    borderRadius: "4px",
                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                    backgroundColor: "rgba(255, 255, 255, 0.1)",
                                    color: "white",
                                    fontSize: "0.85em",
                                }}
                            />
                        </label>
                    </div>
                ))}
            </div>

            {/* Action Buttons */}
            <div style={{ display: "flex", gap: "6px" }}>
                <button
                    onClick={onUndo}
                    disabled={!canUndo || animationPlaying}
                    style={{
                        flex: 1,
                        background: (canUndo && !animationPlaying) ? "#FF9800" : "rgba(255, 255, 255, 0.1)",
                        border: "none",
                        color: (canUndo && !animationPlaying) ? "white" : "#666",
                        padding: "6px 8px",
                        borderRadius: "4px",
                        cursor: (canUndo && !animationPlaying) ? "pointer" : "not-allowed",
                        fontSize: "0.75em",
                    }}
                    title={animationPlaying ? "Disabled while animation playing" : "Undo last change"}
                >
                    ↶ Undo
                </button>
                <button
                    onClick={() => {
                        onReset();
                        setUpdateTrigger(prev => prev + 1);
                    }}
                    disabled={animationPlaying}
                    style={{
                        flex: 1,
                        background: animationPlaying ? "rgba(255, 255, 255, 0.1)" : "#F44336",
                        border: "none",
                        color: animationPlaying ? "#666" : "white",
                        padding: "6px 8px",
                        borderRadius: "4px",
                        cursor: animationPlaying ? "not-allowed" : "pointer",
                        fontSize: "0.75em",
                    }}
                    title={animationPlaying ? "Disabled while animation playing" : "Reset to original transform"}
                >
                    ⟲ Reset
                </button>
            </div>
        </div>
    );
};

export default NeedleUSDTransformControls;