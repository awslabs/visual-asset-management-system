/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useState } from "react";

interface ControlsProps {
    /** BabylonJS Scene instance. */
    scene: any;
    /** BabylonJS ArcRotateCamera instance. */
    camera: any;
    /** BabylonJS Engine instance (for FPS, render loop control). */
    engine: any;
    /** BabylonJS GaussianSplattingMesh / loaded mesh used to compute scene bounds. */
    splatMesh: any;
    /** Optional close handler for the parent panel. */
    onClose?: () => void;
    /** Reset camera to its initial framing computed at load time. */
    onResetView?: () => void;
}

/**
 * Controls tab for the BabylonJS Gaussian Splat viewer panel.
 *
 * Provides camera-view presets (top/front/side/isometric), fit-to-scene,
 * zoom buttons, background color picker, auto-rotate toggle and a
 * "Reset Scene" action that returns the viewer to its initial framing.
 *
 * Mirrors the API and styling of the ThreeJS viewer's Controls component
 * but operates on BabylonJS APIs (ArcRotateCamera, Scene.clearColor, etc.)
 * and adapts gracefully for Gaussian Splat scenes (no lighting controls,
 * no global wireframe toggle — splats are point clouds).
 */
const Controls: React.FC<ControlsProps> = ({ scene, camera, engine, splatMesh, onResetView }) => {
    const [background, setBackgroundState] = useState<string>("#1a1a1a");
    const [autoRotate, setAutoRotate] = useState<boolean>(false);

    const BABYLON = (window as any).BABYLON;

    /**
     * Compute a world-space bounding box for the loaded splat mesh.
     * Returns null if the mesh or its bounding info isn't available yet.
     */
    const getSceneBoundingInfo = useCallback(() => {
        if (!splatMesh || !BABYLON) return null;
        try {
            const meshes = Array.isArray(splatMesh) ? splatMesh : [splatMesh];
            let min: any = null;
            let max: any = null;
            meshes.forEach((m: any) => {
                if (!m?.getBoundingInfo) return;
                const bbox = m.getBoundingInfo().boundingBox;
                if (!min) {
                    min = bbox.minimumWorld.clone();
                    max = bbox.maximumWorld.clone();
                } else {
                    min = BABYLON.Vector3.Minimize(min, bbox.minimumWorld);
                    max = BABYLON.Vector3.Maximize(max, bbox.maximumWorld);
                }
            });
            if (!min || !max) return null;
            const center = min.add(max).scale(0.5);
            const size = max.subtract(min);
            return { min, max, center, size, maxSize: Math.max(size.x, size.y, size.z) };
        } catch (error) {
            console.error("Error computing splat bounding info:", error);
            return null;
        }
    }, [splatMesh, BABYLON]);

    /**
     * Position the camera using a named view preset relative to the splat
     * scene bounding box. Maintains the current radius if no bounds are
     * available, so the preset still produces a sensible angle.
     */
    const setCameraView = useCallback(
        (view: string) => {
            if (!camera || !BABYLON) return;
            try {
                const bounds = getSceneBoundingInfo();
                if (bounds) {
                    camera.setTarget(bounds.center.clone());
                    camera.radius = Math.max(bounds.maxSize * 1.8, 0.5);
                }
                // ArcRotateCamera uses (alpha, beta) spherical coordinates.
                switch (view) {
                    case "top":
                        camera.alpha = -Math.PI / 2;
                        camera.beta = 0.01; // straight down (avoid gimbal lock at 0)
                        break;
                    case "front":
                        camera.alpha = -Math.PI / 2;
                        camera.beta = Math.PI / 2;
                        break;
                    case "side":
                        camera.alpha = 0;
                        camera.beta = Math.PI / 2;
                        break;
                    case "isometric":
                        camera.alpha = -Math.PI / 4;
                        camera.beta = Math.PI / 3;
                        break;
                }
                console.log(`BabylonJS Splat: Camera set to ${view} view`);
            } catch (error) {
                console.error(`Error setting camera to ${view} view:`, error);
            }
        },
        [camera, BABYLON, getSceneBoundingInfo]
    );

    /**
     * Frame the camera so the loaded splat fills the viewport with a
     * comfortable margin. Called by the F shortcut and the Fit button.
     */
    const fitToScene = useCallback(() => {
        if (!camera) return;
        try {
            const bounds = getSceneBoundingInfo();
            if (!bounds) {
                console.warn("BabylonJS Splat: No bounds available for fit-to-scene");
                return;
            }
            camera.setTarget(bounds.center.clone());
            const radius = Math.max(bounds.maxSize * 1.8, 0.5);
            camera.radius = radius;
            camera.lowerRadiusLimit = radius * 0.0001;
            camera.minZ = radius * 0.0001;
            camera.maxZ = radius * 200;
            console.log("BabylonJS Splat: Camera fitted to scene");
        } catch (error) {
            console.error("Error fitting camera:", error);
        }
    }, [camera, getSceneBoundingInfo]);

    /**
     * Multiplicatively adjust ArcRotateCamera radius. direction > 0 zooms
     * in (smaller radius), direction < 0 zooms out.
     */
    const zoomCamera = useCallback(
        (direction: number) => {
            if (!camera) return;
            try {
                const factor = direction > 0 ? 0.9 : 1.1;
                camera.radius = Math.max(camera.lowerRadiusLimit ?? 0.0001, camera.radius * factor);
                console.log(`BabylonJS Splat: Zoomed ${direction > 0 ? "in" : "out"}`);
            } catch (error) {
                console.error("Error zooming camera:", error);
            }
        },
        [camera]
    );

    /**
     * Update the BabylonJS scene clear color from a CSS hex string.
     */
    const changeBackground = useCallback(
        (color: string) => {
            if (!scene || !BABYLON) return;
            try {
                setBackgroundState(color);
                const c = BABYLON.Color3.FromHexString(color);
                scene.clearColor = new BABYLON.Color4(c.r, c.g, c.b, 1.0);
                console.log(`BabylonJS Splat: Background changed to ${color}`);
            } catch (error) {
                console.error("Error changing background:", error);
            }
        },
        [scene, BABYLON]
    );

    /**
     * Toggle ArcRotateCamera's built-in auto-rotation behavior. The
     * AutoRotationBehavior is created lazily on the camera instance.
     */
    const toggleAutoRotate = useCallback(() => {
        if (!camera) return;
        try {
            const next = !autoRotate;
            setAutoRotate(next);
            if (typeof camera.useAutoRotationBehavior !== "undefined") {
                camera.useAutoRotationBehavior = next;
                if (next && camera.autoRotationBehavior) {
                    camera.autoRotationBehavior.idleRotationSpeed = 0.2;
                    camera.autoRotationBehavior.idleRotationWaitTime = 0;
                    camera.autoRotationBehavior.idleRotationSpinupTime = 0;
                }
            }
            console.log(`BabylonJS Splat: Auto-rotate ${next ? "enabled" : "disabled"}`);
        } catch (error) {
            console.error("Error toggling auto-rotate:", error);
        }
    }, [camera, autoRotate]);

    /**
     * Reset all viewer controls and camera framing to defaults. Mirrors
     * the ThreeJS "Reset Scene" action.
     */
    const resetScene = useCallback(() => {
        try {
            changeBackground("#1a1a1a");
            if (autoRotate) toggleAutoRotate();
            if (onResetView) {
                onResetView();
            } else {
                fitToScene();
            }
            console.log("BabylonJS Splat: Scene reset to defaults");
        } catch (error) {
            console.error("Error resetting scene:", error);
        }
    }, [changeBackground, autoRotate, toggleAutoRotate, onResetView, fitToScene]);

    if (!scene || !camera || !engine) {
        return null;
    }

    return (
        <>
            {/* Camera Views */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Camera Views
                </h5>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
                    {["top", "front", "side", "isometric"].map((view) => (
                        <button
                            key={view}
                            onClick={() => setCameraView(view)}
                            style={{
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px 8px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                                textTransform: "capitalize",
                            }}
                            title={`${view} view`}
                        >
                            {view}
                        </button>
                    ))}
                </div>
            </div>

            {/* Quick Actions */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Quick Actions
                </h5>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <button
                        onClick={fitToScene}
                        style={{
                            background: "#2196F3",
                            border: "none",
                            color: "white",
                            padding: "8px 12px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                        }}
                        title="Fit to scene (F)"
                    >
                        🎯 Fit to Scene
                    </button>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
                        <button
                            onClick={() => zoomCamera(1)}
                            style={{
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                            }}
                        >
                            🔍 Zoom In
                        </button>
                        <button
                            onClick={() => zoomCamera(-1)}
                            style={{
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                            }}
                        >
                            🔍 Zoom Out
                        </button>
                    </div>
                </div>
            </div>

            {/* Camera Behavior */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Camera Behavior
                </h5>
                <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                    <input
                        type="checkbox"
                        checked={autoRotate}
                        onChange={toggleAutoRotate}
                        style={{ marginRight: "8px" }}
                    />
                    <span>Auto-Rotate</span>
                </label>
                <div style={{ fontSize: "0.7em", color: "#999", marginTop: "4px" }}>
                    {autoRotate ? "Camera is orbiting the splat" : "Camera idle"}
                </div>
            </div>

            {/* Background */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Background
                </h5>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                    {[
                        { label: "Dark", value: "#1a1a1a" },
                        { label: "Black", value: "#000000" },
                        { label: "White", value: "#ffffff" },
                    ].map((bg) => (
                        <button
                            key={bg.value}
                            onClick={() => changeBackground(bg.value)}
                            style={{
                                background:
                                    background === bg.value
                                        ? "#4CAF50"
                                        : "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px 4px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.75em",
                            }}
                        >
                            {bg.label}
                        </button>
                    ))}
                </div>
                <input
                    type="color"
                    value={background}
                    onChange={(e) => changeBackground(e.target.value)}
                    style={{
                        width: "100%",
                        marginTop: "6px",
                        height: "26px",
                        background: "transparent",
                        border: "1px solid rgba(255, 255, 255, 0.2)",
                        borderRadius: "4px",
                        cursor: "pointer",
                    }}
                    title="Custom background color"
                />
            </div>

            {/* Actions */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>Actions</h5>
                <button
                    onClick={resetScene}
                    style={{
                        width: "100%",
                        background: "#FF9800",
                        border: "none",
                        color: "white",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.8em",
                    }}
                    title="Reset everything to initial state"
                >
                    🔄 Reset Scene
                </button>
                <div
                    style={{
                        fontSize: "0.7em",
                        color: "#999",
                        marginTop: "4px",
                        textAlign: "center",
                    }}
                >
                    Resets background, auto-rotate and camera framing
                </div>
            </div>

            {/* Keyboard Shortcuts Help */}
            <div
                style={{
                    fontSize: "0.75em",
                    color: "#999",
                    marginTop: "16px",
                    paddingTop: "12px",
                    borderTop: "1px solid rgba(255,255,255,0.1)",
                }}
            >
                <div style={{ fontWeight: "bold", marginBottom: "4px" }}>Keyboard Shortcuts:</div>
                <div>F: Fit scene</div>
                <div>Esc: Close panel</div>
            </div>

            {/* Mouse Controls Help */}
            <div
                style={{
                    fontSize: "0.75em",
                    color: "#999",
                    marginTop: "8px",
                    paddingTop: "8px",
                    borderTop: "1px solid rgba(255,255,255,0.1)",
                }}
            >
                <div style={{ fontWeight: "bold", marginBottom: "4px" }}>Mouse Controls:</div>
                <div>Left drag: Rotate</div>
                <div>Right drag: Pan</div>
                <div>Wheel: Zoom</div>
            </div>
        </>
    );
};

export default Controls;
