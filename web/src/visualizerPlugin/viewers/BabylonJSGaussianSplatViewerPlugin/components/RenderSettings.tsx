/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";

interface RenderSettingsProps {
    /** BabylonJS Scene instance. */
    scene: any;
    /** BabylonJS ArcRotateCamera instance — used for FOV control. */
    camera: any;
    /** BabylonJS Engine instance — used for hardware-scaling level. */
    engine: any;
    /** The loaded GaussianSplattingMesh (or first mesh of the imported file). */
    splatMesh: any;
}

/**
 * Render Settings tab for the BabylonJS Gaussian Splat viewer panel.
 *
 * Surfaces splat-relevant rendering properties that BabylonJS exposes
 * directly on the Gaussian Splat mesh and camera: splat scale, mesh
 * visibility, world transform reset, camera FOV, render-resolution
 * scaling, and a debug bounding-box toggle.
 *
 * Replaces the ThreeJS "Material Library" tab — Gaussian Splats encode
 * color and opacity per-Gaussian and don't have an enumerable material
 * list to edit, so we expose the global render parameters that *do* exist
 * for splats instead.
 */
const RenderSettings: React.FC<RenderSettingsProps> = ({ scene, camera, engine, splatMesh }) => {
    const [splatScale, setSplatScale] = useState<number>(1.0);
    const [pointCloudMode, setPointCloudMode] = useState<boolean>(false);
    const [showBoundingBox, setShowBoundingBox] = useState<boolean>(false);
    const [splatVisible, setSplatVisible] = useState<boolean>(true);
    const [fov, setFov] = useState<number>(camera?.fov ?? 0.8);
    const [hwScale, setHwScale] = useState<number>(1.0);
    const [flipped, setFlipped] = useState<{ x: boolean; y: boolean; z: boolean }>({
        x: false,
        y: false,
        z: false,
    });

    const BABYLON = (window as any).BABYLON;

    // Sync local state to the actual values whenever the splatMesh swaps in.
    useEffect(() => {
        if (splatMesh) {
            if (typeof splatMesh.isVisible === "boolean") setSplatVisible(splatMesh.isVisible);
            if (typeof splatMesh.showBoundingBox === "boolean") {
                setShowBoundingBox(splatMesh.showBoundingBox);
            }
            // GaussianSplattingMesh exposes either a `splatScale` property
            // or a uniform on the underlying material. Read whichever
            // exists so the slider matches the real value.
            if (typeof splatMesh.splatScale === "number") {
                setSplatScale(splatMesh.splatScale);
            }
        }
    }, [splatMesh]);

    /**
     * Adjust the per-Gaussian splat scale. Higher values render larger
     * Gaussians (softer/blurrier), smaller values produce a crisper
     * point-like appearance.
     */
    const updateSplatScale = useCallback(
        (value: number) => {
            setSplatScale(value);
            if (!splatMesh) return;
            try {
                if (typeof splatMesh.splatScale !== "undefined") {
                    splatMesh.splatScale = value;
                } else if (splatMesh.material?.setFloat) {
                    splatMesh.material.setFloat("splatScale", value);
                }
                console.log(`BabylonJS Splat: Splat scale = ${value}`);
            } catch (error) {
                console.error("Error updating splat scale:", error);
            }
        },
        [splatMesh]
    );

    /**
     * Toggle a "point cloud" preview by collapsing each Gaussian to a
     * very small scale. Restores the previous scale when disabled.
     */
    const togglePointCloud = useCallback(() => {
        const next = !pointCloudMode;
        setPointCloudMode(next);
        updateSplatScale(next ? 0.05 : 1.0);
    }, [pointCloudMode, updateSplatScale]);

    /**
     * Toggle visibility of the splat mesh (without unloading it).
     */
    const toggleVisibility = useCallback(() => {
        if (!splatMesh) return;
        try {
            const next = !splatVisible;
            setSplatVisible(next);
            splatMesh.isVisible = next;
            console.log(`BabylonJS Splat: Visibility = ${next}`);
        } catch (error) {
            console.error("Error toggling visibility:", error);
        }
    }, [splatMesh, splatVisible]);

    /**
     * Toggle BabylonJS's built-in bounding-box debug rendering on the
     * splat mesh.
     */
    const toggleBoundingBox = useCallback(() => {
        if (!splatMesh) return;
        try {
            const next = !showBoundingBox;
            setShowBoundingBox(next);
            splatMesh.showBoundingBox = next;
            console.log(`BabylonJS Splat: Bounding box = ${next}`);
        } catch (error) {
            console.error("Error toggling bounding box:", error);
        }
    }, [splatMesh, showBoundingBox]);

    /**
     * Reset the splat mesh's local transform (position/rotation/scale)
     * back to the identity, in case the user has nudged it elsewhere.
     */
    const resetTransform = useCallback(() => {
        if (!splatMesh || !BABYLON) return;
        try {
            splatMesh.position = BABYLON.Vector3.Zero();
            splatMesh.rotation = BABYLON.Vector3.Zero();
            splatMesh.scaling = new BABYLON.Vector3(1, 1, 1);
            setFlipped({ x: false, y: false, z: false });
            console.log("BabylonJS Splat: Transform reset");
        } catch (error) {
            console.error("Error resetting transform:", error);
        }
    }, [splatMesh, BABYLON]);

    /**
     * Flip the splat along the named axis by negating that component of
     * the mesh's scaling vector. Re-applying toggles the flip back off.
     *
     * Some splats (notably ones generated from photogrammetry pipelines
     * with differing world conventions) load upside-down or mirrored;
     * the camera's pitch limits then prevent the user from rotating to
     * a corrected view. Flipping the mesh sidesteps this entirely.
     */
    const toggleFlip = useCallback(
        (axis: "x" | "y" | "z") => {
            if (!splatMesh) return;
            try {
                if (!splatMesh.scaling) {
                    console.warn("BabylonJS Splat: Mesh has no scaling vector to flip");
                    return;
                }
                splatMesh.scaling[axis] = -splatMesh.scaling[axis];
                setFlipped((prev) => ({ ...prev, [axis]: !prev[axis] }));
                console.log(`BabylonJS Splat: Flipped ${axis.toUpperCase()} axis`);
            } catch (error) {
                console.error(`Error flipping ${axis} axis:`, error);
            }
        },
        [splatMesh]
    );

    /**
     * Update the camera vertical field of view. BabylonJS stores FOV in
     * radians; we expose degrees in the UI for clarity.
     */
    const updateFov = useCallback(
        (degrees: number) => {
            if (!camera) return;
            try {
                const radians = (degrees * Math.PI) / 180;
                camera.fov = radians;
                setFov(radians);
                console.log(`BabylonJS Splat: FOV = ${degrees}°`);
            } catch (error) {
                console.error("Error updating FOV:", error);
            }
        },
        [camera]
    );

    /**
     * Adjust the engine's hardware scaling level. Values < 1 render
     * at a higher resolution (sharper, slower); > 1 lowers resolution
     * for performance.
     */
    const updateHwScale = useCallback(
        (value: number) => {
            if (!engine) return;
            try {
                setHwScale(value);
                engine.setHardwareScalingLevel(value);
                console.log(`BabylonJS Splat: Hardware scaling = ${value}`);
            } catch (error) {
                console.error("Error updating hardware scaling:", error);
            }
        },
        [engine]
    );

    if (!scene || !camera || !engine) {
        return null;
    }

    return (
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
            {/* Splat Rendering */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Splat Rendering
                </h5>

                <div style={{ marginBottom: "10px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Splat Scale: {splatScale.toFixed(2)}
                    </label>
                    <input
                        type="range"
                        min="0.05"
                        max="3"
                        step="0.05"
                        value={splatScale}
                        onChange={(e) => updateSplatScale(parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                    <div style={{ fontSize: "0.7em", color: "#999", marginTop: "2px" }}>
                        Larger = softer Gaussians; smaller = sharper points
                    </div>
                </div>

                <label
                    style={{
                        display: "flex",
                        alignItems: "center",
                        cursor: "pointer",
                        marginBottom: "6px",
                    }}
                >
                    <input
                        type="checkbox"
                        checked={pointCloudMode}
                        onChange={togglePointCloud}
                        style={{ marginRight: "8px" }}
                    />
                    <span>Point Cloud Mode</span>
                </label>

                <label
                    style={{
                        display: "flex",
                        alignItems: "center",
                        cursor: "pointer",
                        marginBottom: "6px",
                    }}
                >
                    <input
                        type="checkbox"
                        checked={splatVisible}
                        onChange={toggleVisibility}
                        style={{ marginRight: "8px" }}
                    />
                    <span>Splat Visible</span>
                </label>

                <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                    <input
                        type="checkbox"
                        checked={showBoundingBox}
                        onChange={toggleBoundingBox}
                        style={{ marginRight: "8px" }}
                    />
                    <span>Show Bounding Box</span>
                </label>

                <div style={{ marginTop: "10px" }}>
                    <div style={{ fontSize: "0.8em", color: "#ccc", marginBottom: "4px" }}>
                        Flip Axis
                    </div>
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr 1fr",
                            gap: "4px",
                        }}
                    >
                        {(["x", "y", "z"] as const).map((axis) => (
                            <button
                                key={axis}
                                onClick={() => toggleFlip(axis)}
                                style={{
                                    background: flipped[axis]
                                        ? "#4CAF50"
                                        : "rgba(255, 255, 255, 0.1)",
                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                    color: "white",
                                    padding: "6px 4px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                    fontWeight: "bold",
                                }}
                                title={`Flip splat along ${axis.toUpperCase()} axis`}
                            >
                                Flip {axis.toUpperCase()}
                            </button>
                        ))}
                    </div>
                    <div style={{ fontSize: "0.7em", color: "#999", marginTop: "4px" }}>
                        Use if the splat loaded upside-down or mirrored
                    </div>
                </div>
            </div>

            {/* Camera Lens */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Camera Lens
                </h5>
                <div style={{ marginBottom: "8px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Field of View: {((fov * 180) / Math.PI).toFixed(0)}°
                    </label>
                    <input
                        type="range"
                        min="20"
                        max="120"
                        step="1"
                        value={(fov * 180) / Math.PI}
                        onChange={(e) => updateFov(parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                </div>
            </div>

            {/* Render Quality */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Render Quality
                </h5>
                <div style={{ marginBottom: "8px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Resolution Scale: {(1 / hwScale).toFixed(2)}×
                    </label>
                    <input
                        type="range"
                        min="0.5"
                        max="2"
                        step="0.1"
                        // Slider shows the user-facing factor (higher = sharper),
                        // but BabylonJS uses the inverse as its hardware-scaling level.
                        value={1 / hwScale}
                        onChange={(e) => updateHwScale(1 / parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                    <div style={{ fontSize: "0.7em", color: "#999", marginTop: "2px" }}>
                        &gt;1× sharper; &lt;1× faster
                    </div>
                </div>
            </div>

            {/* Actions */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>Actions</h5>
                <button
                    onClick={resetTransform}
                    style={{
                        width: "100%",
                        background: "rgba(255, 255, 255, 0.1)",
                        border: "1px solid rgba(255, 255, 255, 0.2)",
                        color: "white",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.8em",
                    }}
                    title="Reset splat mesh local transform"
                >
                    🔄 Reset Splat Transform
                </button>
            </div>
        </div>
    );
};

export default RenderSettings;
