/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback, useRef } from "react";

interface TransformState {
    position: { x: number; y: number; z: number };
    rotation: { x: number; y: number; z: number }; // In degrees for UI
    scale: { x: number; y: number; z: number };
}

interface VeerumTransformControlsProps {
    selectedObject: any | null;
    selectedCount: number;
    onTransformChange: (object: any, transform: TransformState) => void;
    onUndo: () => void;
    onReset: () => void;
    canUndo: boolean;
}

const VeerumTransformControls: React.FC<VeerumTransformControlsProps> = ({
    selectedObject,
    selectedCount,
    onTransformChange,
    onUndo,
    onReset,
    canUndo,
}) => {
    const [position, setPosition] = useState({ x: 0, y: 0, z: 0 });
    const [rotation, setRotation] = useState({ x: 0, y: 0, z: 0 }); // Degrees
    const [scale, setScale] = useState({ x: 1, y: 1, z: 1 });
    const [uniformScale, setUniformScale] = useState(false);
    const [isWorldCoordinates, setIsWorldCoordinates] = useState(true);
    const resetTriggerRef = useRef(0);

    // Update local state when selected object changes or reset is triggered
    const updateFieldsFromObject = useCallback(() => {
        if (!selectedObject) {
            setPosition({ x: 0, y: 0, z: 0 });
            setRotation({ x: 0, y: 0, z: 0 });
            setScale({ x: 1, y: 1, z: 1 });
            setIsWorldCoordinates(true);
            return;
        }

        // Determine if using world or local coordinates
        const hasParent = selectedObject.parent && selectedObject.parent.type !== "Scene";
        setIsWorldCoordinates(!hasParent);

        // Get position (world or local)
        if (hasParent) {
            setPosition({
                x: parseFloat(selectedObject.position.x.toFixed(2)),
                y: parseFloat(selectedObject.position.y.toFixed(2)),
                z: parseFloat(selectedObject.position.z.toFixed(2)),
            });
        } else {
            const worldPos = selectedObject.getWorldPosition(new (window as any).THREE.Vector3());
            setPosition({
                x: parseFloat(worldPos.x.toFixed(2)),
                y: parseFloat(worldPos.y.toFixed(2)),
                z: parseFloat(worldPos.z.toFixed(2)),
            });
        }

        // Get rotation in degrees
        setRotation({
            x: parseFloat(((selectedObject.rotation.x * 180) / Math.PI).toFixed(1)),
            y: parseFloat(((selectedObject.rotation.y * 180) / Math.PI).toFixed(1)),
            z: parseFloat(((selectedObject.rotation.z * 180) / Math.PI).toFixed(1)),
        });

        // Get scale
        setScale({
            x: parseFloat(selectedObject.scale.x.toFixed(2)),
            y: parseFloat(selectedObject.scale.y.toFixed(2)),
            z: parseFloat(selectedObject.scale.z.toFixed(2)),
        });
    }, [selectedObject]);

    useEffect(() => {
        updateFieldsFromObject();
    }, [updateFieldsFromObject, resetTriggerRef.current]);

    // Apply transform changes (live preview)
    const applyTransform = useCallback(() => {
        if (!selectedObject) return;

        const transform: TransformState = {
            position,
            rotation,
            scale,
        };

        onTransformChange(selectedObject, transform);
    }, [selectedObject, position, rotation, scale, onTransformChange]);

    // Update position
    const updatePosition = useCallback((axis: "x" | "y" | "z", value: number) => {
        setPosition((prev) => {
            const newPos = { ...prev, [axis]: value };
            return newPos;
        });
    }, []);

    // Update rotation
    const updateRotation = useCallback((axis: "x" | "y" | "z", value: number) => {
        setRotation((prev) => {
            const newRot = { ...prev, [axis]: value };
            return newRot;
        });
    }, []);

    // Update scale
    const updateScale = useCallback(
        (axis: "x" | "y" | "z", value: number) => {
            if (uniformScale) {
                // Apply same scale to all axes
                setScale({ x: value, y: value, z: value });
            } else {
                setScale((prev) => ({ ...prev, [axis]: value }));
            }
        },
        [uniformScale]
    );

    // Apply transform whenever values change
    useEffect(() => {
        if (selectedObject) {
            applyTransform();
        }
    }, [position, rotation, scale, selectedObject, applyTransform]);

    // Increment/decrement helpers
    const adjustValue = (
        current: number,
        delta: number,
        setter: (axis: any, value: number) => void,
        axis: string
    ) => {
        const newValue = parseFloat((current + delta).toFixed(2));
        setter(axis as any, newValue);
    };

    // Handle reset with field update
    const handleReset = useCallback(() => {
        onReset();
        // Trigger field update after a short delay to allow the object to be reset
        setTimeout(() => {
            resetTriggerRef.current += 1;
            updateFieldsFromObject();
        }, 50);
    }, [onReset, updateFieldsFromObject]);

    if (!selectedObject) {
        return null;
    }

    const coordLabel = isWorldCoordinates ? "World" : "Local";
    const selectionText =
        selectedCount > 1
            ? `${selectedObject.name || "Object"} + ${selectedCount - 1} children`
            : selectedObject.name || "Object";

    return (
        <div
            style={{
                borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                padding: "12px 16px",
                backgroundColor: "rgba(0, 0, 0, 0.2)",
            }}
        >
            {/* Selection Info */}
            <div
                style={{
                    marginBottom: "12px",
                    fontSize: "0.85em",
                    color: "#4CAF50",
                    fontWeight: "bold",
                }}
            >
                📐 Transform Controls
            </div>
            <div
                style={{
                    marginBottom: "12px",
                    fontSize: "0.8em",
                    color: "#ccc",
                }}
            >
                Selected: {selectionText}
            </div>

            {/* Position Controls */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#fff",
                    }}
                >
                    Position ({coordLabel})
                </div>
                {(["x", "y", "z"] as const).map((axis) => (
                    <div
                        key={axis}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            marginBottom: "4px",
                            gap: "4px",
                        }}
                    >
                        <span
                            style={{
                                width: "12px",
                                fontSize: "0.75em",
                                color: "#999",
                                textTransform: "uppercase",
                            }}
                        >
                            {axis}:
                        </span>
                        <input
                            type="number"
                            value={position[axis]}
                            onChange={(e) => updatePosition(axis, parseFloat(e.target.value) || 0)}
                            step="0.1"
                            style={{
                                flex: 1,
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "4px 6px",
                                borderRadius: "3px",
                                fontSize: "0.75em",
                            }}
                        />
                        <button
                            onClick={() => adjustValue(position[axis], 0.1, updatePosition, axis)}
                            tabIndex={-1}
                            style={{
                                background: "rgba(76, 175, 80, 0.3)",
                                border: "1px solid rgba(76, 175, 80, 0.5)",
                                color: "white",
                                padding: "2px 6px",
                                borderRadius: "3px",
                                cursor: "pointer",
                                fontSize: "0.7em",
                            }}
                        >
                            +
                        </button>
                        <button
                            onClick={() => adjustValue(position[axis], -0.1, updatePosition, axis)}
                            tabIndex={-1}
                            style={{
                                background: "rgba(244, 67, 54, 0.3)",
                                border: "1px solid rgba(244, 67, 54, 0.5)",
                                color: "white",
                                padding: "2px 6px",
                                borderRadius: "3px",
                                cursor: "pointer",
                                fontSize: "0.7em",
                            }}
                        >
                            -
                        </button>
                    </div>
                ))}
            </div>

            {/* Rotation Controls */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#fff",
                    }}
                >
                    Rotation ({coordLabel})
                </div>
                {(["x", "y", "z"] as const).map((axis) => (
                    <div
                        key={axis}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            marginBottom: "4px",
                            gap: "4px",
                        }}
                    >
                        <span
                            style={{
                                width: "12px",
                                fontSize: "0.75em",
                                color: "#999",
                                textTransform: "uppercase",
                            }}
                        >
                            {axis}:
                        </span>
                        <input
                            type="number"
                            value={rotation[axis]}
                            onChange={(e) => updateRotation(axis, parseFloat(e.target.value) || 0)}
                            step="5"
                            style={{
                                flex: 1,
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "4px 6px",
                                borderRadius: "3px",
                                fontSize: "0.75em",
                            }}
                        />
                        <button
                            onClick={() => adjustValue(rotation[axis], 5, updateRotation, axis)}
                            tabIndex={-1}
                            style={{
                                background: "rgba(76, 175, 80, 0.3)",
                                border: "1px solid rgba(76, 175, 80, 0.5)",
                                color: "white",
                                padding: "2px 6px",
                                borderRadius: "3px",
                                cursor: "pointer",
                                fontSize: "0.7em",
                            }}
                        >
                            +
                        </button>
                        <button
                            onClick={() => adjustValue(rotation[axis], -5, updateRotation, axis)}
                            tabIndex={-1}
                            style={{
                                background: "rgba(244, 67, 54, 0.3)",
                                border: "1px solid rgba(244, 67, 54, 0.5)",
                                color: "white",
                                padding: "2px 6px",
                                borderRadius: "3px",
                                cursor: "pointer",
                                fontSize: "0.7em",
                            }}
                        >
                            -
                        </button>
                        <span style={{ fontSize: "0.7em", color: "#999" }}>°</span>
                    </div>
                ))}
            </div>

            {/* Scale Controls */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#fff",
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                    }}
                >
                    <span>Scale ({coordLabel})</span>
                    <label
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "4px",
                            fontSize: "0.75em",
                            fontWeight: "normal",
                            cursor: "pointer",
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={uniformScale}
                            onChange={(e) => setUniformScale(e.target.checked)}
                            tabIndex={-1}
                            style={{ cursor: "pointer" }}
                        />
                        🔗 Uniform
                    </label>
                </div>
                {(["x", "y", "z"] as const).map((axis) => (
                    <div
                        key={axis}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            marginBottom: "4px",
                            gap: "4px",
                        }}
                    >
                        <span
                            style={{
                                width: "12px",
                                fontSize: "0.75em",
                                color: "#999",
                                textTransform: "uppercase",
                            }}
                        >
                            {axis}:
                        </span>
                        <input
                            type="number"
                            value={scale[axis]}
                            onChange={(e) => updateScale(axis, parseFloat(e.target.value) || 0)}
                            step="0.1"
                            min="0.01"
                            style={{
                                flex: 1,
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "4px 6px",
                                borderRadius: "3px",
                                fontSize: "0.75em",
                            }}
                        />
                        <button
                            onClick={() => adjustValue(scale[axis], 0.1, updateScale, axis)}
                            tabIndex={-1}
                            style={{
                                background: "rgba(76, 175, 80, 0.3)",
                                border: "1px solid rgba(76, 175, 80, 0.5)",
                                color: "white",
                                padding: "2px 6px",
                                borderRadius: "3px",
                                cursor: "pointer",
                                fontSize: "0.7em",
                            }}
                        >
                            +
                        </button>
                        <button
                            onClick={() => adjustValue(scale[axis], -0.1, updateScale, axis)}
                            tabIndex={-1}
                            style={{
                                background: "rgba(244, 67, 54, 0.3)",
                                border: "1px solid rgba(244, 67, 54, 0.5)",
                                color: "white",
                                padding: "2px 6px",
                                borderRadius: "3px",
                                cursor: "pointer",
                                fontSize: "0.7em",
                            }}
                        >
                            -
                        </button>
                    </div>
                ))}
            </div>

            {/* Action Buttons */}
            <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
                <button
                    onClick={onUndo}
                    disabled={!canUndo}
                    tabIndex={-1}
                    style={{
                        flex: 1,
                        background: canUndo
                            ? "rgba(33, 150, 243, 0.3)"
                            : "rgba(100, 100, 100, 0.2)",
                        border: canUndo
                            ? "1px solid rgba(33, 150, 243, 0.5)"
                            : "1px solid rgba(100, 100, 100, 0.3)",
                        color: canUndo ? "white" : "#666",
                        padding: "6px 8px",
                        borderRadius: "4px",
                        cursor: canUndo ? "pointer" : "not-allowed",
                        fontSize: "0.75em",
                    }}
                    title="Undo last transform"
                >
                    ↶ Undo
                </button>
                <button
                    onClick={handleReset}
                    tabIndex={-1}
                    style={{
                        flex: 1,
                        background: "rgba(255, 152, 0, 0.3)",
                        border: "1px solid rgba(255, 152, 0, 0.5)",
                        color: "white",
                        padding: "6px 8px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.75em",
                    }}
                    title="Reset to original transform"
                >
                    ⟲ Reset
                </button>
            </div>

            {/* Help Text */}
            <div
                style={{
                    marginTop: "8px",
                    fontSize: "0.65em",
                    color: "#999",
                    fontStyle: "italic",
                }}
            >
                Changes apply immediately. Use Undo to revert.
            </div>
        </div>
    );
};

export default VeerumTransformControls;
