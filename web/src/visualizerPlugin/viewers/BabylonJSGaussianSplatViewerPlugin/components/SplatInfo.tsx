/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";

interface SplatInfoProps {
    /** BabylonJS Scene instance. */
    scene: any;
    /** BabylonJS ArcRotateCamera instance. */
    camera: any;
    /** BabylonJS Engine instance — used for the live FPS readout. */
    engine: any;
    /** The loaded GaussianSplattingMesh (or first mesh of the imported file). */
    splatMesh: any;
    /** Source asset key/file name shown in the header. */
    fileName?: string;
}

/**
 * Splat Info tab for the BabylonJS Gaussian Splat viewer panel.
 *
 * Displays read-only telemetry about the loaded scene: file name, splat
 * count, vertex count, world-space bounding box, and live camera state
 * (position, target, radius, alpha/beta). Refreshes on a 2 Hz timer so
 * the camera fields track user movement without forcing a full re-render
 * of the parent panel.
 *
 * This tab replaces the ThreeJS "Scene Graph" tab — Gaussian Splat assets
 * are flat (one renderable mesh) so a hierarchy view does not apply, but
 * users still benefit from quick stats about what they're looking at.
 */
const SplatInfo: React.FC<SplatInfoProps> = ({ scene, camera, engine, splatMesh, fileName }) => {
    // Tick state forces a re-render so live values (FPS, camera) refresh.
    const [tick, setTick] = useState(0);

    useEffect(() => {
        const interval = setInterval(() => setTick((t) => t + 1), 500);
        return () => clearInterval(interval);
    }, []);

    if (!scene || !camera) {
        return null;
    }

    // Derive splat statistics. BabylonJS Gaussian Splat meshes expose
    // either splat count (via internal data) or vertex count via geometry.
    const getSplatStats = () => {
        if (!splatMesh) return { splatCount: 0, vertexCount: 0 };
        try {
            // GaussianSplattingMesh stores splat count in different places
            // depending on BabylonJS version. Probe several common ones.
            let splatCount = 0;
            const candidates = [
                splatMesh.splatsCount,
                splatMesh.numSplats,
                splatMesh._splatsCount,
                splatMesh._splats?.length,
                splatMesh.splats?.length,
            ];
            for (const c of candidates) {
                if (typeof c === "number" && c > 0) {
                    splatCount = c;
                    break;
                }
            }

            let vertexCount = 0;
            if (typeof splatMesh.getTotalVertices === "function") {
                vertexCount = splatMesh.getTotalVertices();
            }

            return { splatCount, vertexCount };
        } catch (error) {
            console.warn("Error reading splat stats:", error);
            return { splatCount: 0, vertexCount: 0 };
        }
    };

    const getBounds = () => {
        if (!splatMesh?.getBoundingInfo) return null;
        try {
            const bb = splatMesh.getBoundingInfo().boundingBox;
            const min = bb.minimumWorld;
            const max = bb.maximumWorld;
            const size = max.subtract(min);
            return {
                min: `${min.x.toFixed(2)}, ${min.y.toFixed(2)}, ${min.z.toFixed(2)}`,
                max: `${max.x.toFixed(2)}, ${max.y.toFixed(2)}, ${max.z.toFixed(2)}`,
                size: `${size.x.toFixed(2)} × ${size.y.toFixed(2)} × ${size.z.toFixed(2)}`,
            };
        } catch (error) {
            return null;
        }
    };

    const { splatCount, vertexCount } = getSplatStats();
    const bounds = getBounds();
    const fps = engine?.getFps ? Math.round(engine.getFps()) : null;

    const cameraPos = camera.position;
    const cameraTarget = camera.target;

    /**
     * Tiny helper for the read-only "Field: value" rows. Inlined to keep
     * the component dependency-free and visually consistent with ThreeJS.
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
            // Hidden dependency on tick keeps readouts updating.
            data-tick={tick}
        >
            {/* Asset */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>Asset</h5>
                <Row label="File" value={fileName || "—"} color="#4CAF50" />
                <Row label="Splats" value={splatCount > 0 ? splatCount.toLocaleString() : "—"} />
                {vertexCount > 0 && (
                    <Row label="Vertices" value={vertexCount.toLocaleString()} color="#FF9800" />
                )}
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
                    value={`${cameraPos.x.toFixed(2)}, ${cameraPos.y.toFixed(
                        2
                    )}, ${cameraPos.z.toFixed(2)}`}
                />
                <Row
                    label="Target"
                    value={`${cameraTarget.x.toFixed(2)}, ${cameraTarget.y.toFixed(
                        2
                    )}, ${cameraTarget.z.toFixed(2)}`}
                />
                <Row label="Radius" value={camera.radius?.toFixed(3) ?? "—"} />
                <Row
                    label="Alpha (yaw)"
                    value={
                        typeof camera.alpha === "number"
                            ? `${((camera.alpha * 180) / Math.PI).toFixed(1)}°`
                            : "—"
                    }
                />
                <Row
                    label="Beta (pitch)"
                    value={
                        typeof camera.beta === "number"
                            ? `${((camera.beta * 180) / Math.PI).toFixed(1)}°`
                            : "—"
                    }
                />
            </div>

            {/* Performance */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Performance
                </h5>
                <Row
                    label="FPS"
                    value={fps !== null ? fps : "—"}
                    color={fps && fps >= 50 ? "#4CAF50" : fps && fps >= 30 ? "#FF9800" : "#F44336"}
                />
                <Row
                    label="Render API"
                    value={engine?.webGLVersion === 2 ? "WebGL 2" : "WebGL 1"}
                />
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
