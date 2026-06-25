/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import ModelTree from "./components/ModelTree";
import Properties from "./components/Properties";
import Tools from "./components/Tools";
import { IfcViewerInstance, SelectedElement, SpatialNode } from "./types";

interface ThatOpenWebIfcPanelProps {
    instance: IfcViewerInstance;
    bundle: any;
    spatialTree: SpatialNode | null;
    selectedElement: SelectedElement | null;
    selectedLocalIds: number[];
    onSelectLocalIds: (localIds: number[]) => void;
    onClose?: () => void;
}

type TabKey = "modelTree" | "properties" | "tools";

const ThatOpenWebIfcPanel: React.FC<ThatOpenWebIfcPanelProps> = ({
    instance,
    bundle,
    spatialTree,
    selectedElement,
    selectedLocalIds,
    onSelectLocalIds,
    onClose,
}) => {
    // Tools is the default tab (camera + section + measure are the most-used).
    const [activeTab, setActiveTab] = useState<TabKey>("tools");

    if (!instance?.components || !instance?.world) {
        return null;
    }

    const tabButton = (key: TabKey, icon: string, label: string, color: string) => (
        <button
            onClick={() => setActiveTab(key)}
            style={{
                flex: 1,
                minWidth: "70px",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "3px",
                background: activeTab === key ? `${color}33` : "transparent",
                border: "none",
                borderBottom: activeTab === key ? `2px solid ${color}` : "2px solid transparent",
                color: activeTab === key ? "#fff" : "rgba(255,255,255,0.7)",
                padding: "10px 6px",
                cursor: "pointer",
                fontSize: "0.7em",
                fontWeight: activeTab === key ? 700 : 400,
                transition: "background 0.12s ease, color 0.12s ease",
            }}
            title={label}
        >
            <span style={{ fontSize: "1.4em", lineHeight: 1 }}>{icon}</span>
            <span>{label}</span>
        </button>
    );

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
            {/* Header with tabs */}
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                }}
            >
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
                        }}
                        title="Hide panel (Esc)"
                    >
                        ×
                    </button>
                )}
                <div style={{ display: "flex", flex: 1, overflowX: "auto" }}>
                    {tabButton("modelTree", "🌳", "Model", "#4CAF50")}
                    {tabButton("properties", "📋", "Properties", "#FF9800")}
                    {tabButton("tools", "⚙️", "Tools", "#2196F3")}
                </div>
            </div>

            {/* Tab content */}
            {activeTab === "modelTree" && (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                    }}
                >
                    <ModelTree
                        instance={instance}
                        tree={spatialTree}
                        selectedLocalIds={selectedLocalIds}
                        onSelectLocalIds={onSelectLocalIds}
                    />
                </div>
            )}
            {activeTab === "properties" && (
                <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>
                    <Properties selectedElement={selectedElement} />
                </div>
            )}
            {activeTab === "tools" && (
                <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>
                    <Tools instance={instance} bundle={bundle} />
                </div>
            )}
        </div>
    );
};

export default ThatOpenWebIfcPanel;
