/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { BabylonJSGaussianSplatViewerProps } from "./types/viewer.types";
import LoadingSpinner from "../../components/LoadingSpinner";
import { BabylonJSGaussianSplatDependencyManager } from "./dependencies";
import BabylonJSPanel from "./BabylonJSPanel";

// Declare BABYLON as it will be loaded dynamically
declare const BABYLON: any;

const BabylonJSGaussianSplatViewerComponent: React.FC<BabylonJSGaussianSplatViewerProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const initializationRef = useRef(false);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");

    // Viewer instance refs (populated after BabylonJS finishes loading the
    // splat). These power the floating control panel and HUD.
    const viewerInstanceRef = useRef<{
        engine: any;
        scene: any;
        camera: any;
        splatMesh: any;
    } | null>(null);

    // Initial camera framing captured right after auto-fit, used by the
    // panel's "Reset Scene" / "Fit to Scene" actions to restore the view
    // the user first saw.
    const initialCameraStateRef = useRef<{
        target: any;
        radius: number;
        alpha: number;
        beta: number;
    } | null>(null);

    // Tick state forces the panel to re-render once the splat finishes
    // loading and the viewer instance becomes available.
    const [sceneReady, setSceneReady] = useState(false);
    const [showPanel, setShowPanel] = useState(true);

    useEffect(() => {
        if (!assetKey || initializationRef.current) return;
        initializationRef.current = true;

        // Add CSS to hide any unwanted UI elements (Spectrum color pickers)
        const style = document.createElement("style");
        style.id = "babylonjs-gaussian-splat-viewer-hide-ui";
        style.textContent = `
            .sp-container, .sp-palette-container, .sp-picker-container,
            .sp-input-container, .sp-button-container, .sp-initial,
            .sp-palette, .sp-color, .sp-hue, .sp-alpha, .sp-top,
            .sp-fill, .sp-top-inner, .sp-sat, .sp-val, .sp-dragger,
            .sp-clear, .sp-alpha-inner, .sp-alpha-handle, .sp-input,
            .sp-cancel, .sp-choose, .sp-palette-toggle {
                display: none !important;
                visibility: hidden !important;
                opacity: 0 !important;
                pointer-events: none !important;
                position: absolute !important;
                left: -9999px !important;
                top: -9999px !important;
            }
        `;
        document.head.appendChild(style);

        // Function to remove unwanted DOM elements
        const removeUnwantedElements = () => {
            const unwantedSelectors = [
                ".sp-container",
                ".sp-palette-container",
                ".sp-picker-container",
                '[class*="sp-"]',
            ];

            unwantedSelectors.forEach((selector) => {
                const elements = document.querySelectorAll(selector);
                elements.forEach((element) => {
                    if (element && element.parentNode) {
                        element.parentNode.removeChild(element);
                    }
                });
            });
        };

        // Set up periodic cleanup
        const cleanupInterval = setInterval(removeUnwantedElements, 1000);

        const initViewer = async () => {
            try {
                console.log("BabylonJS Gaussian Splat Viewer: Starting initialization");
                setLoadingMessage("Loading BabylonJS...");

                // Load BabylonJS dependencies
                const BABYLON = await BabylonJSGaussianSplatDependencyManager.loadBabylonJS();

                setLoadingMessage("Initializing viewer...");

                // Create canvas directly in DOM
                const canvas = document.createElement("canvas");
                canvas.style.width = "100%";
                canvas.style.height = "100%";
                canvas.style.display = "block";
                canvas.style.backgroundColor = "#222";
                canvas.style.touchAction = "none";

                // Prevent context menu and wheel scrolling
                canvas.addEventListener("contextmenu", (e) => e.preventDefault());
                canvas.addEventListener(
                    "wheel",
                    (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                    },
                    { passive: false }
                );

                if (containerRef.current) {
                    containerRef.current.appendChild(canvas);
                    console.log("BabylonJS Gaussian Splat Viewer: Canvas added to DOM");
                }

                // Create engine with inspector disabled
                const engine = new BABYLON.Engine(canvas, true, {
                    preserveDrawingBuffer: true,
                    stencil: true,
                    disableWebGL2Support: false,
                });
                const scene = new BABYLON.Scene(engine);
                scene.clearColor = new BABYLON.Color4(0.1, 0.1, 0.1, 1);

                // Create camera with fine controls
                const camera = new BABYLON.ArcRotateCamera(
                    "camera",
                    0,
                    Math.PI / 6,
                    0.5,
                    BABYLON.Vector3.Zero(),
                    scene
                );
                camera.attachControl(canvas, true);

                // ULTRA AGGRESSIVE camera controls
                camera.wheelPrecision = 500; // Fast zoom but not too aggressive
                camera.pinchPrecision = 20; // Fast pinch zoom
                camera.panningSensibility = 1000; // Slower panning (higher = slower)
                camera.angularSensibilityX = 500; // Faster rotation
                camera.angularSensibilityY = 500; // Faster rotation
                camera.minZ = 0.0001;
                camera.lowerRadiusLimit = 0.0001; // Almost no limit
                camera.upperRadiusLimit = 5000;
                camera.speed = 2; // Increase overall camera speed

                // Use percentage-based zoom for smoother experience at all distances
                camera.wheelDeltaPercentage = 0.01; // 1% of current distance per wheel tick

                console.log("BabylonJS Camera Settings:", {
                    wheelPrecision: camera.wheelPrecision,
                    initialRadius: camera.radius,
                    initialBeta: camera.beta,
                    lowerRadiusLimit: camera.lowerRadiusLimit,
                });

                // Handle window resize to maintain aspect ratio
                const handleResize = () => {
                    if (canvas && containerRef.current) {
                        const rect = containerRef.current.getBoundingClientRect();

                        // Update canvas size while maintaining aspect ratio
                        canvas.width = rect.width;
                        canvas.height = rect.height;

                        // Update BabylonJS engine
                        engine.resize();

                        console.log(
                            "BabylonJS Gaussian Splat Viewer: Resized to",
                            rect.width,
                            "x",
                            rect.height
                        );
                    }
                };

                // Set up resize observer
                const resizeObserver = new ResizeObserver(handleResize);
                if (containerRef.current) {
                    resizeObserver.observe(containerRef.current);
                }

                // Initial resize
                handleResize();

                // Start render loop
                engine.runRenderLoop(() => scene.render());
                console.log("BabylonJS Gaussian Splat Viewer: BabylonJS initialized");

                // Stash refs early so the panel can show partial state if
                // splat loading is slow (it'll just show empty stats).
                viewerInstanceRef.current = {
                    engine,
                    scene,
                    camera,
                    splatMesh: null,
                };

                // Download and load asset
                console.log("BabylonJS Gaussian Splat Viewer: Downloading asset");
                setLoadingMessage("Downloading asset...");
                const response = await downloadAsset({
                    assetId,
                    databaseId,
                    key: assetKey,
                    versionId: versionId,
                    assetVersionId: assetVersionId as any,
                    downloadType: "assetFile",
                });

                if (response && Array.isArray(response) && response[0] !== false) {
                    console.log(
                        "BabylonJS Gaussian Splat Viewer: Asset URL retrieved, downloading asset..."
                    );
                    // Keep "Downloading asset..." message since the actual download happens in GaussianSplattingMesh

                    try {
                        console.log(
                            "BabylonJS Gaussian Splat Viewer: Loading Gaussian Splat with SceneLoader..."
                        );

                        BABYLON.SceneLoader.ImportMeshAsync(
                            "",
                            "",
                            response[1],
                            scene,
                            null,
                            ".spz"
                        )
                            .then((result: any) => {
                                console.log(
                                    "BabylonJS Gaussian Splat Viewer: File loaded successfully, positioning camera"
                                );

                                if (result.meshes.length > 0) {
                                    const mesh = result.meshes[0];
                                    const boundingInfo = mesh.getBoundingInfo();
                                    const mn = boundingInfo.boundingBox.minimumWorld;
                                    const mx = boundingInfo.boundingBox.maximumWorld;

                                    // SPZ files store coordinates as fractional-fixed-point
                                    // integers; the scene loader hasn't applied that scale yet.
                                    // Detect oversized bounds (rawMaxHalf >> 10) and divide by
                                    // the fractional-bits factor to recover real-world units.
                                    const rawMaxHalf = Math.max(
                                        (mx.x - mn.x) / 2,
                                        (mx.y - mn.y) / 2,
                                        (mx.z - mn.z) / 2
                                    );
                                    const fractionalBits = 12; // SPZ default
                                    const fbScale = rawMaxHalf > 10 ? 1 << fractionalBits : 1;

                                    // Use createDefaultCameraOrLight for all cases
                                    scene.createDefaultCameraOrLight(true, true, true);
                                    const cam = scene.activeCamera;
                                    const scaledRadius = (cam.radius / fbScale) * 3.0;
                                    cam.target =
                                        fbScale > 1 ? cam.target.scale(1 / fbScale) : cam.target;
                                    cam.radius = scaledRadius;
                                    cam.lowerRadiusLimit = scaledRadius * 0.0001;
                                    cam.minZ = scaledRadius * 0.0001;
                                    cam.maxZ = scaledRadius * 200;
                                    cam.attachControl(scene.getEngine().getRenderingCanvas(), true);
                                    cam.wheelDeltaPercentage = 0.01;
                                    cam.panningSensibility = 1000;

                                    console.log("BabylonJS Camera Auto-fit:", {
                                        rawMaxHalf,
                                        fbScale,
                                        scaledRadius,
                                    });

                                    // Capture the resolved camera state for the panel's
                                    // "Reset View" action.
                                    initialCameraStateRef.current = {
                                        target: cam.target.clone
                                            ? cam.target.clone()
                                            : new BABYLON.Vector3(
                                                  cam.target.x,
                                                  cam.target.y,
                                                  cam.target.z
                                              ),
                                        radius: cam.radius,
                                        alpha: cam.alpha,
                                        beta: cam.beta,
                                    };

                                    // Update viewer instance with the active camera and
                                    // splat mesh so the panel can drive both.
                                    if (viewerInstanceRef.current) {
                                        viewerInstanceRef.current.camera = cam;
                                        viewerInstanceRef.current.splatMesh = mesh;
                                    }
                                }

                                setSceneReady(true);
                                setIsLoading(false);
                            })
                            .catch((error: unknown) => {
                                console.error(
                                    "BabylonJS Gaussian Splat Viewer: Error loading file:",
                                    error
                                );
                                setIsLoading(false);
                            });
                    } catch (error) {
                        console.error(
                            "BabylonJS Gaussian Splat Viewer: Error creating GaussianSplattingMesh:",
                            error
                        );
                        setIsLoading(false); // Hide loading on error
                    }
                } else {
                    console.error("BabylonJS Gaussian Splat Viewer: Failed to download asset");
                    setIsLoading(false); // Hide loading on error
                }
            } catch (error) {
                console.error("BabylonJS Gaussian Splat Viewer: Initialization error:", error);
                setIsLoading(false); // Hide loading on error
            }
        };

        initViewer();

        // Cleanup function
        return () => {
            clearInterval(cleanupInterval);

            // Remove the CSS style
            const existingStyle = document.getElementById(
                "babylonjs-gaussian-splat-viewer-hide-ui"
            );
            if (existingStyle) {
                existingStyle.remove();
            }

            // Final cleanup of any remaining unwanted elements
            removeUnwantedElements();
        };
    }, [assetKey, assetId, databaseId, versionId, assetVersionId]);

    // Reset camera back to the framing computed at load time. Wired into
    // the panel's "Reset Scene" action and the F shortcut as a fallback
    // when no splat bounds can be derived live.
    const resetView = () => {
        const cam = viewerInstanceRef.current?.camera;
        const initial = initialCameraStateRef.current;
        if (!cam || !initial) return;
        cam.target = initial.target.clone ? initial.target.clone() : initial.target;
        cam.radius = initial.radius;
        cam.alpha = initial.alpha;
        cam.beta = initial.beta;
        console.log("BabylonJS Splat: View reset to initial framing");
    };

    // Keyboard shortcuts: Esc hides the panel, F resets the view. Mirrors
    // the ThreeJS viewer.
    useEffect(() => {
        const handleKeyPress = (event: KeyboardEvent) => {
            if (
                event.target instanceof HTMLInputElement ||
                event.target instanceof HTMLTextAreaElement
            ) {
                return;
            }
            const key = event.key.toLowerCase();
            if (key === "escape" && showPanel) {
                setShowPanel(false);
            } else if (key === "f") {
                resetView();
            }
        };
        window.addEventListener("keydown", handleKeyPress);
        return () => window.removeEventListener("keydown", handleKeyPress);
    }, [showPanel]);

    // File name shown in the panel header. Strip path segments to display
    // only the leaf filename.
    const fileName = assetKey ? assetKey.split("/").pop() || assetKey : undefined;

    return (
        <div
            ref={containerRef}
            style={{
                width: "100%",
                height: "100%",
                backgroundColor: "#000",
                position: "relative",
            }}
            onWheel={(e) => {
                e.preventDefault();
                e.stopPropagation();
            }}
        >
            {/* Loading overlay */}
            {isLoading && <LoadingSpinner message={loadingMessage} />}

            {/* Floating control panel — visible after the splat loads */}
            {sceneReady && viewerInstanceRef.current && showPanel && (
                <BabylonJSPanel
                    scene={viewerInstanceRef.current.scene}
                    camera={viewerInstanceRef.current.camera}
                    engine={viewerInstanceRef.current.engine}
                    splatMesh={viewerInstanceRef.current.splatMesh}
                    fileName={fileName}
                    onClose={() => setShowPanel(false)}
                    onResetView={resetView}
                />
            )}

            {/* Show-panel button — only shown when the panel is hidden */}
            {sceneReady && !showPanel && (
                <button
                    onClick={() => setShowPanel(true)}
                    style={{
                        position: "absolute",
                        top: "20px",
                        left: "10px",
                        backgroundColor: "rgba(0, 0, 0, 0.7)",
                        color: "white",
                        border: "1px solid rgba(255, 255, 255, 0.2)",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.8em",
                        zIndex: 1000,
                    }}
                    title="Show controls panel"
                >
                    ⚙️ Panel
                </button>
            )}

            {/* Compact HUD — viewer name, mouse hint and live splat count */}
            {sceneReady && viewerInstanceRef.current && (
                <div
                    style={{
                        position: "absolute",
                        top: "10px",
                        right: "10px",
                        color: "white",
                        fontSize: "12px",
                        backgroundColor: "rgba(0,0,0,0.7)",
                        padding: "8px",
                        borderRadius: "4px",
                        zIndex: 1000,
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "4px" }}>
                        BabylonJS Gaussian Splat
                    </div>
                    <div style={{ fontSize: "0.9em", opacity: 0.9 }}>
                        Mouse: Rotate | Wheel: Zoom | Right-click: Pan
                    </div>
                    {fileName && (
                        <div
                            style={{
                                fontSize: "0.85em",
                                marginTop: "6px",
                                color: "#4CAF50",
                            }}
                        >
                            📁 {fileName}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default BabylonJSGaussianSplatViewerComponent;
