/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { MaterialLibraryItem } from "./NeedleUSDMaterialLibrary";

interface NeedleUSDObjectMaterialAssignmentProps {
    selectedObject: any;
    materialLibrary: Map<string, MaterialLibraryItem>;
    onAssignMaterial: (objectUuid: string, materialId: string) => void;
    onMakeUnique: (objectUuid: string) => void;
    onCreateAndAssign: (objectUuid: string) => void;
    onEditMaterial: (materialId: string) => void;
}

const NeedleUSDObjectMaterialAssignment: React.FC<NeedleUSDObjectMaterialAssignmentProps> = ({
    selectedObject,
    materialLibrary,
    onAssignMaterial,
    onMakeUnique,
    onCreateAndAssign,
    onEditMaterial,
}) => {
    const [currentMaterialId, setCurrentMaterialId] = useState<string | null>(null);
    const [showDropdown, setShowDropdown] = useState(false);

    const THREE = (window as any).THREE;

    // Find which material is currently assigned to this object
    useEffect(() => {
        if (!selectedObject || !selectedObject.material) {
            setCurrentMaterialId(null);
            return;
        }

        // Search through material library to find which material this object uses
        // Note: Selected objects have highlighted materials (clones), so we check usedBy instead
        let foundMaterialId: string | null = null;
        Array.from(materialLibrary.entries()).forEach(([id, item]) => {
            if (item.usedBy.has(selectedObject.uuid)) {
                foundMaterialId = id;
            }
        });

        setCurrentMaterialId(foundMaterialId);
    }, [selectedObject, materialLibrary]);

    if (!selectedObject || !selectedObject.material) {
        return (
            <div
                style={{
                    padding: "12px 16px",
                    borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                    backgroundColor: "rgba(0, 0, 0, 0.2)",
                }}
            >
                <div style={{ fontSize: "0.8em", color: "#999", textAlign: "center" }}>
                    Selected object has no material
                </div>
            </div>
        );
    }

    const currentMaterial = currentMaterialId ? materialLibrary.get(currentMaterialId) : null;
    const isSharedMaterial = currentMaterial && currentMaterial.usedBy.size > 1;

    const getColorHex = (material: any): string => {
        if (!material || !THREE) return "#808080";
        try {
            if (material.color) {
                return `#${material.color.getHexString()}`;
            }
        } catch (error) {
            return "#808080";
        }
        return "#808080";
    };

    // Sort materials for dropdown
    const sortedMaterials = Array.from(materialLibrary.values()).sort((a, b) => {
        if (a.isCustom !== b.isCustom) {
            return a.isCustom ? -1 : 1;
        }
        return a.name.localeCompare(b.name);
    });

    return (
        <div
            style={{
                borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                padding: "12px 16px",
                backgroundColor: "rgba(76, 175, 80, 0.1)",
            }}
        >
            <h5
                style={{
                    margin: "0 0 12px 0",
                    fontSize: "0.9em",
                    color: "#4CAF50",
                    textAlign: "center",
                }}
            >
                🎨 Material Assignment
            </h5>

            {/* Current Material Display */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.75em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#ccc",
                    }}
                >
                    Current Material
                </div>
                {currentMaterial ? (
                    <div
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                            padding: "8px",
                            backgroundColor: "rgba(0, 0, 0, 0.3)",
                            borderRadius: "4px",
                            border: "1px solid rgba(255, 255, 255, 0.1)",
                        }}
                    >
                        {/* Color Swatch */}
                        <div
                            style={{
                                width: "30px",
                                height: "30px",
                                backgroundColor: getColorHex(currentMaterial.material),
                                border: "2px solid rgba(255, 255, 255, 0.3)",
                                borderRadius: "4px",
                                flexShrink: 0,
                            }}
                        />
                        {/* Material Info */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div
                                style={{
                                    fontSize: "0.8em",
                                    fontWeight: "bold",
                                    color: "white",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                }}
                            >
                                {currentMaterial.name}
                            </div>
                            <div style={{ fontSize: "0.7em", color: "#999" }}>
                                {isSharedMaterial
                                    ? `Shared by ${currentMaterial.usedBy.size} objects`
                                    : "Unique to this object"}
                            </div>
                        </div>
                    </div>
                ) : (
                    <div
                        style={{
                            padding: "8px",
                            backgroundColor: "rgba(255, 152, 0, 0.2)",
                            borderRadius: "4px",
                            fontSize: "0.75em",
                            color: "#FFA726",
                            textAlign: "center",
                        }}
                    >
                        ⚠️ Material not in library
                    </div>
                )}
            </div>

            {/* Material Selection Dropdown */}
            <div style={{ marginBottom: "12px", position: "relative" }}>
                <div
                    style={{
                        fontSize: "0.75em",
                        fontWeight: "bold",
                        marginBottom: "6px",
                        color: "#ccc",
                    }}
                >
                    Assign Material
                </div>
                <button
                    onClick={() => setShowDropdown(!showDropdown)}
                    style={{
                        width: "100%",
                        padding: "8px 10px",
                        borderRadius: "4px",
                        border: "1px solid rgba(255, 255, 255, 0.2)",
                        backgroundColor: "rgba(255, 255, 255, 0.1)",
                        color: "white",
                        fontSize: "0.8em",
                        cursor: "pointer",
                        textAlign: "left",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                    }}
                >
                    <span>Select from library...</span>
                    <span>{showDropdown ? "▲" : "▼"}</span>
                </button>

                {/* Dropdown Menu */}
                {showDropdown && (
                    <div
                        style={{
                            position: "absolute",
                            top: "100%",
                            left: 0,
                            right: 0,
                            marginTop: "4px",
                            maxHeight: "200px",
                            overflowY: "auto",
                            backgroundColor: "rgba(30, 30, 30, 0.98)",
                            border: "1px solid rgba(255, 255, 255, 0.2)",
                            borderRadius: "4px",
                            zIndex: 1000,
                            boxShadow: "0 4px 8px rgba(0, 0, 0, 0.5)",
                        }}
                    >
                        {sortedMaterials.map((item) => {
                            const isCurrentMaterial = item.id === currentMaterialId;
                            return (
                                <div
                                    key={item.id}
                                    onClick={() => {
                                        if (!isCurrentMaterial) {
                                            onAssignMaterial(selectedObject.uuid, item.id);
                                        }
                                        setShowDropdown(false);
                                    }}
                                    style={{
                                        padding: "8px",
                                        cursor: isCurrentMaterial ? "default" : "pointer",
                                        backgroundColor: isCurrentMaterial
                                            ? "rgba(76, 175, 80, 0.3)"
                                            : "transparent",
                                        borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
                                        display: "flex",
                                        alignItems: "center",
                                        gap: "8px",
                                    }}
                                    onMouseEnter={(e) => {
                                        if (!isCurrentMaterial) {
                                            e.currentTarget.style.backgroundColor =
                                                "rgba(255, 255, 255, 0.1)";
                                        }
                                    }}
                                    onMouseLeave={(e) => {
                                        if (!isCurrentMaterial) {
                                            e.currentTarget.style.backgroundColor = "transparent";
                                        }
                                    }}
                                >
                                    {/* Color Swatch */}
                                    <div
                                        style={{
                                            width: "24px",
                                            height: "24px",
                                            backgroundColor: getColorHex(item.material),
                                            border: "1px solid rgba(255, 255, 255, 0.3)",
                                            borderRadius: "3px",
                                            flexShrink: 0,
                                        }}
                                    />
                                    {/* Material Name */}
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div
                                            style={{
                                                fontSize: "0.75em",
                                                fontWeight: "bold",
                                                color: "white",
                                                overflow: "hidden",
                                                textOverflow: "ellipsis",
                                                whiteSpace: "nowrap",
                                            }}
                                        >
                                            {item.name}
                                            {isCurrentMaterial && (
                                                <span
                                                    style={{ color: "#4CAF50", marginLeft: "6px" }}
                                                >
                                                    ✓
                                                </span>
                                            )}
                                        </div>
                                        <div style={{ fontSize: "0.65em", color: "#999" }}>
                                            {item.usedBy.size} object
                                            {item.usedBy.size !== 1 ? "s" : ""}
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* Action Buttons */}
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {/* Edit Material Button */}
                {currentMaterial && (
                    <button
                        onClick={() => onEditMaterial(currentMaterial.id)}
                        style={{
                            width: "100%",
                            background: "#9C27B0",
                            border: "none",
                            color: "white",
                            padding: "8px 10px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.75em",
                            fontWeight: "bold",
                        }}
                        title="Edit this material in Material Library tab"
                    >
                        ✏️ Edit Material
                    </button>
                )}

                {/* Make Unique Button */}
                {isSharedMaterial && (
                    <button
                        onClick={() => onMakeUnique(selectedObject.uuid)}
                        style={{
                            width: "100%",
                            background: "#FF9800",
                            border: "none",
                            color: "white",
                            padding: "8px 10px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.75em",
                            fontWeight: "bold",
                        }}
                        title="Create a unique copy of this material for this object only"
                    >
                        🔓 Make Material Unique
                    </button>
                )}

                {/* Create & Assign Button */}
                <button
                    onClick={() => onCreateAndAssign(selectedObject.uuid)}
                    style={{
                        width: "100%",
                        background: "linear-gradient(135deg, #9C27B0 0%, #E91E63 100%)",
                        border: "none",
                        color: "white",
                        padding: "8px 10px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.75em",
                        fontWeight: "bold",
                    }}
                    title="Create a new material and assign it to this object"
                >
                    ➕ Create & Assign New Material
                </button>
            </div>

            {/* Info Text */}
            {isSharedMaterial && (
                <div
                    style={{
                        marginTop: "8px",
                        padding: "6px 8px",
                        backgroundColor: "rgba(255, 152, 0, 0.15)",
                        borderRadius: "4px",
                        fontSize: "0.65em",
                        color: "#FFA726",
                        lineHeight: "1.4",
                    }}
                >
                    ℹ️ This material is shared. Changes in Material Editor will affect all{" "}
                    {currentMaterial?.usedBy.size} objects. Use "Make Unique" to edit independently.
                </div>
            )}
        </div>
    );
};

export default NeedleUSDObjectMaterialAssignment;
