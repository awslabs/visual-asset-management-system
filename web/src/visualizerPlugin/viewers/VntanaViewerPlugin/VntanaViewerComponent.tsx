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
    const initializationRef = useRef(false);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");
    const [error, setError] = useState<string | null>(null);

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
                                // Create Scene Buttons Container (left bottom)
                                const sceneButtonsContainer = document.createElement("div");
                                sceneButtonsContainer.className = "button-container scene-buttons";

                                // Add Scene Graph Button
                                const sceneGraphButton = document.createElement(
                                    "vntana-scene-graph-button"
                                ) as any;
                                sceneGraphButton.viewer = viewerRef.current;
                                sceneButtonsContainer.appendChild(sceneGraphButton);

                                containerRef.current.appendChild(sceneButtonsContainer);
                                console.log("VNTANA Viewer: Scene buttons container added");

                                // Programmatically click the scene graph button to show it by default
                                setTimeout(() => {
                                    sceneGraphButton.click();
                                    console.log("VNTANA Viewer: Scene graph opened by default");
                                }, 100);

                                // Create General Buttons Container (right bottom)
                                const generalButtonsContainer = document.createElement("div");
                                generalButtonsContainer.className =
                                    "button-container general-buttons";

                                // Add Exploded View Button
                                const explodedViewButton = document.createElement(
                                    "vntana-exploded-view"
                                ) as any;
                                explodedViewButton.viewer = viewerRef.current;
                                generalButtonsContainer.appendChild(explodedViewButton);

                                // Add Center Button
                                const centerButton = document.createElement(
                                    "vntana-center-button"
                                ) as any;
                                centerButton.viewer = viewerRef.current;
                                generalButtonsContainer.appendChild(centerButton);

                                // Add Fullscreen Button
                                const fsButton = document.createElement("vntana-fs-button") as any;
                                fsButton.viewer = viewerRef.current;
                                generalButtonsContainer.appendChild(fsButton);

                                containerRef.current.appendChild(generalButtonsContainer);
                                console.log("VNTANA Viewer: General buttons container added");

                                // Create Zoom Buttons Container (right, above general buttons)
                                const zoomButtonsContainer = document.createElement("div");
                                zoomButtonsContainer.className = "button-container zoom-buttons";

                                // Add Zoom In Button
                                const zoomInButton = document.createElement(
                                    "vntana-zoom-in-button"
                                ) as any;
                                zoomInButton.viewer = viewerRef.current;
                                zoomButtonsContainer.appendChild(zoomInButton);

                                // Add Zoom Out Button
                                const zoomOutButton = document.createElement(
                                    "vntana-zoom-out-button"
                                ) as any;
                                zoomOutButton.viewer = viewerRef.current;
                                zoomButtonsContainer.appendChild(zoomOutButton);

                                containerRef.current.appendChild(zoomButtonsContainer);
                                console.log("VNTANA Viewer: Zoom buttons container added");
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

            // Remove viewer element and all its children (button containers)
            if (containerRef.current && viewerRef.current) {
                try {
                    containerRef.current.removeChild(viewerRef.current);
                    viewerRef.current = null;
                } catch (error) {
                    console.error("VNTANA Viewer: Error removing viewer element:", error);
                }
            }
        };
    }, [assetKey, assetId, databaseId, versionId]);

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
