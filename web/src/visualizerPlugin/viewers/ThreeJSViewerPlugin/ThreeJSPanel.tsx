/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Controls from "./components/Controls";
import SceneGraph from "./components/SceneGraph";
import MaterialLibrary from "./components/MaterialLibrary";
import { MaterialLibraryItem } from "./ThreeJSMaterialLibrary";

interface ThreeJSPanelProps {
    scene: any;
    camera: any;
    renderer: any;
    threeRoot: any;
    controls: any;
    selectedObjects?: any[];
    onSelectObjects?: (objects: any[]) => void;
    onClose?: () => void;
    originalTransforms?: Map<string, any>;
    // Material library props
    materialLibrary: Map<string, MaterialLibraryItem>;
    selectedMaterialId: string | null;
    onSelectMaterial: (materialId: string | null) => void;
    onCreateMaterial: () => void;
    onRenameMaterial: (materialId: string, newName: string) => void;
    onMaterialChange: (materialId: string, materialState: any) => void;
    onResetMaterial: (materialId: string) => void;
    onDuplicateMaterial: (materialId: string) => void;
    onDeleteMaterial: (materialId: string) => void;
    onAssignMaterial: (objectUuid: string, materialId: string) => void;
    onMakeUnique: (objectUuid: string) => void;
    onCreateAndAssign: (objectUuid: string) => void;
    onEditMaterial: (materialId: string) => void;
    onResetAllTransforms: () => void;
    onResetAllMaterials: () => void;
    onClearSelection: () => void;
    // Control props
    enable3DSelection: boolean;
    onToggle3DSelection: (enabled: boolean) => void;
    // Animation props
    animations?: any[];
    animationPaused?: boolean;
    onToggleAnimation?: (paused: boolean) => void;
    activeTabRef?: React.MutableRefObject<"sceneGraph" | "materialLibrary" | "controls">;
}

