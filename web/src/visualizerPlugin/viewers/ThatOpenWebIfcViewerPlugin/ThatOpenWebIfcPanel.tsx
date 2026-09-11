/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useState } from "react";
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
    const tabRefs = useRef<Partial<Record<TabKey, HTMLButtonElement | null>>>({});

    if (!instance?.components || !instance?.world) {
        return null;
    }

    const tabOrder: TabKey[] = ["modelTree", "properties", "tools"];

    // Left/Right move between tabs and activate, per the ARIA tabs pattern.
    const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, key: TabKey) => {
        const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (delta === 0) return;
        event.preventDefault();
        const index = tabOrder.indexOf(key);
        const next = tabOrder[(index + delta + tabOrder.length) % tabOrder.length];
        setActiveTab(next);
        tabRefs.current[next]?.focus();
    };

    const tabButton = (key: TabKey, icon: string, label: string, color: string) => (
        <button
            type="button"
            role="tab"
            id={`thatopenwebifc-tab-${key}`}
            aria-selected={activeTab === key}
            aria-controls={`thatopenwebifc-tabpanel-${key}`}
            tabIndex={activeTab === key ? 0 : -1}
            ref={(el) => {
                tabRefs.current[key] = el;
            }}
            onClick={() => setActiveTab(key)}
            onKeyDown={(event) => handleTabKeyDown(event, key)}
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
            <span style={{ fontSize: "1.4em", lineHeight: 1 }} aria-hidden="true">
                {icon}
            </span>
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
                        type="button"
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
                        aria-label="Hide panel"
                    >
                        <span aria-hidden="true">×</span>
                    </button>
                )}
                <div
                    style={{ display: "flex", flex: 1, overflowX: "auto" }}
                    role="tablist"
                    aria-label="IFC viewer panels"
                >
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
                    role="tabpanel"
                    id="thatopenwebifc-tabpanel-modelTree"
                    aria-labelledby="thatopenwebifc-tab-modelTree"
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
                <div
                    style={{ flex: 1, overflowY: "auto", padding: "16px" }}
                    role="tabpanel"
                    id="thatopenwebifc-tabpanel-properties"
                    aria-labelledby="thatopenwebifc-tab-properties"
                >
                    <Properties selectedElement={selectedElement} />
                </div>
            )}
            {activeTab === "tools" && (
                <div
                    style={{ flex: 1, overflowY: "auto", padding: "16px" }}
                    role="tabpanel"
                    id="thatopenwebifc-tabpanel-tools"
                    aria-labelledby="thatopenwebifc-tab-tools"
                >
                    <Tools instance={instance} bundle={bundle} />
                </div>
            )}
        </div>
    );
};

export default ThatOpenWebIfcPanel;
