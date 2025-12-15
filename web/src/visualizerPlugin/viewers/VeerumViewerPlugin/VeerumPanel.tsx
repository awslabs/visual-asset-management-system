/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import VeerumControls from "./VeerumControls";
import VeerumSceneGraph from "./VeerumSceneGraph";

interface VeerumPanelProps {
    viewerController: any;
    loadedModels: any[];
    initError: string | null;
    onClose?: () => void;
}

const VeerumPanel: React.FC<VeerumPanelProps> = ({
    viewerController,
    loadedModels,
    initError,
    onClose,
}) => {
    const [activeTab, setActiveTab] = useState<"controls" | "sceneGraph">("controls");

    if (!viewerController || loadedModels.length === 0) {
        return null;
    }

    return (
        <div
            style={{
                position: "fixed",
                top: initError ? "50px" : "20px",
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
                <div style={{ display: "flex", flex: 1 }}>
                    <button
                        onClick={() => setActiveTab("controls")}
                        style={{
                            flex: 1,
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
                            padding: "12px 16px",
                            cursor: "pointer",
                            fontSize: "0.9em",
                            fontWeight: activeTab === "controls" ? "bold" : "normal",
                        }}
                    >
                        ⚙️ Controls
                    </button>
                    <button
                        onClick={() => setActiveTab("sceneGraph")}
                        style={{
                            flex: 1,
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
                            padding: "12px 16px",
                            cursor: "pointer",
                            fontSize: "0.9em",
                            fontWeight: activeTab === "sceneGraph" ? "bold" : "normal",
                        }}
                    >
                        🌳 Scene
                    </button>
                </div>
            </div>

            {/* Tab Content */}
            <div
                style={{
                    flex: 1,
                    overflowY: "auto",
                    overflowX: "hidden",
                    scrollbarWidth: "thin",
                    scrollbarColor: "rgba(255, 255, 255, 0.5) transparent",
                }}
            >
                {activeTab === "controls" ? (
                    <div style={{ padding: "16px", paddingBottom: "24px" }}>
                        <VeerumControls
                            viewerController={viewerController}
                            loadedModels={loadedModels}
                            initError={initError}
                            onClose={undefined} // Don't show close button in tab mode
                        />
                    </div>
                ) : (
                    <VeerumSceneGraph
                        viewerController={viewerController}
                        loadedModels={loadedModels}
                        initError={initError}
                        onClose={undefined} // Don't show close button in tab mode
                    />
                )}
            </div>
        </div>
    );
};

export default VeerumPanel;