const ThreeJSPanel: React.FC<ThreeJSPanelProps> = ({
    scene,
    camera,
    renderer,
    threeRoot,
    controls,
    selectedObjects,
    onSelectObjects,
    onClose,
    originalTransforms,
    materialLibrary,
    selectedMaterialId,
    onSelectMaterial,
    onCreateMaterial,
    onRenameMaterial,
    onMaterialChange,
    onResetMaterial,
    onDuplicateMaterial,
    onDeleteMaterial,
    onAssignMaterial,
    onMakeUnique,
    onCreateAndAssign,
    onEditMaterial,
    onResetAllTransforms,
    onResetAllMaterials,
    onClearSelection,
    enable3DSelection,
    onToggle3DSelection,
    animations,
    animationPaused,
    onToggleAnimation,
    activeTabRef,
}) => {
    const [activeTab, setActiveTab] = useState<"sceneGraph" | "materialLibrary" | "controls">(
        "sceneGraph"
    );
    const [forceShowEditor, setForceShowEditor] = useState(false);

    // Sync activeTab with ref if provided
    React.useEffect(() => {
        if (activeTabRef) {
            activeTabRef.current = activeTab;
        }
    }, [activeTab, activeTabRef]);

    // Reset forceShowEditor when switching away from material library
    React.useEffect(() => {
        if (activeTab !== "materialLibrary") {
            setForceShowEditor(false);
        }
    }, [activeTab]);

    if (!scene || !camera || !renderer || !threeRoot) {
        return null;
    }

    // Wrapper for onEditMaterial that also switches tabs and forces editor view
    const handleEditMaterialWithTabSwitch = (materialId: string) => {
        onEditMaterial(materialId);
        setForceShowEditor(true);
        setActiveTab("materialLibrary");
    };

    return (
        <div
            style={{
                position: "fixed",
                top: "20px",
                left: "10px",
                bottom: "20px",
                backgroundColor: "rgba(0, 0, 0, 0.85)",
                color: "white",
                borderRadius: "8px",
                fontSize: "0.85em",
                zIndex: 1000,
                minWidth: "280px",
                maxWidth: "320px",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
            }}
        >
            {/* Header with Tabs */}
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                }}
            >
                {/* Close Button */}
                {onClose && (
                    <button
                        onClick={onClose}
                        style={{
                            background: "none",
                            border: "none",
                            color: "white",
                            cursor: "pointer",
                            fontSize: "16px",
                            padding: "16px 12px",
                            width: "auto",
                            height: "auto",
                        }}
                        title="Hide panel (Esc)"
                    >
                        ×
                    </button>
                )}

                {/* Tab Buttons */}
                <div style={{ display: "flex", flex: 1, overflowX: "auto" }}>
                    <button
                        onClick={() => setActiveTab("sceneGraph")}
                        style={{
                            flex: 1,
                            minWidth: "70px",
                            background:
                                activeTab === "sceneGraph"
                                    ? "rgba(76, 175, 80, 0.3)"
                                    : "transparent",
                            border: "none",
                            borderBottom:
                                activeTab === "sceneGraph"
                                    ? "2px solid #4CAF50"
                                    : "2px solid transparent",
                            color: "white",
                            padding: "12px 8px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                            fontWeight: activeTab === "sceneGraph" ? "bold" : "normal",
                        }}
                        title="Scene Graph"
                    >
                        🌳
                    </button>
                    <button
                        onClick={() => setActiveTab("materialLibrary")}
                        style={{
                            flex: 1,
                            minWidth: "70px",
                            background:
                                activeTab === "materialLibrary"
                                    ? "rgba(156, 39, 176, 0.3)"
                                    : "transparent",
                            border: "none",
                            borderBottom:
                                activeTab === "materialLibrary"
                                    ? "2px solid #9C27B0"
                                    : "2px solid transparent",
                            color: "white",
                            padding: "12px 8px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                            fontWeight: activeTab === "materialLibrary" ? "bold" : "normal",
                        }}
                        title="Material Library"
                    >
                        📚
                    </button>
                    <button
                        onClick={() => setActiveTab("controls")}
                        style={{
                            flex: 1,
                            minWidth: "70px",
                            background:
                                activeTab === "controls"
                                    ? "rgba(33, 150, 243, 0.3)"
                                    : "transparent",
                            border: "none",
                            borderBottom:
                                activeTab === "controls"
                                    ? "2px solid #2196F3"
                                    : "2px solid transparent",
                            color: "white",
                            padding: "12px 8px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                            fontWeight: activeTab === "controls" ? "bold" : "normal",
                        }}
                        title="Controls"
                    >
                        ⚙️
                    </button>
                </div>
            </div>

            {/* Tab Content */}
            {activeTab === "sceneGraph" && (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                    }}
                >
                    <SceneGraph
                        scene={scene}
                        camera={camera}
                        threeRoot={threeRoot}
                        controls={controls}
                        selectedObjects={selectedObjects}
                        onSelectObjects={onSelectObjects}
                        onClose={undefined}
                        originalTransforms={originalTransforms}
                        materialLibrary={materialLibrary}
                        onAssignMaterial={onAssignMaterial}
                        onMakeUnique={onMakeUnique}
                        onCreateAndAssign={onCreateAndAssign}
                        onEditMaterial={handleEditMaterialWithTabSwitch}
                    />
                </div>
            )}

            {activeTab === "materialLibrary" && (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                    }}
                >
                    <MaterialLibrary
                        materialLibrary={materialLibrary}
                        selectedMaterialId={selectedMaterialId}
                        onSelectMaterial={onSelectMaterial}
                        onCreateMaterial={onCreateMaterial}
                        onRenameMaterial={onRenameMaterial}
                        onMaterialChange={onMaterialChange}
                        onResetMaterial={onResetMaterial}
                        onDuplicateMaterial={onDuplicateMaterial}
                        onDeleteMaterial={onDeleteMaterial}
                        scene={scene}
                        forceShowEditor={forceShowEditor}
                    />
                </div>
            )}

            {activeTab === "controls" && (
                <div
                    style={{
                        flex: 1,
                        overflowY: "auto",
                        overflowX: "hidden",
                        padding: "16px",
                        paddingBottom: "24px",
                        scrollbarWidth: "thin",
                        scrollbarColor: "rgba(255, 255, 255, 0.5) transparent",
                    }}
                >
                    <Controls
                        scene={scene}
                        camera={camera}
                        renderer={renderer}
                        threeRoot={threeRoot}
                        controls={controls}
                        onClose={undefined}
                        onClearSelection={onClearSelection}
                        onResetAllTransforms={onResetAllTransforms}
                        onResetAllMaterials={onResetAllMaterials}
                        enable3DSelection={enable3DSelection}
                        onToggle3DSelection={onToggle3DSelection}
                        animations={animations}
                        animationPaused={animationPaused}
                        onToggleAnimation={onToggleAnimation}
                    />
                </div>
            )}
        </div>
    );
};

export default ThreeJSPanel;
