/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useRef, useState } from "react";

interface ControlsProps {
    /** PlayCanvas Application instance. */
    app: any;
    /** PlayCanvas camera Entity. */
    camera: any;
    /** PlayCanvas Entity holding the GSplat component (or null until loaded). */
    splatEntity: any;
    /** Camera-controls handle returned by `createCameraControls` in the parent. */
    cameraControls: any;
    /** Optional close handler for the parent panel. */
    onClose?: () => void;
    /** Reset camera to its initial framing computed at load time. */
    onResetView?: () => void;
}

/**
 * Controls tab for the PlayCanvas Gaussian Splat viewer panel.
 *
 * Provides camera-view presets (top/front/side/isometric), fit-to-scene,
 * zoom buttons, background color picker, auto-rotate toggle and a
 * "Reset Scene" action that returns the viewer to its initial framing.
 *
 * Mirrors the API and styling of the ThreeJS / BabylonJS viewer Controls
 * components but operates on PlayCanvas APIs (pc.Application, camera
 * Entity with `camera` component, custom orbit cameraControls handle).
 * Adapts gracefully for Gaussian Splat scenes (no lighting controls,
 * no global wireframe toggle — splats are point clouds).
 */
const Controls: React.FC<ControlsProps> = ({
    app,
    camera,
    splatEntity,
    cameraControls,
    onResetView,
}) => {
    const [background, setBackgroundState] = useState<string>("#1a1a1a");
    const [autoRotate, setAutoRotate] = useState<boolean>(false);
    const autoRotateAngleRef = useRef<number>(0);
    const autoRotateRafRef = useRef<number | null>(null);

    const pc = (window as any).pc;

    /**
     * Compute a world-space bounding box for the loaded splat entity.
     * Returns null if the entity or its gsplat AABB isn't available yet.
     */
    const getSceneBoundingInfo = useCallback(() => {
        if (!splatEntity || !pc) return null;
        try {
            const aabb = splatEntity.gsplat?.instance?.meshInstance?.aabb;
            if (!aabb) return null;
            const center = aabb.center;
            const halfExtents = aabb.halfExtents;
            const radius = halfExtents.length();
            const maxSize = Math.max(halfExtents.x, halfExtents.y, halfExtents.z) * 2;
            return { center, halfExtents, radius, maxSize };
        } catch (error) {
            console.error("Error computing splat bounding info:", error);
            return null;
        }
    }, [splatEntity, pc]);

    /**
     * Position the camera using a named view preset relative to the splat
     * scene bounding box. Uses the parent's cameraControls.setTarget so the
     * orbit yaw/pitch state stays in sync with the new view.
     */
    const setCameraView = useCallback(
        (view: string) => {
            if (!camera || !pc || !cameraControls?.setTarget) return;
            try {
                const bounds = getSceneBoundingInfo();
                const center = bounds ? bounds.center : new pc.Vec3(0, 0, 0);
                const distance = bounds ? Math.max(bounds.radius * 2.5, 1) : 5;

                // Position the camera and let setTarget recompute orbit state.
                switch (view) {
                    case "top":
                        camera.setPosition(center.x, center.y + distance, center.z);
                        break;
                    case "front":
                        camera.setPosition(center.x, center.y, center.z + distance);
                        break;
                    case "side":
                        camera.setPosition(center.x + distance, center.y, center.z);
                        break;
                    case "isometric":
                        camera.setPosition(
                            center.x + distance * 0.6,
                            center.y + distance * 0.6,
                            center.z + distance * 0.6
                        );
                        break;
                }
                camera.lookAt(center.x, center.y, center.z);
                cameraControls.setTarget(center.x, center.y, center.z, distance);
                console.log(`PlayCanvas Splat: Camera set to ${view} view`);
            } catch (error) {
                console.error(`Error setting camera to ${view} view:`, error);
            }
        },
        [camera, pc, cameraControls, getSceneBoundingInfo]
    );

    /**
     * Frame the camera so the loaded splat fills the viewport with a
     * comfortable margin. Bound to the F shortcut and the Fit button.
     */
    const fitToScene = useCallback(() => {
        if (!camera || !cameraControls?.setTarget) return;
        try {
            const bounds = getSceneBoundingInfo();
            if (!bounds) {
                console.warn("PlayCanvas Splat: No bounds available for fit-to-scene");
                return;
            }
            const distance = Math.max(bounds.radius * 1.8, 0.5);
            cameraControls.setTarget(bounds.center.x, bounds.center.y, bounds.center.z, distance);
            // Adjust camera near/far to scene scale.
            if (camera.camera) {
                camera.camera.farClip = Math.max(bounds.radius * 20, 1000);
                camera.camera.nearClip = Math.min(bounds.radius * 0.001, 0.01);
            }
            console.log("PlayCanvas Splat: Camera fitted to scene");
        } catch (error) {
            console.error("Error fitting camera:", error);
        }
    }, [camera, cameraControls, getSceneBoundingInfo]);

    /**
     * Multiplicatively adjust orbit-camera distance. direction > 0 zooms
     * in (smaller distance), direction < 0 zooms out. PlayCanvas uses a
     * separate orbit-distance state held by the parent's cameraControls,
     * so we read the current camera-to-target distance and re-apply it.
     */
    const zoomCamera = useCallback(
        (direction: number) => {
            if (!camera || !cameraControls?.setTarget) return;
            try {
                const bounds = getSceneBoundingInfo();
                const targetVec = bounds?.center ?? new pc.Vec3(0, 0, 0);
                const camPos = camera.getPosition();
                const dx = camPos.x - targetVec.x;
                const dy = camPos.y - targetVec.y;
                const dz = camPos.z - targetVec.z;
                const currentDistance = Math.sqrt(dx * dx + dy * dy + dz * dz);
                const factor = direction > 0 ? 0.9 : 1.1;
                cameraControls.setTarget(
                    targetVec.x,
                    targetVec.y,
                    targetVec.z,
                    Math.max(0.1, currentDistance * factor)
                );
                console.log(`PlayCanvas Splat: Zoomed ${direction > 0 ? "in" : "out"}`);
            } catch (error) {
                console.error("Error zooming camera:", error);
            }
        },
        [camera, cameraControls, pc, getSceneBoundingInfo]
    );

    /**
     * Update the camera component's clear color from a CSS hex string.
     */
    const changeBackground = useCallback(
        (color: string) => {
            if (!camera?.camera || !pc) return;
            try {
                setBackgroundState(color);
                // CSS hex → 0..1 RGB.
                const hex = color.replace("#", "");
                const r = parseInt(hex.substr(0, 2), 16) / 255;
                const g = parseInt(hex.substr(2, 2), 16) / 255;
                const b = parseInt(hex.substr(4, 2), 16) / 255;
                camera.camera.clearColor = new pc.Color(r, g, b, 1.0);
                console.log(`PlayCanvas Splat: Background changed to ${color}`);
            } catch (error) {
                console.error("Error changing background:", error);
            }
        },
        [camera, pc]
    );

    /**
     * Toggle a manually-driven auto-rotate loop. PlayCanvas doesn't ship
     * an equivalent of BabylonJS's AutoRotationBehavior for an orbit
     * camera, so we drive it from a requestAnimationFrame loop, orbiting
     * around the current splat bounding-box center.
     */
    const toggleAutoRotate = useCallback(() => {
        if (!camera || !cameraControls?.setTarget) return;
        const next = !autoRotate;
        setAutoRotate(next);

        const stopLoop = () => {
            if (autoRotateRafRef.current !== null) {
                cancelAnimationFrame(autoRotateRafRef.current);
                autoRotateRafRef.current = null;
            }
        };

        stopLoop();

        if (!next) {
            console.log("PlayCanvas Splat: Auto-rotate disabled");
            return;
        }

        const bounds = getSceneBoundingInfo();
        const targetVec = bounds?.center ?? (pc ? new pc.Vec3(0, 0, 0) : null);
        if (!targetVec) return;

        const camPos = camera.getPosition();
        const dx = camPos.x - targetVec.x;
        const dz = camPos.z - targetVec.z;
        const currentY = camPos.y - targetVec.y;
        const horizontalDistance = Math.sqrt(dx * dx + dz * dz);
        autoRotateAngleRef.current = Math.atan2(dx, dz);

        const startTime = performance.now();
        const tick = () => {
            const elapsed = (performance.now() - startTime) / 1000;
            const angle = autoRotateAngleRef.current + elapsed * 0.5; // ~0.5 rad/s
            const x = targetVec.x + horizontalDistance * Math.sin(angle);
            const z = targetVec.z + horizontalDistance * Math.cos(angle);
            const y = targetVec.y + currentY;
            camera.setPosition(x, y, z);
            camera.lookAt(targetVec.x, targetVec.y, targetVec.z);
            autoRotateRafRef.current = requestAnimationFrame(tick);
        };
        autoRotateRafRef.current = requestAnimationFrame(tick);
        console.log("PlayCanvas Splat: Auto-rotate enabled");
    }, [autoRotate, camera, cameraControls, getSceneBoundingInfo, pc]);

    // Stop the auto-rotate RAF when the component unmounts.
    React.useEffect(() => {
        return () => {
            if (autoRotateRafRef.current !== null) {
                cancelAnimationFrame(autoRotateRafRef.current);
                autoRotateRafRef.current = null;
            }
        };
    }, []);

    /**
     * Reset all viewer controls and camera framing to defaults. Mirrors
     * the ThreeJS/BabylonJS "Reset Scene" action.
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
            console.log("PlayCanvas Splat: Scene reset to defaults");
        } catch (error) {
            console.error("Error resetting scene:", error);
        }
    }, [changeBackground, autoRotate, toggleAutoRotate, onResetView, fitToScene]);

    if (!app || !camera) {
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
