/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { useViewerContext } from "../../context/ViewerContext";

interface MeshListDisplayProps {
    model: any;
    viewer?: any;
    selection?: any;
    onMeshSelected?: (meshId: any) => void;
    onMaterialSelected?: (materialIndex: number) => void;
}

interface MeshData {
    id: string;
    name: string;
    visible: boolean;
    triangleCount: number;
    vertexCount: number;
}

export const MeshListDisplay: React.FC<MeshListDisplayProps> = ({
    model,
    viewer,
    selection,
    onMeshSelected,
    onMaterialSelected,
}) => {
    const { state, setSelection } = useViewerContext();
    const [meshes, setMeshes] = useState<MeshData[]>([]);
    const [materials, setMaterials] = useState<any[]>([]);

    useEffect(() => {
        if (model) {
            try {
                const meshList: MeshData[] = [];

                // Extract mesh data from the model
                if (model.MeshCount) {
                    for (let i = 0; i < model.MeshCount(); i++) {
                        const mesh = model.GetMesh(i);
                        if (mesh) {
                            meshList.push({
                                id: `mesh_${i}`,
                                name: mesh.GetName ? mesh.GetName() : `Mesh ${i + 1}`,
                                visible: true,
                                triangleCount: mesh.TriangleCount ? mesh.TriangleCount() : 0,
                                vertexCount: mesh.VertexCount ? mesh.VertexCount() : 0,
                            });
                        }
                    }
                }

                // If no meshes found, create a default entry
                if (meshList.length === 0) {
                    meshList.push({
                        id: "mesh_0",
                        name: "Default Mesh",
                        visible: true,
                        triangleCount: 0,
                        vertexCount: 0,
                    });
                }

                setMeshes(meshList);
            } catch (error) {
                console.error("Error extracting mesh data:", error);
                // Fallback mesh data
                setMeshes([
                    {
                        id: "mesh_0",
                        name: "Loaded Model",
                        visible: true,
                        triangleCount: 0,
                        vertexCount: 0,
                    },
                ]);
            }
        }
    }, [model]);

    const getUnderlyingViewer = () => {
        if (state.viewer) {
            if (state.viewer.viewer) return state.viewer.viewer;
            if (state.viewer.GetViewer) return state.viewer.GetViewer();
            return state.viewer;
        }
        return null;
    };

    const handleMeshVisibilityToggle = async (meshId: string) => {
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                console.log(`Toggling visibility for mesh ${meshId}`);

                // Update local state first
                const updatedMeshes = meshes.map((mesh) =>
                    mesh.id === meshId ? { ...mesh, visible: !mesh.visible } : mesh
                );
                setMeshes(updatedMeshes);

                // Apply to viewer using SetMeshesVisibility with proper mesh filtering
                if (viewer.SetMeshesVisibility) {
                    viewer.SetMeshesVisibility((meshUserData: any) => {
                        // Find the corresponding mesh in our updated state
                        const meshIndex = parseInt(meshId.replace("mesh_", ""));
                        const correspondingMesh = updatedMeshes.find((m) => m.id === meshId);

                        // If this is the mesh we're toggling, return its new visibility state
                        if (
                            meshUserData.originalMeshInstance &&
                            meshUserData.originalMeshInstance.id &&
                            meshUserData.originalMeshInstance.id
                                .toString()
                                .includes(meshIndex.toString())
                        ) {
                            return correspondingMesh ? correspondingMesh.visible : true;
                        }

                        // For other meshes, check their visibility state
                        const otherMeshId = `mesh_${meshIndex}`;
                        const otherMesh = updatedMeshes.find((m) => m.id === otherMeshId);
                        return otherMesh ? otherMesh.visible : true;
                    });

                    // Force a render
                    viewer.Render();
                }
            } catch (error) {
                console.error("Error toggling mesh visibility:", error);
            }
        }
    };

    const handleMeshFitToWindow = async (meshId: string) => {
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                console.log(`Fitting mesh ${meshId} to window`);

                // Get bounding sphere for specific mesh and fit to window
                const boundingSphere = viewer.GetBoundingSphere((meshUserData: any) => {
                    // This would need proper mesh instance ID matching
                    return true; // For now, fit all
                });

                if (boundingSphere) {
                    viewer.FitSphereToWindow(boundingSphere, true);
                }
            } catch (error) {
                console.error("Error fitting mesh to window:", error);
            }
        }
    };

    const handleMeshSelect = (meshId: string) => {
        // Toggle selection if clicking on already selected mesh
        if (selection?.type === "Mesh" && selection?.meshInstanceId === meshId) {
            setSelection({
                type: null,
                materialIndex: undefined,
                meshInstanceId: undefined,
            });
        } else {
            setSelection({
                type: "Mesh",
                meshInstanceId: meshId,
                materialIndex: undefined,
            });
        }
    };

    return (
        <div className="ov-mesh-list">
            {meshes.map((mesh) => (
                <div
                    key={mesh.id}
                    className={`ov-mesh-item ${
                        selection?.type === "Mesh" && selection?.meshInstanceId === mesh.id
                            ? "selected"
                            : ""
                    }`}
                    onClick={() => handleMeshSelect(mesh.id)}
                >
                    <div className="ov-mesh-controls">
                        <button
                            className="ov-visibility-button"
                            onClick={(e) => {
                                e.stopPropagation();
                                handleMeshVisibilityToggle(mesh.id);
                            }}
                            title="Toggle visibility"
                        >
                            <div className="ov_svg_icon light">
                                <i className={mesh.visible ? "icon-visible" : "icon-hidden"}></i>
                            </div>
                        </button>
                        <button
                            className="ov-fit-button"
                            onClick={(e) => {
                                e.stopPropagation();
                                handleMeshFitToWindow(mesh.id);
                            }}
                            title="Fit to window"
                        >
                            <div className="ov_svg_icon light">
                                <i className="icon-fit"></i>
                            </div>
                        </button>
                    </div>
                    <span className="ov-mesh-name">{mesh.name}</span>
                </div>
            ))}
        </div>
    );
};
