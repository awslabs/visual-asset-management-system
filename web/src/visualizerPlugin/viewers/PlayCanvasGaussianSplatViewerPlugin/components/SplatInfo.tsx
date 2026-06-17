/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";

interface SplatInfoProps {
    /** PlayCanvas Application instance. */
    app: any;
    /** PlayCanvas camera Entity. */
    camera: any;
    /** PlayCanvas Entity holding the GSplat component (or null until loaded). */
    splatEntity: any;
    /** Source asset key/file name shown in the header. */
    fileName?: string;
}

/**
 * Splat Info tab for the PlayCanvas Gaussian Splat viewer panel.
 *
 * Displays read-only telemetry about the loaded scene: file name, splat
 * count, world-space bounding box, live camera state (position, target,
 * forward) and engine FPS. Refreshes on a 2 Hz timer so the camera and
 * FPS fields track live activity without forcing a full re-render of
 * the parent panel.
 *
 * This tab replaces the ThreeJS "Scene Graph" tab — Gaussian Splat
 * assets are flat (one renderable entity) so a hierarchy view does not
 * apply, but users still benefit from quick stats about what they're
 * looking at.
 */
const SplatInfo: React.FC<SplatInfoProps> = ({ app, camera, splatEntity, fileName }) => {
    // Tick state forces a re-render so live values (FPS, camera) refresh.
    const [tick, setTick] = useState(0);

    useEffect(() => {
        const interval = setInterval(() => setTick((t) => t + 1), 500);
        return () => clearInterval(interval);
    }, []);

    if (!app || !camera) {
        return null;
    }

    /**
     * Derive splat statistics. PlayCanvas's gsplat instance exposes the
     * splat count via `numSplats` on the underlying data, with several
     * fallbacks across engine versions.
     */
    const getSplatStats = () => {
        if (!splatEntity?.gsplat) return { splatCount: 0 };
        try {
            const inst = splatEntity.gsplat.instance;
            const candidates = [
                inst?.splatData?.numSplats,
                inst?.numSplats,
                inst?.meshInstance?.numSplats,
                splatEntity.gsplat.asset?.resource?.numSplats,
            ];
            for (const c of candidates) {
                if (typeof c === "number" && c > 0) return { splatCount: c };
            }
            return { splatCount: 0 };
        } catch (error) {
            console.warn("Error reading splat stats:", error);
            return { splatCount: 0 };
        }
    };

    const getBounds = () => {
        const aabb = splatEntity?.gsplat?.instance?.meshInstance?.aabb;
        if (!aabb) return null;
        try {
            const center = aabb.center;
            const halfExtents = aabb.halfExtents;
            const min = {
                x: center.x - halfExtents.x,
                y: center.y - halfExtents.y,
                z: center.z - halfExtents.z,
            };
            const max = {
                x: center.x + halfExtents.x,
                y: center.y + halfExtents.y,
                z: center.z + halfExtents.z,
            };
            return {
                min: `${min.x.toFixed(2)}, ${min.y.toFixed(2)}, ${min.z.toFixed(2)}`,
                max: `${max.x.toFixed(2)}, ${max.y.toFixed(2)}, ${max.z.toFixed(2)}`,
                size: `${(halfExtents.x * 2).toFixed(2)} × ${(halfExtents.y * 2).toFixed(2)} × ${(
                    halfExtents.z * 2
                ).toFixed(2)}`,
            };
        } catch (error) {
            return null;
        }
    };

    const { splatCount } = getSplatStats();
    const bounds = getBounds();

    // Approximate FPS from the application's timer. PlayCanvas exposes
    // app.stats.frame but we fall back to dt-based math if it's missing.
    const fps = (() => {
        const dt = app?.stats?.frame?.ms ?? null;
        if (dt && dt > 0) return Math.round(1000 / dt);
        if (app?.timeScale && app?.frame) {
            // No reliable per-frame timing; return null and let the row
            // render an em dash.
            return null;
        }
        return null;
    })();

    const camPos = camera.getPosition?.() ?? { x: 0, y: 0, z: 0 };
    const camForward = camera.forward ?? { x: 0, y: 0, z: -1 };

    /**
     * Tiny helper for the read-only "Field: value" rows. Inlined to keep
     * the component dependency-free and visually consistent with the
     * sibling viewers.
     */
    const Row: React.FC<{ label: string; value: string | number; color?: string }> = ({
        label,
        value,
        color,
    }) => (
        <div
            style={{
                display: "flex",
                justifyContent: "space-between",
                gap: "8px",
                fontSize: "0.8em",
                padding: "3px 0",
                borderBottom: "1px solid rgba(255,255,255,0.05)",
            }}
        >
            <span style={{ color: "#999" }}>{label}</span>
            <span style={{ color: color || "#fff", fontFamily: "monospace", textAlign: "right" }}>
                {value}
            </span>
        </div>
    );

    const camComponent = camera.camera;
    const renderApi = (() => {
        const dev = app?.graphicsDevice;
        if (!dev) return "—";
        if (dev.isWebGPU) return "WebGPU";
        if (dev.webgl2 || dev.isWebGL2) return "WebGL 2";
        return "WebGL 1";
    })();

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
            data-tick={tick}
        >
            {/* Asset */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>Asset</h5>
                <Row label="File" value={fileName || "—"} color="#4CAF50" />
                <Row label="Splats" value={splatCount > 0 ? splatCount.toLocaleString() : "—"} />
            </div>

            {/* Bounds */}
            {bounds && (
                <div style={{ marginBottom: "16px" }}>
                    <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                        World Bounds
                    </h5>
                    <Row label="Min" value={bounds.min} />
                    <Row label="Max" value={bounds.max} />
                    <Row label="Size" value={bounds.size} color="#2196F3" />
                </div>
            )}

            {/* Camera */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>Camera</h5>
                <Row
                    label="Position"
                    value={`${camPos.x.toFixed(2)}, ${camPos.y.toFixed(2)}, ${camPos.z.toFixed(2)}`}
                />
                <Row
                    label="Forward"
                    value={`${camForward.x.toFixed(2)}, ${camForward.y.toFixed(
                        2
                    )}, ${camForward.z.toFixed(2)}`}
                />
                {camComponent && (
                    <>
                        <Row label="FOV" value={`${camComponent.fov?.toFixed(0) ?? "—"}°`} />
                        <Row label="Near" value={camComponent.nearClip?.toFixed(3) ?? "—"} />
                        <Row label="Far" value={camComponent.farClip?.toFixed(0) ?? "—"} />
                    </>
                )}
            </div>

            {/* Performance */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Performance
                </h5>
                <Row
                    label="FPS"
                    value={fps !== null ? fps : "—"}
                    color={
                        fps && fps >= 50
                            ? "#4CAF50"
                            : fps && fps >= 30
                            ? "#FF9800"
                            : fps !== null
                            ? "#F44336"
                            : "#fff"
                    }
                />
                <Row label="Render API" value={renderApi} />
            </div>

            <div
                style={{
                    fontSize: "0.7em",
                    color: "#999",
                    marginTop: "12px",
                    paddingTop: "10px",
                    borderTop: "1px solid rgba(255,255,255,0.1)",
                    textAlign: "center",
                }}
            >
                Live values refresh every 500ms
            </div>
        </div>
    );
};

export default SplatInfo;
