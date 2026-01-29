/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useState } from "react";

interface ControlsProps {
    scene: any;
    camera: any;
    renderer: any;
    threeRoot: any;
    controls: any;
    onClose?: () => void;
    onClearSelection?: () => void;
    onResetAllTransforms?: () => void;
    onResetAllMaterials?: () => void;
    // Control props
    enable3DSelection: boolean;
    onToggle3DSelection: (enabled: boolean) => void;
    // Animation props
    animations?: any[];
    animationPaused?: boolean;
    onToggleAnimation?: (paused: boolean) => void;
}

const Controls: React.FC<ControlsProps> = ({
    scene,
    camera,
    renderer,
    threeRoot,
    controls,
    onClose,
    onClearSelection,
    onResetAllTransforms,
    onResetAllMaterials,
    enable3DSelection,
    onToggle3DSelection,
    animations,
    animationPaused,
    onToggleAnimation,
}) => {
    const [background, setBackgroundState] = useState<string>("#333333");
    const [ambientIntensity, setAmbientIntensity] = useState<number>(0.5);
    const [directionalIntensity, setDirectionalIntensity] = useState<number>(0.8);
    const [wireframe, setWireframe] = useState<boolean>(false);

    const THREE = (window as any).THREE;

    // Camera view presets
    const setCameraView = useCallback(
        (view: string) => {
            if (!camera || !threeRoot || !controls) return;

            try {
                // Calculate bounding box for all file groups
                const fileGroups = Array.isArray(threeRoot) ? threeRoot : [threeRoot];
                const box = new THREE.Box3();
                fileGroups.forEach((group: any) => {
                    const groupBox = new THREE.Box3().setFromObject(group);
                    box.union(groupBox);
                });

                const size = box.getSize(new THREE.Vector3());
                const center = box.getCenter(new THREE.Vector3());

                const maxSize = Math.max(size.x, size.y, size.z);
                const distance = maxSize * 2;

                // Set camera position based on view
                switch (view) {
                    case "top":
                        camera.position.set(center.x, center.y + distance, center.z);
                        break;
                    case "front":
                        camera.position.set(center.x, center.y, center.z + distance);
                        break;
                    case "side":
                        camera.position.set(center.x + distance, center.y, center.z);
                        break;
                    case "isometric":
                        camera.position.set(
                            center.x + distance * 0.7,
                            center.y + distance * 0.7,
                            center.z + distance * 0.7
                        );
                        break;
                }

                camera.lookAt(center);
                controls.setTarget(center.x, center.y, center.z);
                camera.updateProjectionMatrix();

                console.log(`Camera set to ${view} view`);
            } catch (error) {
                console.error(`Error setting camera to ${view} view:`, error);
            }
        },
        [camera, threeRoot, controls, THREE]
    );

    // Fit camera to scene
    const fitToScene = useCallback(() => {
        if (!camera || !threeRoot || !controls) return;

        try {
            // Calculate bounding box for all file groups
            const fileGroups = Array.isArray(threeRoot) ? threeRoot : [threeRoot];
            const box = new THREE.Box3();
            fileGroups.forEach((group: any) => {
                const groupBox = new THREE.Box3().setFromObject(group);
                box.union(groupBox);
            });

            const size = box.getSize(new THREE.Vector3());
            const center = box.getCenter(new THREE.Vector3());

            const maxSize = Math.max(size.x, size.y, size.z);
            const fitHeightDistance = maxSize / (2 * Math.tan((Math.PI * camera.fov) / 360));
            const fitWidthDistance = fitHeightDistance / camera.aspect;
            const distance = 1.5 * Math.max(fitHeightDistance, fitWidthDistance);

            camera.position.set(
                center.x + distance * 0.5,
                center.y + distance * 0.5,
                center.z + distance * 0.5
            );
            camera.lookAt(center);
            controls.setTarget(center.x, center.y, center.z);

            camera.near = distance / 100;
            camera.far = distance * 100;
            camera.updateProjectionMatrix();

            console.log("Camera fitted to scene");
        } catch (error) {
            console.error("Error fitting camera:", error);
        }
    }, [camera, threeRoot, controls, THREE]);

    // Zoom camera
    const zoomCamera = useCallback(
        (direction: number) => {
            if (!camera || !controls) return;

            try {
                const target = controls.getTarget();
                const offset = camera.position.clone().sub(target);
                const distance = offset.length();

                const newDistance = distance * (direction > 0 ? 0.9 : 1.1);
                offset.normalize().multiplyScalar(newDistance);

                camera.position.copy(target).add(offset);
                console.log(`Zoomed ${direction > 0 ? "in" : "out"}`);
            } catch (error) {
                console.error("Error zooming camera:", error);
            }
        },
        [camera, controls]
    );

    // Change background
    const changeBackground = useCallback(
        (color: string) => {
            if (!scene) return;

            try {
                setBackgroundState(color);
                scene.background = new THREE.Color(color);
                console.log(`Background changed to ${color}`);
            } catch (error) {
                console.error("Error changing background:", error);
            }
        },
        [scene, THREE]
    );

    // Update ambient light
    const updateAmbientLight = useCallback(
        (intensity: number) => {
            if (!scene) return;

            try {
                setAmbientIntensity(intensity);
                scene.traverse((obj: any) => {
                    if (obj.type === "AmbientLight") {
                        obj.intensity = intensity;
                    }
                });
                console.log(`Ambient light intensity: ${intensity}`);
            } catch (error) {
                console.error("Error updating ambient light:", error);
            }
        },
        [scene]
    );

    // Update directional light
    const updateDirectionalLight = useCallback(
        (intensity: number) => {
            if (!scene) return;

            try {
                setDirectionalIntensity(intensity);
                scene.traverse((obj: any) => {
                    if (obj.type === "DirectionalLight") {
                        obj.intensity = intensity;
                    }
                });
                console.log(`Directional light intensity: ${intensity}`);
            } catch (error) {
                console.error("Error updating directional light:", error);
            }
        },
        [scene]
    );

    // Toggle wireframe
    const toggleWireframe = useCallback(() => {
        if (!threeRoot) return;

        try {
            const newState = !wireframe;
            setWireframe(newState);

            // Handle both single group and array of groups
            const fileGroups = Array.isArray(threeRoot) ? threeRoot : [threeRoot];

            fileGroups.forEach((group: any) => {
                group.traverse((obj: any) => {
                    if (obj.material) {
                        if (Array.isArray(obj.material)) {
                            obj.material.forEach((mat: any) => {
                                mat.wireframe = newState;
                            });
                        } else {
                            obj.material.wireframe = newState;
                        }
                    }
                });
            });

            console.log(`Wireframe ${newState ? "enabled" : "disabled"}`);
        } catch (error) {
            console.error("Error toggling wireframe:", error);
        }
    }, [threeRoot, wireframe]);

    // Reset scene
    const resetScene = useCallback(() => {
        try {
            // Clear all selections
            if (onClearSelection) {
                onClearSelection();
            }

            // Reset all object transforms
            if (onResetAllTransforms) {
                onResetAllTransforms();
            }

            // Reset all object materials
            if (onResetAllMaterials) {
                onResetAllMaterials();
            }

            // Reset visual settings
            changeBackground("#333333");
            updateAmbientLight(0.5);
            updateDirectionalLight(0.8);
            if (wireframe) toggleWireframe();

            // Fit camera
            fitToScene();

            console.log("Scene reset to defaults");
        } catch (error) {
            console.error("Error resetting scene:", error);
        }
    }, [
        changeBackground,
        updateAmbientLight,
        updateDirectionalLight,
        wireframe,
        toggleWireframe,
        fitToScene,
        onClearSelection,
        onResetAllTransforms,
        onResetAllMaterials,
    ]);

    if (!scene || !camera || !renderer) {
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

            {/* Viewer Controls */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Viewer Controls
                </h5>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                        <input
                            type="checkbox"
                            checked={enable3DSelection}
                            onChange={(e) => onToggle3DSelection(e.target.checked)}
                            style={{ marginRight: "8px" }}
                        />
                        <span>Enable 3D Selection</span>
                    </label>
                </div>
                <div style={{ fontSize: "0.7em", color: "#999", marginTop: "4px" }}>
                    {enable3DSelection
                        ? "Click objects in 3D view to select"
                        : "3D selection disabled"}
                </div>
            </div>

            {/* Animation Controls */}
            {animations && animations.length > 0 && onToggleAnimation && (
                <div style={{ marginBottom: "16px" }}>
                    <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                        Animation
                    </h5>
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                        <button
                            onClick={() => onToggleAnimation(!animationPaused)}
                            style={{
                                background: animationPaused ? "#4CAF50" : "#FF9800",
                                border: "none",
                                color: "white",
                                padding: "8px 12px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                                fontWeight: "bold",
                            }}
                            title={animationPaused ? "Play animation" : "Pause animation"}
                        >
                            {animationPaused ? "▶️ Play Animation" : "⏸️ Pause Animation"}
                        </button>
                    </div>
                    <div style={{ fontSize: "0.7em", color: "#999", marginTop: "4px" }}>
                        {animations.length} animation{animations.length !== 1 ? "s" : ""} available
                    </div>
                </div>
            )}

            {/* Background */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Background
                </h5>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                    {[
                        { label: "Dark", value: "#333333" },
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
            </div>

            {/* Lighting */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>Lighting</h5>
                <div style={{ marginBottom: "8px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Ambient: {ambientIntensity.toFixed(2)}
                    </label>
                    <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        value={ambientIntensity}
                        onChange={(e) => updateAmbientLight(parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                </div>
                <div style={{ marginBottom: "8px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Directional: {directionalIntensity.toFixed(2)}
                    </label>
                    <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        value={directionalIntensity}
                        onChange={(e) => updateDirectionalLight(parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                </div>
            </div>

            {/* Visual Options */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Visual Options
                </h5>
                <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                    <input
                        type="checkbox"
                        checked={wireframe}
                        onChange={toggleWireframe}
                        style={{ marginRight: "8px" }}
                    />
                    Wireframe Mode
                </label>
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
                    Resets transforms, materials, selections, and camera
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
