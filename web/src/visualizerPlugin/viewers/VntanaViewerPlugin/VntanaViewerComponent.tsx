/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { VntanaDependencyManager } from "./dependencies";
import { VntanaViewerProps, VntanaViewerElement } from "./types/viewer.types";
import LoadingSpinner from "../../components/LoadingSpinner";

const VntanaViewerComponent: React.FC<VntanaViewerProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<VntanaViewerElement | null>(null);
    const sceneGraphRef = useRef<HTMLElement | null>(null);
    const explodedViewRef = useRef<HTMLElement | null>(null);
    const initializationRef = useRef(false);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");
    const [error, setError] = useState<string | null>(null);
    const [showSceneGraph, setShowSceneGraph] = useState(true); // Show by default

    useEffect(() => {
        if (!assetKey || initializationRef.current) return;
        initializationRef.current = true;

        const initViewer = async () => {
            try {
                console.log("VNTANA Viewer: Starting initialization");
                setLoadingMessage("Initializing viewer...");

                // Load VNTANA viewer library
                setLoadingMessage("Loading VNTANA viewer library...");
                await VntanaDependencyManager.loadVntana();
                console.log("VNTANA Viewer: Library loaded successfully");

                // Download asset
                console.log("VNTANA Viewer: Downloading asset");
                setLoadingMessage("Downloading asset...");
                const response = await downloadAsset({
                    assetId,
                    databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                if (response && Array.isArray(response) && response[0] !== false) {
                    const assetUrl = response[1]; // URL from downloadAsset
                    console.log("VNTANA Viewer: Asset URL retrieved:", assetUrl);

                    // Create VNTANA viewer element
                    setLoadingMessage("Creating viewer...");
                    const viewer = document.createElement("vntana-viewer") as VntanaViewerElement;

                    // Set viewer properties for optimal visibility
                    // Check file extension to determine which property to use
                    const fileExtension = assetKey.toLowerCase().split(".").pop();
                    if (fileExtension === "usdz") {
                        viewer.usdzSrc = assetUrl;
                        console.log("VNTANA Viewer: Loading USDZ file using usdzSrc");
                    } else {
                        // Default to src for GLB and other formats
                        viewer.src = assetUrl;
                        console.log("VNTANA Viewer: Loading file using src (GLB or other format)");
                    }
                    viewer.loading = "eager";

                    // Lighting settings
                    viewer.shadowIntensity = 0; // Disable shadows initially
                    viewer.shadowRadius = 0;

                    // Camera settings
                    viewer.enableAutoRotate = false;
                    viewer.fieldOfView = "45deg";
                    viewer.cameraRotation = "-15deg 0deg 0deg";

                    // Rendering settings - use defaults for better compatibility
                    viewer.exposure = 1; // Use default exposure
                    viewer.toneMapping = "aces";
                    viewer.antiAliasing = "ssaa";

                    // Don't set background - let VNTANA use its default
                    // This allows the viewer to use its built-in lighting

                    // Set light rig intensity
                    viewer.setAttribute("light-rig-intensity", "2");

                    // Add a custom light rig for better illumination
                    viewer.setAttribute(
                        "light-rig",
                        "directional intensity 1 color #ffffff position 5m 5m 5m direction -1m -1m -1m;" +
                            "directional intensity 0.5 color #ffffff position -5m 5m 5m direction 1m -1m -1m;" +
                            "directional intensity 0.3 color #ffffff position 0m -5m 5m direction 0m 1m -1m"
                    );

                    // Style the viewer element
                    viewer.style.width = "100%";
                    viewer.style.height = "100%";
                    viewer.style.display = "block";

                    // Add event listeners
                    viewer.addEventListener("load", (event: Event) => {
                        const loadEvent = event as any;
                        const loadTime = loadEvent.detail?.time || 0;
                        console.log(`VNTANA Viewer: Model loaded successfully in ${loadTime}ms`);
                        setIsLoading(false);
                    });

                    viewer.addEventListener("error", (event: Event) => {
                        const errorEvent = event as any;
                        const errorMessage = errorEvent.detail?.message || "Failed to load model";
                        console.error("VNTANA Viewer: Error loading model:", errorMessage);
                        setError(errorMessage);
                        setIsLoading(false);
                    });

                    // Add viewer to container
                    if (containerRef.current) {
                        containerRef.current.appendChild(viewer);
                        viewerRef.current = viewer;
                        console.log("VNTANA Viewer: Viewer element added to DOM");

                        // Wait for viewer to be fully initialized before adding UI elements
                        setTimeout(() => {
                            if (containerRef.current && viewerRef.current) {
                                // Create Scene Graph element
                                const sceneGraph = document.createElement(
                                    "vntana-scene-graph"
                                ) as any;
                                sceneGraph.style.position = "absolute";
                                sceneGraph.style.left = "10px";
                                sceneGraph.style.top = "60px";
                                sceneGraph.style.maxHeight = "calc(100% - 120px)";
                                sceneGraph.style.width = "300px";
                                sceneGraph.style.backgroundColor = "white"; // White background as recommended by VNTANA
                                sceneGraph.style.borderRadius = "4px";
                                sceneGraph.style.padding = "10px";
                                sceneGraph.style.overflowY = "auto";
                                sceneGraph.style.zIndex = "1000";
                                sceneGraph.style.display = "block"; // Show by default since showSceneGraph is true
                                // Set the viewer property to reference the viewer element
                                sceneGraph.viewer = viewerRef.current;
                                containerRef.current.appendChild(sceneGraph);
                                sceneGraphRef.current = sceneGraph;
                                console.log("VNTANA Viewer: Scene graph added");

                                // Create Exploded View slider
                                const explodedView = document.createElement(
                                    "vntana-exploded-view"
                                ) as any;
                                explodedView.style.position = "absolute";
                                explodedView.style.bottom = "20px";
                                explodedView.style.left = "50%";
                                explodedView.style.transform = "translateX(-50%)";
                                explodedView.style.width = "300px";
                                explodedView.style.backgroundColor = "white"; // White background as recommended by VNTANA
                                explodedView.style.borderRadius = "4px";
                                explodedView.style.padding = "10px";
                                explodedView.style.zIndex = "1000";
                                // Set the viewer property to reference the viewer element
                                explodedView.viewer = viewerRef.current;
                                containerRef.current.appendChild(explodedView);
                                explodedViewRef.current = explodedView;
                                console.log("VNTANA Viewer: Exploded view added");

                                // Create Center Button (bottom right)
                                const centerButton = document.createElement(
                                    "vntana-center-button"
                                ) as any;
                                centerButton.style.position = "absolute";
                                centerButton.style.bottom = "130px";
                                centerButton.style.right = "20px";
                                centerButton.style.zIndex = "1001";
                                centerButton.viewer = viewerRef.current;
                                containerRef.current.appendChild(centerButton);
                                console.log("VNTANA Viewer: Center button added");

                                // Create Zoom In Button (bottom right, above center)
                                const zoomInButton = document.createElement(
                                    "vntana-zoom-in-button"
                                ) as any;
                                zoomInButton.style.position = "absolute";
                                zoomInButton.style.bottom = "90px";
                                zoomInButton.style.right = "20px";
                                zoomInButton.style.zIndex = "1001";
                                zoomInButton.viewer = viewerRef.current;
                                containerRef.current.appendChild(zoomInButton);
                                console.log("VNTANA Viewer: Zoom in button added");

                                // Create Zoom Out Button (bottom right, above zoom in)
                                const zoomOutButton = document.createElement(
                                    "vntana-zoom-out-button"
                                ) as any;
                                zoomOutButton.style.position = "absolute";
                                zoomOutButton.style.bottom = "50px";
                                zoomOutButton.style.right = "20px";
                                zoomOutButton.style.zIndex = "1001";
                                zoomOutButton.viewer = viewerRef.current;
                                containerRef.current.appendChild(zoomOutButton);
                                console.log("VNTANA Viewer: Zoom out button added");
                            }
                        }, 500);
                    }
                } else {
                    console.error("VNTANA Viewer: Failed to download asset");
                    setError("Failed to download asset");
                    setIsLoading(false);
                }
            } catch (error) {
                console.error("VNTANA Viewer: Initialization error:", error);
                const errorMessage =
                    error instanceof Error ? error.message : "Unknown error occurred";
                setError(errorMessage);
                setIsLoading(false);
            }
        };

        initViewer();

        // Cleanup function
        return () => {
            console.log("VNTANA Viewer: Cleaning up");

            // Remove UI elements
            if (containerRef.current) {
                if (sceneGraphRef.current) {
                    try {
                        containerRef.current.removeChild(sceneGraphRef.current);
                        sceneGraphRef.current = null;
                    } catch (error) {
                        console.error("VNTANA Viewer: Error removing scene graph:", error);
                    }
                }

                if (explodedViewRef.current) {
                    try {
                        containerRef.current.removeChild(explodedViewRef.current);
                        explodedViewRef.current = null;
                    } catch (error) {
                        console.error("VNTANA Viewer: Error removing exploded view:", error);
                    }
                }

                // Remove viewer element
                if (viewerRef.current) {
                    try {
                        containerRef.current.removeChild(viewerRef.current);
                        viewerRef.current = null;
                    } catch (error) {
                        console.error("VNTANA Viewer: Error removing viewer element:", error);
                    }
                }
            }
        };
    }, [assetKey, assetId, databaseId, versionId]);

    // Effect to handle scene graph visibility
    useEffect(() => {
        if (sceneGraphRef.current) {
            sceneGraphRef.current.style.display = showSceneGraph ? "block" : "none";
        }
    }, [showSceneGraph]);

    // Toggle scene graph visibility
    const toggleSceneGraph = () => {
        setShowSceneGraph((prev) => !prev);
    };

    return (
        <div
            ref={containerRef}
            style={{
                width: "100%",
                height: "100%",
                backgroundColor: "#1a1a1a",
                position: "relative",
            }}
        >
            {/* Loading overlay */}
            {isLoading && !error && <LoadingSpinner message={loadingMessage} />}

            {/* Error message */}
            {error && (
                <div
                    style={{
                        position: "absolute",
                        top: "50%",
                        left: "50%",
                        transform: "translate(-50%, -50%)",
                        color: "white",
                        fontSize: "16px",
                        backgroundColor: "rgba(255, 0, 0, 0.8)",
                        padding: "20px",
                        borderRadius: "8px",
                        textAlign: "center",
                        maxWidth: "80%",
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "10px" }}>
                        Error Loading Model
                    </div>
                    <div>{error}</div>
                </div>
            )}

            {/* Control Buttons */}
            {!isLoading && !error && (
                <>
                    {/* Scene Graph Toggle Button */}
                    <button
                        onClick={toggleSceneGraph}
                        style={{
                            position: "absolute",
                            top: "10px",
                            left: "10px",
                            backgroundColor: showSceneGraph
                                ? "rgba(0, 120, 212, 0.9)"
                                : "rgba(0, 0, 0, 0.7)",
                            color: "white",
                            border: "1px solid rgba(255, 255, 255, 0.3)",
                            borderRadius: "4px",
                            padding: "8px 12px",
                            fontSize: "12px",
                            cursor: "pointer",
                            zIndex: 1001,
                            display: "flex",
                            alignItems: "center",
                            gap: "6px",
                            transition: "background-color 0.2s",
                        }}
                        onMouseEnter={(e) => {
                            if (!showSceneGraph) {
                                e.currentTarget.style.backgroundColor = "rgba(0, 0, 0, 0.85)";
                            }
                        }}
                        onMouseLeave={(e) => {
                            if (!showSceneGraph) {
                                e.currentTarget.style.backgroundColor = "rgba(0, 0, 0, 0.7)";
                            }
                        }}
                        title="Toggle Scene Graph"
                    >
                        <svg
                            width="16"
                            height="16"
                            viewBox="0 0 16 16"
                            fill="currentColor"
                            style={{ flexShrink: 0 }}
                        >
                            <path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h3A1.5 1.5 0 0 1 7 2.5v3A1.5 1.5 0 0 1 5.5 7h-3A1.5 1.5 0 0 1 1 5.5v-3zm8 0A1.5 1.5 0 0 1 10.5 1h3A1.5 1.5 0 0 1 15 2.5v3A1.5 1.5 0 0 1 13.5 7h-3A1.5 1.5 0 0 1 9 5.5v-3zm-8 8A1.5 1.5 0 0 1 2.5 9h3A1.5 1.5 0 0 1 7 10.5v3A1.5 1.5 0 0 1 5.5 15h-3A1.5 1.5 0 0 1 1 13.5v-3zm8 0A1.5 1.5 0 0 1 10.5 9h3a1.5 1.5 0 0 1 1.5 1.5v3a1.5 1.5 0 0 1-1.5 1.5h-3A1.5 1.5 0 0 1 9 13.5v-3z" />
                        </svg>
                        Scene Graph
                    </button>

                    {/* Info Panel */}
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
                        VNTANA 3D Viewer
                        <br />
                        Mouse: Rotate | Wheel: Zoom | Right-click: Pan
                    </div>
                </>
            )}
        </div>
    );
};

export default VntanaViewerComponent;
