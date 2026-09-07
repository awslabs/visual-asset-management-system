/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Controls from "./components/Controls";
import SplatInfo from "./components/SplatInfo";
import RenderSettings from "./components/RenderSettings";

interface PlayCanvasPanelProps {
    /** PlayCanvas Application instance. */
    app: any;
    /** PlayCanvas camera Entity. */
    camera: any;
    /** PlayCanvas Entity holding the GSplat component (or null until loaded). */
    splatEntity: any;
    /** Camera-controls handle returned by `createCameraControls` in the parent. */
    cameraControls: any;
    /** Source asset key/file name shown in the Splat Info tab. */
    fileName?: string;
    /** Hide-the-panel handler (also bound to Esc by the parent). */
    onClose?: () => void;
    /** Reset camera to its initial framing computed at load time. */
    onResetView?: () => void;
    /** Optional ref kept in sync with the active tab id. */
    activeTabRef?: React.MutableRefObject<"splatInfo" | "renderSettings" | "controls">;
}

/**
 * Floating control panel for the PlayCanvas Gaussian Splat viewer.
 *
 * Mirrors the structure and styling of `ThreeJSPanel` and `BabylonJSPanel`:
 * a 3-tab dark overlay with a close button, fixed to the left edge of the
 * viewport. Tabs are adapted for splat-specific concepts:
 *
 *   🔍 Splat Info       — file/splat counts, world bounds, camera state, FPS
 *   🎚️ Render Settings  — splat scale, visibility, FOV, pixel ratio
 *   ⚙️ Controls         — camera presets, fit, background, auto-rotate, reset
 *
 * The look-and-feel intentionally matches the ThreeJS and BabylonJS
 * viewers so the three feel like sibling tools.
 */
const PlayCanvasPanel: React.FC<PlayCanvasPanelProps> = ({
    app,
    camera,
    splatEntity,
    cameraControls,
    fileName,
    onClose,
    onResetView,
    activeTabRef,
}) => {
    const [activeTab, setActiveTab] = useState<"splatInfo" | "renderSettings" | "controls">(
        "controls"
    );

    React.useEffect(() => {
        if (activeTabRef) {
            activeTabRef.current = activeTab;
        }
    }, [activeTab, activeTabRef]);

    if (!app || !camera) {
        return null;
    }

    return (
        <div
            style={{
                position: "absolute",
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

                <div style={{ display: "flex", flex: 1, overflowX: "auto" }}>
                    <button
                        onClick={() => setActiveTab("splatInfo")}
                        style={{
                            flex: 1,
                            minWidth: "70px",
                            background:
                                activeTab === "splatInfo"
                                    ? "rgba(76, 175, 80, 0.3)"
                                    : "transparent",
                            border: "none",
                            borderBottom:
                                activeTab === "splatInfo"
                                    ? "2px solid #4CAF50"
                                    : "2px solid transparent",
                            color: "white",
                            padding: "12px 8px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                            fontWeight: activeTab === "splatInfo" ? "bold" : "normal",
                        }}
                        title="Splat Info"
                    >
                        🔍
                    </button>
                    <button
                        onClick={() => setActiveTab("renderSettings")}
                        style={{
                            flex: 1,
                            minWidth: "70px",
                            background:
                                activeTab === "renderSettings"
                                    ? "rgba(156, 39, 176, 0.3)"
                                    : "transparent",
                            border: "none",
                            borderBottom:
                                activeTab === "renderSettings"
                                    ? "2px solid #9C27B0"
                                    : "2px solid transparent",
                            color: "white",
                            padding: "12px 8px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                            fontWeight: activeTab === "renderSettings" ? "bold" : "normal",
                        }}
                        title="Render Settings"
                    >
                        🎚️
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
            {activeTab === "splatInfo" && (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                    }}
                >
                    <SplatInfo
                        app={app}
                        camera={camera}
                        splatEntity={splatEntity}
                        fileName={fileName}
                    />
                </div>
            )}

            {activeTab === "renderSettings" && (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                    }}
                >
                    <RenderSettings app={app} camera={camera} splatEntity={splatEntity} />
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
                        app={app}
                        camera={camera}
                        splatEntity={splatEntity}
                        cameraControls={cameraControls}
                        onResetView={onResetView}
                    />
                </div>
            )}
        </div>
    );
};

export default PlayCanvasPanel;
