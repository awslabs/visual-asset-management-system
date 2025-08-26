/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import { useViewerContext } from "../../context/ViewerContext";
import { MeshListDisplay } from "../panels/MeshListDisplay";

interface NavigatorFilesProps {
    model: any;
    onFileBrowseClick: () => void;
}

const NavigatorFiles: React.FC<NavigatorFilesProps> = ({ model, onFileBrowseClick }) => {
    const { state } = useViewerContext();
    const [fileNames, setFileNames] = useState<string[]>([]);

    useEffect(() => {
        // Extract file names from the loaded model
        if (state.model && state.model.loaded) {
            const names: string[] = [];

            // Try multiple sources for file names
            const fileSources = [state.model.files, state.model.fileNames, state.model.urls];

            for (const source of fileSources) {
                if (source && Array.isArray(source) && source.length > 0) {
                    for (const file of source) {
                        if (typeof file === "string") {
                            // If it's a URL, extract filename
                            if (file.includes("/")) {
                                const urlParts = file.split("/");
                                const fileName = urlParts[urlParts.length - 1];
                                // Remove query parameters if any
                                const cleanFileName = fileName.split("?")[0];
                                names.push(cleanFileName || file);
                            } else {
                                // It's already a filename
                                names.push(file);
                            }
                        } else if (file && typeof file === "object" && file.name) {
                            // File object
                            names.push(file.name);
                        }
                    }
                    break; // Use the first non-empty source
                }
            }

            // Remove duplicates and empty names
            const uniqueNames = Array.from(new Set(names)).filter(
                (name) => name && name.trim() !== ""
            );
            setFileNames(uniqueNames);

            console.log("NavigatorFiles: Extracted file names:", uniqueNames);
        } else {
            setFileNames([]);
        }
    }, [state.model]);

    return (
        <div className="ov-files-panel">
            {fileNames.length > 0 ? (
                <div className="ov-file-list">
                    {fileNames.map((fileName, index) => (
                        <div key={index} className="ov-file-item">
                            <div className="ov_svg_icon">
                                <i className="icon-files"></i>
                            </div>
                            <span title={fileName}>{fileName}</span>
                        </div>
                    ))}
                </div>
            ) : (
                <div className="ov-empty-state">
                    <p>No files loaded</p>
                    <div className="ov_button" onClick={onFileBrowseClick}>
                        Browse Files
                    </div>
                </div>
            )}
        </div>
    );
};

interface NavigatorMaterialsProps {
    model: any;
    viewer: any;
    selection: any;
    onMaterialSelected: (materialIndex: number) => void;
    onMeshSelected: (meshId: any) => void;
}

