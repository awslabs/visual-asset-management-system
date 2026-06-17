/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";

interface RenderSettingsProps {
    /** PlayCanvas Application instance. */
    app: any;
    /** PlayCanvas camera Entity. */
    camera: any;
    /** PlayCanvas Entity holding the GSplat component (or null until loaded). */
    splatEntity: any;
}

/**
 * Render Settings tab for the PlayCanvas Gaussian Splat viewer panel.
 *
 * Surfaces splat-relevant rendering properties exposed by the PlayCanvas
 * gsplat component and camera: per-Gaussian scale, mesh visibility, world
 * transform reset, camera FOV and the application's max pixel ratio.
 *
 * Replaces the ThreeJS "Material Library" tab — Gaussian Splats encode
 * color and opacity per-Gaussian and don't have an enumerable material
 * list to edit, so we expose the global render parameters that *do* exist
 * for splats instead.
 */
const RenderSettings: React.FC<RenderSettingsProps> = ({ app, camera, splatEntity }) => {
    const [splatScale, setSplatScale] = useState<number>(1.0);
    const [pointCloudMode, setPointCloudMode] = useState<boolean>(false);
    const [splatVisible, setSplatVisible] = useState<boolean>(true);
    const [fov, setFov] = useState<number>(camera?.camera?.fov ?? 75);
    const [pixelRatio, setPixelRatio] = useState<number>(
        app?.graphicsDevice?.maxPixelRatio ?? window.devicePixelRatio ?? 1
    );
    const [flipped, setFlipped] = useState<{ x: boolean; y: boolean; z: boolean }>({
        x: false,
        y: false,
        z: false,
    });

    const pc = (window as any).pc;

    // Sync local state to actual values whenever the splatEntity swaps in.
    useEffect(() => {
        if (splatEntity) {
            if (typeof splatEntity.enabled === "boolean") {
                setSplatVisible(splatEntity.enabled);
            }
            // PlayCanvas gsplat component exposes a uniform via
            // material.setParameter("splatScale"). We can't read it back
            // reliably across versions, so we leave the slider at its
            // last user-set value.
        }
    }, [splatEntity]);

    /**
     * Adjust the per-Gaussian splat scale. Higher values render larger
     * Gaussians (softer/blurrier), smaller values produce a crisper
     * point-like appearance.
     *
     * PlayCanvas's GSplat material is driven through shader uniforms;
     * we attempt the standard PlayCanvas surface but fall back gracefully
     * if a particular engine build doesn't expose the property.
     */
    const updateSplatScale = useCallback(
        (value: number) => {
            setSplatScale(value);
            if (!splatEntity?.gsplat) return;
            try {
                const inst = splatEntity.gsplat.instance;
                // Direct property (newer engine builds)
                if (inst && typeof inst.splatSize !== "undefined") {
                    inst.splatSize = value;
                }
                if (inst && typeof inst.splatScale !== "undefined") {
                    inst.splatScale = value;
                }
                // Uniform path (older builds expose material.setParameter)
                const material = inst?.material;
                if (material?.setParameter) {
                    material.setParameter("splatScale", value);
                    material.setParameter("splatSize", value);
                }
                console.log(`PlayCanvas Splat: Splat scale = ${value}`);
            } catch (error) {
                console.error("Error updating splat scale:", error);
            }
        },
        [splatEntity]
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
     * Toggle the splat entity's `enabled` flag. PlayCanvas treats this
     * as scene-graph visibility — the entity stops rendering and
     * receiving updates.
     */
    const toggleVisibility = useCallback(() => {
        if (!splatEntity) return;
        try {
            const next = !splatVisible;
            setSplatVisible(next);
            splatEntity.enabled = next;
            console.log(`PlayCanvas Splat: Visibility = ${next}`);
        } catch (error) {
            console.error("Error toggling visibility:", error);
        }
    }, [splatEntity, splatVisible]);

    /**
     * Reset the splat entity's local transform back to identity, in
     * case the user has nudged it elsewhere.
     */
    const resetTransform = useCallback(() => {
        if (!splatEntity || !pc) return;
        try {
            splatEntity.setLocalPosition(0, 0, 0);
            splatEntity.setLocalEulerAngles(0, 0, 0);
            splatEntity.setLocalScale(1, 1, 1);
            setFlipped({ x: false, y: false, z: false });
            console.log("PlayCanvas Splat: Transform reset");
        } catch (error) {
            console.error("Error resetting transform:", error);
        }
    }, [splatEntity, pc]);

    /**
     * Flip the splat along the named axis by negating that component of
     * the entity's local scale. Re-applying toggles the flip back off.
     *
     * Some splats (notably ones generated from photogrammetry pipelines
     * with differing world conventions) load upside-down or mirrored;
     * the orbit camera's pitch limits then prevent the user from
     * rotating to a corrected view. Flipping the entity sidesteps this
     * entirely.
     */
    const toggleFlip = useCallback(
        (axis: "x" | "y" | "z") => {
            if (!splatEntity) return;
            try {
                const scale = splatEntity.getLocalScale();
                const sx = axis === "x" ? -scale.x : scale.x;
                const sy = axis === "y" ? -scale.y : scale.y;
                const sz = axis === "z" ? -scale.z : scale.z;
                splatEntity.setLocalScale(sx, sy, sz);
                setFlipped((prev) => ({ ...prev, [axis]: !prev[axis] }));
                console.log(`PlayCanvas Splat: Flipped ${axis.toUpperCase()} axis`);
            } catch (error) {
                console.error(`Error flipping ${axis} axis:`, error);
            }
        },
        [splatEntity]
    );

    /**
     * Update the camera vertical field of view. PlayCanvas stores FOV
     * directly in degrees on the camera component.
     */
    const updateFov = useCallback(
        (degrees: number) => {
            if (!camera?.camera) return;
            try {
                camera.camera.fov = degrees;
                setFov(degrees);
                console.log(`PlayCanvas Splat: FOV = ${degrees}°`);
            } catch (error) {
                console.error("Error updating FOV:", error);
            }
        },
        [camera]
    );

    /**
     * Adjust the application's max pixel ratio. Higher values render at
     * higher resolution (sharper, slower); lower values reduce pixel
     * count for performance.
     */
    const updatePixelRatio = useCallback(
        (value: number) => {
            if (!app?.graphicsDevice) return;
            try {
                setPixelRatio(value);
                app.graphicsDevice.maxPixelRatio = value;
                // Trigger a resize so the change takes effect immediately.
                if (typeof app.resizeCanvas === "function") {
                    app.resizeCanvas();
                }
                console.log(`PlayCanvas Splat: Pixel ratio = ${value}`);
            } catch (error) {
                console.error("Error updating pixel ratio:", error);
            }
        },
        [app]
    );

    if (!app || !camera) {
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

                <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                    <input
                        type="checkbox"
                        checked={splatVisible}
                        onChange={toggleVisibility}
                        style={{ marginRight: "8px" }}
                    />
                    <span>Splat Visible</span>
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
                        Field of View: {fov.toFixed(0)}°
                    </label>
                    <input
                        type="range"
                        min="20"
                        max="120"
                        step="1"
                        value={fov}
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
                        Pixel Ratio: {pixelRatio.toFixed(2)}×
                    </label>
                    <input
                        type="range"
                        min="0.5"
                        max="3"
                        step="0.1"
                        value={pixelRatio}
                        onChange={(e) => updatePixelRatio(parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                    <div style={{ fontSize: "0.7em", color: "#999", marginTop: "2px" }}>
                        Higher = sharper; lower = faster
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
                    title="Reset splat entity local transform"
                >
                    🔄 Reset Splat Transform
                </button>
            </div>
        </div>
    );
};

export default RenderSettings;
