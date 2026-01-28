/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";

interface MaterialState {
    color: string;
    emissive: string;
    metalness: number;
    roughness: number;
    opacity: number;
    transparent: boolean;
    wireframe: boolean;
}

interface NeedleUSDMaterialControlsProps {
    selectedObject: any;
    baseMaterial: any;
    onMaterialChange: (object: any, material: MaterialState) => void;
    onResetMaterial: () => void;
}

const NeedleUSDMaterialControls: React.FC<NeedleUSDMaterialControlsProps> = ({
    selectedObject,
    baseMaterial,
    onMaterialChange,
    onResetMaterial,
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

    const THREE = (window as any).THREE;

    // Update material state when selected object changes - read from BASE material, not highlighted
    useEffect(() => {
        if (baseMaterial && THREE) {
            const mat = baseMaterial;

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
    }, [baseMaterial, THREE, updateTrigger]);

    const updateMaterial = (property: keyof MaterialState, value: any) => {
        const newMaterial = {
            ...material,
            [property]: value,
        };
        setMaterial(newMaterial);
        onMaterialChange(selectedObject, newMaterial);
    };

    if (!selectedObject || !baseMaterial) {
        return (
            <div style={{ padding: "16px", textAlign: "center", color: "#999", fontSize: "0.8em" }}>
                Selected object has no material
            </div>
        );
    }

    return (
        <div
            style={{
                borderTop: "1px solid rgba(255, 255, 255, 0.1)",
                padding: "12px 16px",
                backgroundColor: "rgba(233, 30, 99, 0.1)",
            }}
        >
            <h5
                style={{
                    margin: "0 0 12px 0",
                    fontSize: "0.9em",
                    color: "#E91E63",
                    textAlign: "center",
                }}
            >
                🎨 Material Properties
            </h5>

            {/* Base Color */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
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
                            width: "40px",
                            height: "30px",
                            border: "1px solid rgba(255, 255, 255, 0.2)",
                            borderRadius: "4px",
                            cursor: "pointer",
                        }}
                    />
                    <input
                        type="text"
                        value={material.color}
                        onChange={(e) => updateMaterial("color", e.target.value)}
                        style={{
                            flex: 1,
                            padding: "4px 8px",
                            borderRadius: "4px",
                            border: "1px solid rgba(255, 255, 255, 0.2)",
                            backgroundColor: "rgba(255, 255, 255, 0.1)",
                            color: "white",
                            fontSize: "0.75em",
                        }}
                    />
                </div>
            </div>

            {/* Emissive Color */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
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
                            width: "40px",
                            height: "30px",
                            border: "1px solid rgba(255, 255, 255, 0.2)",
                            borderRadius: "4px",
                            cursor: "pointer",
                        }}
                    />
                    <input
                        type="text"
                        value={material.emissive}
                        onChange={(e) => updateMaterial("emissive", e.target.value)}
                        style={{
                            flex: 1,
                            padding: "4px 8px",
                            borderRadius: "4px",
                            border: "1px solid rgba(255, 255, 255, 0.2)",
                            backgroundColor: "rgba(255, 255, 255, 0.1)",
                            color: "white",
                            fontSize: "0.75em",
                        }}
                    />
                </div>
            </div>

            {/* Metalness */}
            {baseMaterial.metalness !== undefined && (
                <div style={{ marginBottom: "12px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Metalness: {material.metalness.toFixed(2)}
                    </label>
                    <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={material.metalness}
                        onChange={(e) => updateMaterial("metalness", parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                </div>
            )}

            {/* Roughness */}
            {baseMaterial.roughness !== undefined && (
                <div style={{ marginBottom: "12px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Roughness: {material.roughness.toFixed(2)}
                    </label>
                    <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={material.roughness}
                        onChange={(e) => updateMaterial("roughness", parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                </div>
            )}

            {/* Opacity */}
            <div style={{ marginBottom: "12px" }}>
                <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                    Opacity: {material.opacity.toFixed(2)}
                </label>
                <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={material.opacity}
                    onChange={(e) => updateMaterial("opacity", parseFloat(e.target.value))}
                    style={{ width: "100%" }}
                />
            </div>

            {/* Boolean Options */}
            <div style={{ marginBottom: "12px" }}>
                <div
                    style={{
                        fontSize: "0.8em",
                        fontWeight: "bold",
                        marginBottom: "6px",
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
                        marginBottom: "4px",
                    }}
                >
                    <input
                        type="checkbox"
                        checked={material.transparent}
                        onChange={(e) => updateMaterial("transparent", e.target.checked)}
                        style={{ marginRight: "8px" }}
                    />
                    <span style={{ fontSize: "0.8em" }}>Transparent</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                    <input
                        type="checkbox"
                        checked={material.wireframe}
                        onChange={(e) => updateMaterial("wireframe", e.target.checked)}
                        style={{ marginRight: "8px" }}
                    />
                    <span style={{ fontSize: "0.8em" }}>Wireframe</span>
                </label>
            </div>

            {/* Material Info */}
            <div
                style={{
                    marginBottom: "12px",
                    padding: "8px",
                    backgroundColor: "rgba(0, 0, 0, 0.3)",
                    borderRadius: "4px",
                    fontSize: "0.7em",
                }}
            >
                <div>
                    <strong>Type:</strong> {baseMaterial.type}
                </div>
                {baseMaterial.name && (
                    <div>
                        <strong>Name:</strong> {baseMaterial.name}
                    </div>
                )}
            </div>

            {/* Reset Button */}
            <button
                onClick={() => {
                    onResetMaterial();
                    setUpdateTrigger((prev) => prev + 1);
                }}
                style={{
                    width: "100%",
                    background: "#F44336",
                    border: "none",
                    color: "white",
                    padding: "8px 12px",
                    borderRadius: "4px",
                    cursor: "pointer",
                    fontSize: "0.75em",
                }}
                title="Reset to original material"
            >
                ⟲ Reset Material
            </button>
        </div>
    );
};

export default NeedleUSDMaterialControls;
