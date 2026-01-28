/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { MaterialLibraryItem } from "./NeedleUSDMaterialLibrary";

interface MaterialState {
    color: string;
    emissive: string;
    metalness: number;
    roughness: number;
    opacity: number;
    transparent: boolean;
    wireframe: boolean;
}

interface NeedleUSDMaterialEditorProps {
    materialLibraryItem: MaterialLibraryItem | null;
    onMaterialChange: (materialId: string, materialState: MaterialState) => void;
    onResetMaterial: (materialId: string) => void;
    onDuplicateMaterial: (materialId: string) => void;
    onDeleteMaterial: (materialId: string) => void;
    scene: any;
}

const NeedleUSDMaterialEditor: React.FC<NeedleUSDMaterialEditorProps> = ({
    materialLibraryItem,
    onMaterialChange,
    onResetMaterial,
    onDuplicateMaterial,
    onDeleteMaterial,
    scene,
}) => {
    const [material, setMaterial] = useState<MaterialState>({
        color: "#ffffff",
        emissive: "#000000",
        metalness: 0,
        roughness: 1,
        opacity: 1,
        transparent: false,
        wireframe: false,
    });
    const [updateTrigger, setUpdateTrigger] = useState(0);
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

    const THREE = (window as any).THREE;

    // Update material state when selected material changes
    useEffect(() => {
        if (materialLibraryItem?.material && THREE) {
            const mat = materialLibraryItem.material;

            setMaterial({
                color: mat.color ? `#${mat.color.getHexString()}` : "#ffffff",
                emissive: mat.emissive ? `#${mat.emissive.getHexString()}` : "#000000",
                metalness: mat.metalness !== undefined ? mat.metalness : 0,
                roughness: mat.roughness !== undefined ? mat.roughness : 1,
                opacity: mat.opacity !== undefined ? mat.opacity : 1,
                transparent: mat.transparent || false,
                wireframe: mat.wireframe || false,
            });
        }
    }, [materialLibraryItem, THREE, updateTrigger]);

    const updateMaterial = (property: keyof MaterialState, value: any) => {
        if (!materialLibraryItem) return;

        const newMaterial = {
            ...material,
            [property]: value,
        };
        setMaterial(newMaterial);
        onMaterialChange(materialLibraryItem.id, newMaterial);
    };

    const handleDelete = () => {
        if (!materialLibraryItem) return;

        if (materialLibraryItem.usedBy.size > 0) {
            alert(
                `Cannot delete material "${materialLibraryItem.name}" because it is used by ${materialLibraryItem.usedBy.size} object(s). Please assign a different material to those objects first.`
            );
            setShowDeleteConfirm(false);
            return;
        }

        onDeleteMaterial(materialLibraryItem.id);
        setShowDeleteConfirm(false);
    };

    if (!materialLibraryItem) {
        return (
            <div
                style={{
                    display: "flex",
                    flexDirection: "column",
                    height: "100%",
                    backgroundColor: "rgba(0, 0, 0, 0.2)",
                    justifyContent: "center",
                    alignItems: "center",
                    padding: "20px",
                }}
            >
                <div style={{ fontSize: "3em", marginBottom: "16px", opacity: 0.3 }}>🎨</div>
                <div style={{ color: "#999", fontSize: "0.9em", textAlign: "center" }}>
                    No material selected
                </div>
                <div
                    style={{
                        color: "#666",
                        fontSize: "0.75em",
                        marginTop: "8px",
                        textAlign: "center",
                    }}
                >
                    Select a material from the Material Library to edit its properties
                </div>
            </div>
        );
    }

    const mat = materialLibraryItem.material;

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                backgroundColor: "rgba(0, 0, 0, 0.2)",
            }}
        >
            {/* Header */}
            <div
                style={{
                    padding: "12px 16px",
                    borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                    backgroundColor: "rgba(233, 30, 99, 0.2)",
                }}
            >
                <h4
                    style={{
                        margin: "0 0 8px 0",
                        fontSize: "1em",
                        color: "#E91E63",
                        textAlign: "center",
                    }}
                >
                    ✏️ Material Editor
                </h4>
                <div
                    style={{
                        fontSize: "0.85em",
                        fontWeight: "bold",
                        color: "white",
                        textAlign: "center",
                        marginBottom: "4px",
                    }}
                >
                    {materialLibraryItem.name}
                </div>
                <div style={{ fontSize: "0.7em", color: "#ccc", textAlign: "center" }}>
                    Used by {materialLibraryItem.usedBy.size} object
                    {materialLibraryItem.usedBy.size !== 1 ? "s" : ""}
                </div>
            </div>

            {/* Warning Banner */}
            {materialLibraryItem.usedBy.size > 1 && (
                <div
                    style={{
                        padding: "8px 12px",
                        backgroundColor: "rgba(255, 152, 0, 0.2)",
                        borderBottom: "1px solid rgba(255, 152, 0, 0.3)",
                        fontSize: "0.75em",
                        color: "#FFA726",
                        textAlign: "center",
                    }}
                >
                    ⚠️ Changes will affect all {materialLibraryItem.usedBy.size} objects using this
                    material
                </div>
            )}

            {/* Material Properties */}
            <div
                style={{
                    flex: 1,
                    overflowY: "auto",
                    padding: "16px",
                    scrollbarWidth: "thin",
                    scrollbarColor: "rgba(255, 255, 255, 0.5) transparent",
                }}
            >
                {/* Base Color */}
                <div style={{ marginBottom: "16px" }}>
                    <div
                        style={{
                            fontSize: "0.8em",
                            fontWeight: "bold",
                            marginBottom: "8px",
                            color: "#ccc",
                        }}
                    >
                        Base Color
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <input
                            type="color"
                            value={material.color}
                            onChange={(e) => updateMaterial("color", e.target.value)}
                            style={{
                                width: "50px",
                                height: "40px",
                                border: "2px solid rgba(255, 255, 255, 0.2)",
                                borderRadius: "6px",
                                cursor: "pointer",
                            }}
                        />
                        <input
                            type="text"
                            value={material.color}
                            onChange={(e) => updateMaterial("color", e.target.value)}
                            style={{
                                flex: 1,
                                padding: "8px 10px",
                                borderRadius: "4px",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                backgroundColor: "rgba(255, 255, 255, 0.1)",
                                color: "white",
                                fontSize: "0.8em",
                            }}
                        />
                    </div>
                </div>

                {/* Emissive Color */}
                <div style={{ marginBottom: "16px" }}>
                    <div
                        style={{
                            fontSize: "0.8em",
                            fontWeight: "bold",
                            marginBottom: "8px",
                            color: "#ccc",
                        }}
                    >
                        Emissive Color
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <input
                            type="color"
                            value={material.emissive}
                            onChange={(e) => updateMaterial("emissive", e.target.value)}
                            style={{
                                width: "50px",
                                height: "40px",
                                border: "2px solid rgba(255, 255, 255, 0.2)",
                                borderRadius: "6px",
                                cursor: "pointer",
                            }}
                        />
                        <input
                            type="text"
                            value={material.emissive}
                            onChange={(e) => updateMaterial("emissive", e.target.value)}
                            style={{
                                flex: 1,
                                padding: "8px 10px",
                                borderRadius: "4px",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                backgroundColor: "rgba(255, 255, 255, 0.1)",
                                color: "white",
                                fontSize: "0.8em",
                            }}
                        />
                    </div>
                </div>

                {/* Metalness */}
                {mat.metalness !== undefined && (
                    <div style={{ marginBottom: "16px" }}>
                        <label
                            style={{
                                display: "block",
                                marginBottom: "8px",
                                fontSize: "0.8em",
                                fontWeight: "bold",
                                color: "#ccc",
                            }}
                        >
                            Metalness: {material.metalness.toFixed(2)}
                        </label>
                        <input
                            type="range"
                            min="0"
                            max="1"
                            step="0.01"
                            value={material.metalness}
                            onChange={(e) =>
                                updateMaterial("metalness", parseFloat(e.target.value))
                            }
                            style={{ width: "100%", cursor: "pointer" }}
                        />
                    </div>
                )}

                {/* Roughness */}
                {mat.roughness !== undefined && (
                    <div style={{ marginBottom: "16px" }}>
                        <label
                            style={{
                                display: "block",
                                marginBottom: "8px",
                                fontSize: "0.8em",
                                fontWeight: "bold",
                                color: "#ccc",
                            }}
                        >
                            Roughness: {material.roughness.toFixed(2)}
                        </label>
                        <input
                            type="range"
                            min="0"
                            max="1"
                            step="0.01"
                            value={material.roughness}
                            onChange={(e) =>
                                updateMaterial("roughness", parseFloat(e.target.value))
                            }
                            style={{ width: "100%", cursor: "pointer" }}
                        />
                    </div>
                )}

                {/* Opacity */}
                <div style={{ marginBottom: "16px" }}>
                    <label
                        style={{
                            display: "block",
                            marginBottom: "8px",
                            fontSize: "0.8em",
                            fontWeight: "bold",
                            color: "#ccc",
                        }}
                    >
                        Opacity: {material.opacity.toFixed(2)}
                    </label>
                    <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={material.opacity}
                        onChange={(e) => updateMaterial("opacity", parseFloat(e.target.value))}
                        style={{ width: "100%", cursor: "pointer" }}
                    />
                </div>

                {/* Boolean Options */}
                <div style={{ marginBottom: "16px" }}>
                    <div
                        style={{
                            fontSize: "0.8em",
                            fontWeight: "bold",
                            marginBottom: "8px",
                            color: "#ccc",
                        }}
                    >
                        Options
                    </div>
                    <label
                        style={{
                            display: "flex",
                            alignItems: "center",
                            cursor: "pointer",
                            marginBottom: "8px",
                            fontSize: "0.85em",
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={material.transparent}
                            onChange={(e) => updateMaterial("transparent", e.target.checked)}
                            style={{ marginRight: "8px" }}
                        />
                        <span>Transparent</span>
                    </label>
                    <label
                        style={{
                            display: "flex",
                            alignItems: "center",
                            cursor: "pointer",
                            fontSize: "0.85em",
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={material.wireframe}
                            onChange={(e) => updateMaterial("wireframe", e.target.checked)}
                            style={{ marginRight: "8px" }}
                        />
                        <span>Wireframe</span>
                    </label>
                </div>

                {/* Material Info */}
                <div
                    style={{
                        marginBottom: "16px",
                        padding: "12px",
                        backgroundColor: "rgba(0, 0, 0, 0.3)",
                        borderRadius: "6px",
                        fontSize: "0.75em",
                    }}
                >
                    <div style={{ marginBottom: "4px" }}>
                        <strong>Type:</strong> {mat.type}
                    </div>
                    <div style={{ marginBottom: "4px" }}>
                        <strong>ID:</strong> {materialLibraryItem.id.substring(0, 8)}...
                    </div>
                    {materialLibraryItem.isCustom && (
                        <div style={{ marginTop: "8px", color: "#9C27B0" }}>
                            ✨ User-created material
                        </div>
                    )}
                </div>
            </div>

            {/* Action Buttons */}
            <div
                style={{
                    padding: "12px 16px",
                    borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "8px",
                }}
            >
                {/* Reset Material */}
                <button
                    onClick={() => {
                        onResetMaterial(materialLibraryItem.id);
                        setUpdateTrigger((prev) => prev + 1);
                    }}
                    style={{
                        width: "100%",
                        background: "#FF9800",
                        border: "none",
                        color: "white",
                        padding: "10px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.85em",
                        fontWeight: "bold",
                    }}
                    title="Reset material to original properties"
                >
                    ⟲ Reset Material
                </button>

                {/* Duplicate Material */}
                <button
                    onClick={() => onDuplicateMaterial(materialLibraryItem.id)}
                    style={{
                        width: "100%",
                        background: "#2196F3",
                        border: "none",
                        color: "white",
                        padding: "10px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.85em",
                        fontWeight: "bold",
                    }}
                    title="Create a copy of this material"
                >
                    📋 Duplicate Material
                </button>

                {/* Delete Material */}
                {!showDeleteConfirm ? (
                    <button
                        onClick={() => setShowDeleteConfirm(true)}
                        style={{
                            width: "100%",
                            background: "#F44336",
                            border: "none",
                            color: "white",
                            padding: "10px 12px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.85em",
                            fontWeight: "bold",
                        }}
                        title="Delete this material"
                    >
                        🗑️ Delete Material
                    </button>
                ) : (
                    <div
                        style={{
                            display: "flex",
                            gap: "8px",
                            padding: "8px",
                            backgroundColor: "rgba(244, 67, 54, 0.2)",
                            borderRadius: "4px",
                            border: "1px solid rgba(244, 67, 54, 0.5)",
                        }}
                    >
                        <button
                            onClick={handleDelete}
                            style={{
                                flex: 1,
                                background: "#F44336",
                                border: "none",
                                color: "white",
                                padding: "8px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                                fontWeight: "bold",
                            }}
                        >
                            ✓ Confirm
                        </button>
                        <button
                            onClick={() => setShowDeleteConfirm(false)}
                            style={{
                                flex: 1,
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "8px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                            }}
                        >
                            ✕ Cancel
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default NeedleUSDMaterialEditor;
