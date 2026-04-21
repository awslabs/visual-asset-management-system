/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import MaterialEditor from "./MaterialEditor";
import { MaterialLibraryItem } from "../ThreeJSMaterialLibrary";

interface MaterialLibraryProps {
    materialLibrary: Map<string, MaterialLibraryItem>;
    selectedMaterialId: string | null;
    onSelectMaterial: (materialId: string | null) => void;
    onCreateMaterial: () => void;
    onRenameMaterial: (materialId: string, newName: string) => void;
    onMaterialChange: (materialId: string, materialState: any) => void;
    onResetMaterial: (materialId: string) => void;
    onDuplicateMaterial: (materialId: string) => void;
    onDeleteMaterial: (materialId: string) => void;
    scene: any;
    forceShowEditor?: boolean;
}

const MaterialLibrary: React.FC<MaterialLibraryProps> = ({
    materialLibrary,
    selectedMaterialId,
    onSelectMaterial,
    onCreateMaterial,
    onRenameMaterial,
    onMaterialChange,
    onResetMaterial,
    onDuplicateMaterial,
    onDeleteMaterial,
    scene,
    forceShowEditor,
}) => {
    const [searchTerm, setSearchTerm] = useState("");
    const [editingMaterialId, setEditingMaterialId] = useState<string | null>(null);
    const [editingName, setEditingName] = useState("");
    const [showEditor, setShowEditor] = useState(false);

    const THREE = (window as any).THREE;

    const selectedMaterialItem = selectedMaterialId
        ? materialLibrary.get(selectedMaterialId) || null
        : null;

    // Auto-show editor when forceShowEditor is true and a material is selected
    React.useEffect(() => {
        if (forceShowEditor && selectedMaterialId) {
            setShowEditor(true);
        }
    }, [forceShowEditor, selectedMaterialId]);

    // Filter materials based on search term
    const filteredMaterials = Array.from(materialLibrary.values()).filter((item) =>
        item.name.toLowerCase().includes(searchTerm.toLowerCase())
    );

    // Sort materials: custom first, then by name
    const sortedMaterials = filteredMaterials.sort((a, b) => {
        if (a.isCustom !== b.isCustom) {
            return a.isCustom ? -1 : 1;
        }
        return a.name.localeCompare(b.name);
    });

    const handleStartRename = (materialId: string, currentName: string) => {
        setEditingMaterialId(materialId);
        setEditingName(currentName);
    };

    const handleFinishRename = () => {
        if (editingMaterialId && editingName.trim()) {
            onRenameMaterial(editingMaterialId, editingName.trim());
        }
        setEditingMaterialId(null);
        setEditingName("");
    };

    const handleCancelRename = () => {
        setEditingMaterialId(null);
        setEditingName("");
    };

    const getColorHex = (material: any): string => {
        if (!material || !THREE) return "#808080";

        try {
            if (material.color) {
                return `#${material.color.getHexString()}`;
            }
        } catch (error) {
            console.warn("Error getting material color:", error);
        }
        return "#808080";
    };

    const getMaterialTypeIcon = (material: any): string => {
        if (!material) return "📦";

        const type = material.type || "";
        if (type.includes("Standard")) return "✨";
        if (type.includes("Basic")) return "🔷";
        if (type.includes("Physical")) return "💎";
        if (type.includes("Lambert")) return "🔵";
        if (type.includes("Phong")) return "⚪";
        return "📦";
    };

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
                    backgroundColor: "rgba(156, 39, 176, 0.2)",
                }}
            >
                <h4
                    style={{
                        margin: "0 0 8px 0",
                        fontSize: "1em",
                        color: "#9C27B0",
                        textAlign: "center",
                    }}
                >
                    🎨 Material Library
                </h4>
                <div style={{ fontSize: "0.75em", color: "#ccc", textAlign: "center" }}>
                    {materialLibrary.size} material{materialLibrary.size !== 1 ? "s" : ""} in scene
                </div>
            </div>

            {/* Search Bar */}
            <div
                style={{ padding: "8px 16px", borderBottom: "1px solid rgba(255, 255, 255, 0.1)" }}
            >
                <input
                    type="text"
                    placeholder="🔍 Search materials..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    style={{
                        width: "100%",
                        padding: "6px 10px",
                        borderRadius: "4px",
                        border: "1px solid rgba(255, 255, 255, 0.2)",
                        backgroundColor: "rgba(255, 255, 255, 0.1)",
                        color: "white",
                        fontSize: "0.8em",
                    }}
                />
            </div>

            {/* Create Material Button */}
            <div
                style={{ padding: "8px 16px", borderBottom: "1px solid rgba(255, 255, 255, 0.1)" }}
            >
                <button
                    onClick={onCreateMaterial}
                    style={{
                        width: "100%",
                        background: "linear-gradient(135deg, #9C27B0 0%, #E91E63 100%)",
                        border: "none",
                        color: "white",
                        padding: "10px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.85em",
                        fontWeight: "bold",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: "6px",
                    }}
                    title="Create a new material"
                >
                    <span style={{ fontSize: "1.2em" }}>➕</span>
                    Create New Material
                </button>
            </div>

            {/* Conditional View: Material List or Editor */}
            {!showEditor || !selectedMaterialItem ? (
                <>
                    {/* Material List */}
                    <div
                        style={{
                            flex: 1,
                            overflowY: "auto",
                            padding: "8px",
                            scrollbarWidth: "thin",
                            scrollbarColor: "rgba(255, 255, 255, 0.5) transparent",
                        }}
                    >
                        {sortedMaterials.length === 0 ? (
                            <div
                                style={{
                                    padding: "20px",
                                    textAlign: "center",
                                    color: "#999",
                                    fontSize: "0.85em",
                                }}
                            >
                                {searchTerm
                                    ? "No materials match your search"
                                    : "No materials in scene"}
                            </div>
                        ) : (
                            sortedMaterials.map((item) => {
                                const isSelected = item.id === selectedMaterialId;
                                const isEditing = item.id === editingMaterialId;
                                const colorHex = getColorHex(item.material);
                                const typeIcon = getMaterialTypeIcon(item.material);

                                return (
                                    <div
                                        key={item.id}
                                        style={{
                                            padding: "10px",
                                            marginBottom: "6px",
                                            backgroundColor: isSelected
                                                ? "rgba(156, 39, 176, 0.3)"
                                                : "rgba(255, 255, 255, 0.05)",
                                            border: isSelected
                                                ? "2px solid #9C27B0"
                                                : "1px solid rgba(255, 255, 255, 0.1)",
                                            borderRadius: "6px",
                                            cursor: "pointer",
                                            transition: "all 0.2s",
                                        }}
                                        onClick={() => {
                                            if (!isEditing) {
                                                onSelectMaterial(item.id);
                                                setShowEditor(true);
                                            }
                                        }}
                                        onMouseEnter={(e) => {
                                            if (!isSelected && !isEditing) {
                                                e.currentTarget.style.backgroundColor =
                                                    "rgba(255, 255, 255, 0.1)";
                                            }
                                        }}
                                        onMouseLeave={(e) => {
                                            if (!isSelected && !isEditing) {
                                                e.currentTarget.style.backgroundColor =
                                                    "rgba(255, 255, 255, 0.05)";
                                            }
                                        }}
                                    >
                                        <div
                                            style={{
                                                display: "flex",
                                                alignItems: "center",
                                                gap: "10px",
                                            }}
                                        >
                                            {/* Color Swatch */}
                                            <div
                                                style={{
                                                    width: "40px",
                                                    height: "40px",
                                                    backgroundColor: colorHex,
                                                    border: "2px solid rgba(255, 255, 255, 0.3)",
                                                    borderRadius: "6px",
                                                    flexShrink: 0,
                                                    boxShadow: "0 2px 4px rgba(0, 0, 0, 0.3)",
                                                }}
                                                title={`Color: ${colorHex}`}
                                            />

                                            {/* Material Info */}
                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                {/* Material Name */}
                                                {isEditing ? (
                                                    <input
                                                        type="text"
                                                        value={editingName}
                                                        onChange={(e) =>
                                                            setEditingName(e.target.value)
                                                        }
                                                        onBlur={handleFinishRename}
                                                        onKeyDown={(e) => {
                                                            if (e.key === "Enter") {
                                                                handleFinishRename();
                                                            } else if (e.key === "Escape") {
                                                                handleCancelRename();
                                                            }
                                                        }}
                                                        onClick={(e) => e.stopPropagation()}
                                                        autoFocus
                                                        style={{
                                                            width: "100%",
                                                            padding: "4px 6px",
                                                            borderRadius: "3px",
                                                            border: "1px solid #9C27B0",
                                                            backgroundColor:
                                                                "rgba(255, 255, 255, 0.1)",
                                                            color: "white",
                                                            fontSize: "0.85em",
                                                            fontWeight: "bold",
                                                        }}
                                                    />
                                                ) : (
                                                    <div
                                                        style={{
                                                            fontSize: "0.85em",
                                                            fontWeight: "bold",
                                                            color: isSelected ? "#9C27B0" : "white",
                                                            marginBottom: "4px",
                                                            overflow: "hidden",
                                                            textOverflow: "ellipsis",
                                                            whiteSpace: "nowrap",
                                                            display: "flex",
                                                            alignItems: "center",
                                                            gap: "6px",
                                                        }}
                                                    >
                                                        <span>{typeIcon}</span>
                                                        <span style={{ flex: 1, minWidth: 0 }}>
                                                            {item.name}
                                                        </span>
                                                        {item.isCustom && (
                                                            <span
                                                                style={{
                                                                    fontSize: "0.7em",
                                                                    backgroundColor:
                                                                        "rgba(156, 39, 176, 0.5)",
                                                                    padding: "2px 6px",
                                                                    borderRadius: "3px",
                                                                }}
                                                                title="User-created material"
                                                            >
                                                                CUSTOM
                                                            </span>
                                                        )}
                                                    </div>
                                                )}

                                                {/* Material Stats */}
                                                <div
                                                    style={{
                                                        fontSize: "0.7em",
                                                        color: "#999",
                                                        display: "flex",
                                                        gap: "8px",
                                                        alignItems: "center",
                                                    }}
                                                >
                                                    <span title="Number of objects using this material">
                                                        📦 {item.usedBy.size} object
                                                        {item.usedBy.size !== 1 ? "s" : ""}
                                                    </span>
                                                    <span>•</span>
                                                    <span title="Material type">
                                                        {item.material.type}
                                                    </span>
                                                </div>
                                            </div>

                                            {/* Rename Button */}
                                            {!isEditing && (
                                                <button
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleStartRename(item.id, item.name);
                                                    }}
                                                    style={{
                                                        background: "rgba(255, 255, 255, 0.1)",
                                                        border: "1px solid rgba(255, 255, 255, 0.2)",
                                                        color: "white",
                                                        padding: "4px 8px",
                                                        borderRadius: "3px",
                                                        cursor: "pointer",
                                                        fontSize: "0.75em",
                                                        flexShrink: 0,
                                                    }}
                                                    title="Rename material"
                                                >
                                                    ✏️
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </div>

                    {/* Footer Info */}
                    <div
                        style={{
                            padding: "8px 16px",
                            borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                            fontSize: "0.7em",
                            color: "#999",
                            textAlign: "center",
                        }}
                    >
                        {selectedMaterialId
                            ? "Click material to select • Double-click object to edit"
                            : "Select a material to edit its properties"}
                    </div>
                </>
            ) : (
                <>
                    {/* Back Button */}
                    <div
                        style={{
                            padding: "8px 16px",
                            borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                        }}
                    >
                        <button
                            onClick={() => {
                                setShowEditor(false);
                                onSelectMaterial(null);
                            }}
                            style={{
                                width: "100%",
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "8px 12px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                gap: "6px",
                            }}
                        >
                            ← Back to Library
                        </button>
                    </div>

                    {/* Material Editor */}
                    <div
                        style={{
                            flex: 1,
                            display: "flex",
                            flexDirection: "column",
                            overflow: "hidden",
                        }}
                    >
                        <MaterialEditor
                            materialLibraryItem={selectedMaterialItem}
                            onMaterialChange={onMaterialChange}
                            onResetMaterial={onResetMaterial}
                            onDuplicateMaterial={onDuplicateMaterial}
                            onDeleteMaterial={(materialId) => {
                                onDeleteMaterial(materialId);
                                setShowEditor(false);
                                onSelectMaterial(null);
                            }}
                            scene={scene}
                        />
                    </div>
                </>
            )}
        </div>
    );
};

export default MaterialLibrary;