const NavigatorMaterials: React.FC<NavigatorMaterialsProps> = ({
    model,
    viewer,
    selection,
    onMaterialSelected,
    onMeshSelected,
}) => {
    const [materials, setMaterials] = useState<any[]>([]);
    const [usedByMeshes, setUsedByMeshes] = useState<any[]>([]);

    useEffect(() => {
        if (!model || !viewer) return;

        // Extract materials from model
        const extractedMaterials = [];
        try {
            if (model.MaterialCount && typeof model.MaterialCount === "function") {
                for (let i = 0; i < model.MaterialCount(); i++) {
                    const material = model.GetMaterial(i);
                    if (material) {
                        extractedMaterials.push({
                            index: i,
                            name: material.name || `Material ${i}`,
                            color: material.color || { r: 200, g: 200, b: 200 },
                        });
                    }
                }
            }
        } catch (error) {
            console.warn("Error extracting materials:", error);
        }

        setMaterials(extractedMaterials);
    }, [model, viewer]);

    useEffect(() => {
        // Update meshes that use the selected material
        if (selection?.type === "material" && viewer) {
            try {
                const meshes: any[] = [];
                if (viewer.EnumerateMeshesAndLinesUserData) {
                    viewer.EnumerateMeshesAndLinesUserData((meshUserData: any) => {
                        if (
                            selection.materialIndex === null ||
                            meshUserData.originalMaterials?.indexOf(selection.materialIndex) !== -1
                        ) {
                            meshes.push(meshUserData.originalMeshInstance);
                        }
                    });
                }
                setUsedByMeshes(meshes);
            } catch (error) {
                console.warn("Error getting meshes for material:", error);
                setUsedByMeshes([]);
            }
        } else {
            setUsedByMeshes([]);
        }
    }, [selection, viewer]);

    const handleMaterialClick = (materialIndex: number) => {
        onMaterialSelected(materialIndex);
    };

    const handleMeshClick = (meshInstance: any) => {
        if (meshInstance?.id) {
            onMeshSelected(meshInstance.id);
        }
    };

    const rgbToHex = (color: any) => {
        if (!color) return "#cccccc";
        const r = Math.round(color.r || 200);
        const g = Math.round(color.g || 200);
        const b = Math.round(color.b || 200);
        return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
    };

    return (
        <div className="ov-materials-panel">
            {materials.length > 0 ? (
                <>
                    <div className="ov-material-list">
                        {materials.map((material) => (
                            <div
                                key={material.index}
                                className={`ov-material-item ${
                                    selection?.type === "material" &&
                                    selection?.materialIndex === material.index
                                        ? "selected"
                                        : ""
                                }`}
                                onClick={() => handleMaterialClick(material.index)}
                            >
                                <div
                                    className="ov_color_circle"
                                    style={{ backgroundColor: rgbToHex(material.color) }}
                                ></div>
                                <span>{material.name}</span>
                            </div>
                        ))}
                    </div>

                    {usedByMeshes.length > 0 && (
                        <div className="ov-material-meshes">
                            <h4>Used by meshes:</h4>
                            <div className="ov-mesh-list">
                                {usedByMeshes.map((meshInstance, index) => (
                                    <div
                                        key={index}
                                        className="ov-mesh-item"
                                        onClick={() => handleMeshClick(meshInstance)}
                                    >
                                        <div className="ov_svg_icon">
                                            <i className="icon-tree_mesh"></i>
                                        </div>
                                        <span>{meshInstance.name || `Mesh ${index}`}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </>
            ) : (
                <div className="ov-empty-state">
                    <p>No materials to display</p>
                </div>
            )}
        </div>
    );
};

export const LeftPanel: React.FC = () => {
    const { state, selection, setSelection } = useViewerContext();
    const [activeTab, setActiveTab] = useState<"files" | "materials" | "meshes">("meshes");
    const [isVisible, setIsVisible] = useState(true);

    const toggleVisibility = () => {
        setIsVisible(!isVisible);
    };

    const handleTabClick = (tab: "files" | "materials" | "meshes") => {
        if (activeTab === tab && isVisible) {
            // If clicking the same tab while visible, hide the panel
            setIsVisible(false);
        } else {
            // Show panel and switch to tab
            setIsVisible(true);
            setActiveTab(tab);
        }
    };

    const handleMaterialSelected = useCallback(
        (materialIndex: number) => {
            setSelection({
                type: "Material",
                materialIndex: materialIndex,
                meshInstanceId: undefined,
            });
        },
        [setSelection]
    );

    const handleMeshSelected = useCallback(
        (meshId: any) => {
            setSelection({
                type: "Mesh",
                materialIndex: undefined,
                meshInstanceId: meshId,
            });
        },
        [setSelection]
    );

    const handleFileBrowseClick = useCallback(() => {
        // This would trigger file browser - for now just log
        console.log("File browse clicked");
    }, []);

    if (!isVisible) {
        return (
            <div className="ov-left-panel-collapsed">
                <button
                    className="ov-panel-toggle-button"
                    onClick={toggleVisibility}
                    title="Show navigator"
                >
                    ▶
                </button>
            </div>
        );
    }

    return (
        <div className="ov-left-panel ov_panel_set_container">
            <div className="ov_panel_set_menu">
                <div
                    className={`ov_panel_set_menu_button ${
                        activeTab === "files" ? "selected" : ""
                    }`}
                    onClick={() => handleTabClick("files")}
                    title="Files"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-files"></i>
                    </div>
                </div>
                <div
                    className={`ov_panel_set_menu_button ${
                        activeTab === "materials" ? "selected" : ""
                    }`}
                    onClick={() => handleTabClick("materials")}
                    title="Materials"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-materials"></i>
                    </div>
                </div>
                <div
                    className={`ov_panel_set_menu_button ${
                        activeTab === "meshes" ? "selected" : ""
                    }`}
                    onClick={() => handleTabClick("meshes")}
                    title="Meshes"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-meshes"></i>
                    </div>
                </div>
            </div>

            <div className="ov_panel_set_content">
                {activeTab === "files" && (
                    <NavigatorFiles model={state.model} onFileBrowseClick={handleFileBrowseClick} />
                )}

                {activeTab === "materials" && (
                    <NavigatorMaterials
                        model={state.model}
                        viewer={state.viewer}
                        selection={selection}
                        onMaterialSelected={handleMaterialSelected}
                        onMeshSelected={handleMeshSelected}
                    />
                )}

                {activeTab === "meshes" && (
                    <div className="ov-meshes-panel">
                        {state.model ? (
                            <MeshListDisplay
                                model={state.model}
                                viewer={state.viewer}
                                selection={selection}
                                onMeshSelected={handleMeshSelected}
                                onMaterialSelected={handleMaterialSelected}
                            />
                        ) : (
                            <div className="ov-empty-state">
                                <p>No meshes to display</p>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};
