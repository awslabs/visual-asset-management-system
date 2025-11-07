/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import { Auth, Cache } from "aws-amplify";
import { ViewerPluginProps } from "../../core/types";
import { CesiumDependencyManager } from "./dependencies";

// Cesium will be loaded dynamically and accessed from window
// No imports needed - we'll use window.Cesium directly

// Declare Cesium as available from window for TypeScript
declare const Cesium: any;

const CesiumViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    multiFileKeys,
    versionId,
    viewerMode,
    onViewerModeChange,
    onDeletePreview,
    isPreviewFile,
    customParameters,
}) => {
    const cesiumContainer = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [initError, setInitError] = useState<string | null>(null);
    const [loadedTilesets, setLoadedTilesets] = useState<any[]>([]);
    const [config] = useState(Cache.getItem("config"));
    const [cesiumLoaded, setCesiumLoaded] = useState(false);

    // Load Cesium dynamically on mount
    useEffect(() => {
        const loadCesiumLib = async () => {
            try {
                await CesiumDependencyManager.loadCesium();
                setCesiumLoaded(true);
                console.log("Cesium loaded and ready");
            } catch (error) {
                console.error("Failed to load Cesium:", error);
                setError("Failed to load Cesium viewer library");
                setLoading(false);
            }
        };

        loadCesiumLib();

        return () => {
            // Cleanup is handled by the viewer cleanup effect
        };
    }, []);

    // Scene control states
    const [showControls, setShowControls] = useState(true);
    const [wireframeMode, setWireframeMode] = useState(false);
    const [showBoundingVolumes, setShowBoundingVolumes] = useState(false);
    const [lightingEnabled, setLightingEnabled] = useState(true);
    const [shadowsEnabled, setShadowsEnabled] = useState(false);
    const [tilesetVisibility, setTilesetVisibility] = useState<Record<number, boolean>>({});
    const [currentViewMode, setCurrentViewMode] = useState<string>("perspective");
    const [performanceStats, setPerformanceStats] = useState<{
        fps: number;
        memory: number;
    } | null>(null);
    const [measurementMode, setMeasurementMode] = useState<"none" | "distance" | "area">("none");
    const [measurementPoints, setMeasurementPoints] = useState<any[]>([]);
    const [measurementEntities, setMeasurementEntities] = useState<any[]>([]);
    const [measurementResults, setMeasurementResults] = useState<
        Array<{ type: "distance" | "area"; value: number; unit: string; id: number }>
    >([]);
    const [backgroundColor, setBackgroundColor] = useState<string>("#1e1e1e");

    // Helper function to get authentication headers
    const getAuthHeaders = useCallback(async (): Promise<Record<string, string>> => {
        try {
            const session = await Auth.currentSession();
            const idToken = session.getIdToken().getJwtToken();
            return {
                Authorization: `Bearer ${idToken}`,
                "Content-Type": "application/json",
            };
        } catch (error) {
            console.warn("Failed to get auth headers:", error);
            return {};
        }
    }, []);

    // Helper function to construct streaming URL
    const constructStreamingUrl = useCallback(
        (fileKey: string): string => {
            if (!config) {
                throw new Error("Configuration not available");
            }

            // Don't encode the entire path - Cesium needs the slashes to resolve relative paths
            // Only encode individual path segments to handle special characters
            const pathSegments = fileKey.split("/");
            const encodedSegments = pathSegments.map((segment) => encodeURIComponent(segment));
            const encodedFileKey = encodedSegments.join("/");

            return `${config.api}database/${databaseId}/assets/${assetId}/download/stream/${encodedFileKey}`;
        },
        [config, databaseId, assetId]
    );

    // Global error handler for uncaught promise rejections
    useEffect(() => {
        const handleUnhandledRejection = (event: PromiseRejectionEvent) => {
            if (
                event.reason &&
                event.reason.message &&
                event.reason.message.includes("source image could not be decoded")
            ) {
                console.warn("Caught image decoding error (non-critical):", event.reason.message);
                event.preventDefault(); // Prevent the error from being logged as uncaught
                return;
            }
            // Let other errors through
        };

        window.addEventListener("unhandledrejection", handleUnhandledRejection);

        return () => {
            window.removeEventListener("unhandledrejection", handleUnhandledRejection);
        };
    }, []);

    useEffect(() => {
        // Initialize Cesium viewer only after Cesium is loaded
        if (cesiumContainer.current && !viewerRef.current && cesiumLoaded) {
            const initializeViewer = async () => {
                try {
                    setInitError(null);
                    setLoading(true);

                    // Get Cesium Ion token from custom parameters
                    const cesiumIonToken = customParameters?.cesiumIonToken;
                    const hasValidToken = cesiumIonToken && cesiumIonToken.trim() !== "";

                    // Get Cesium from window
                    const Cesium = (window as any).Cesium;

                    // Set Cesium Ion access token if provided
                    if (hasValidToken) {
                        Cesium.Ion.defaultAccessToken = cesiumIonToken;
                        console.log("Cesium Ion token configured - enhanced features enabled");
                    } else {
                        console.log("No Cesium Ion token provided - using basic features only");
                    }

                    // Create viewer with error handling
                    viewerRef.current = new Cesium.Viewer(cesiumContainer.current!, {
                        timeline: false,
                        animation: false,
                        geocoder: false,
                        homeButton: true,
                        sceneModePicker: true,
                        baseLayerPicker: false,
                        navigationHelpButton: false,
                        fullscreenButton: viewerMode === "fullscreen",
                        vrButton: false,
                        infoBox: true,
                        selectionIndicator: true,
                    });

                    // Set a simple imagery provider if no Ion token to prevent image decode errors
                    if (!hasValidToken) {
                        const simpleImageryProvider = new Cesium.SingleTileImageryProvider({
                            url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
                            rectangle: Cesium.Rectangle.fromDegrees(-180, -90, 180, 90),
                            tileWidth: 256,
                            tileHeight: 256,
                        });
                        viewerRef.current.imageryLayers.removeAll();
                        viewerRef.current.imageryLayers.add(
                            new Cesium.ImageryLayer(simpleImageryProvider)
                        );
                    }

                    // Add error event listeners
                    viewerRef.current.scene.renderError.addEventListener(
                        (scene: any, error: any) => {
                            console.error("Cesium render error:", error);
                            setInitError(`Render error: ${error.message || error}`);
                        }
                    );

                    // Only load terrain if Ion token is provided
                    if (hasValidToken) {
                        try {
                            const terrainProvider = await Cesium.createWorldTerrainAsync();
                            if (viewerRef.current) {
                                viewerRef.current.terrainProvider = terrainProvider;
                                console.log("World terrain loaded successfully");
                            }
                        } catch (terrainError: any) {
                            console.warn("Failed to load world terrain:", terrainError);
                            const errorMessage =
                                terrainError?.message ||
                                terrainError?.toString() ||
                                "Unknown terrain error";
                            setInitError(
                                `Terrain loading failed: ${errorMessage}. Check your Cesium Ion token.`
                            );
                        }
                    } else {
                        console.log("Terrain loading skipped - requires Cesium Ion token");
                    }

                    // Configure viewer settings
                    if (viewerRef.current) {
                        viewerRef.current.scene.globe.enableLighting = true;
                        viewerRef.current.scene.globe.depthTestAgainstTerrain = hasValidToken;

                        // Configure camera controller for better 3D tileset interaction
                        const controller = viewerRef.current.scene.screenSpaceCameraController;

                        // Set zoom constraints - these will be adjusted per tileset
                        controller.minimumZoomDistance = 1.0; // Allow very close zoom for detailed models
                        controller.maximumZoomDistance = 50000000.0; // Allow far zoom for context

                        // Improve movement sensitivity for 3D models
                        controller.zoomEventTypes = [
                            Cesium.CameraEventType.WHEEL,
                            Cesium.CameraEventType.PINCH,
                        ];
                        controller.tiltEventTypes = [
                            Cesium.CameraEventType.MIDDLE_DRAG,
                            Cesium.CameraEventType.PINCH,
                            {
                                eventType: Cesium.CameraEventType.LEFT_DRAG,
                                modifier: Cesium.KeyboardEventModifier.CTRL,
                            },
                            {
                                eventType: Cesium.CameraEventType.RIGHT_DRAG,
                                modifier: Cesium.KeyboardEventModifier.CTRL,
                            },
                        ];

                        // Configure collision detection
                        controller.minimumCollisionTerrainHeight = 15000;
                        controller.enableCollisionDetection = true;

                        // Adjust movement rates for better control
                        controller.minimumPickingTerrainHeight = 150000;
                        controller.minimumTrackBallHeight = 7500000;

                        // Set initial camera position (will be updated when tileset loads)
                        viewerRef.current.camera.setView({
                            destination: Cesium.Cartesian3.fromDegrees(-122.4194, 37.7749, 1000), // San Francisco
                            orientation: {
                                heading: Cesium.Math.toRadians(0.0),
                                pitch: Cesium.Math.toRadians(-45.0),
                            },
                        });

                        console.log(
                            "Cesium viewer initialized successfully with enhanced camera controls"
                        );
                    }
                } catch (initError: any) {
                    console.error("Error initializing Cesium viewer:", initError);
                    const errorMessage = initError.message || initError.toString();

                    if (errorMessage.includes("source image could not be decoded")) {
                        setInitError(
                            "Image loading error: Unable to load Cesium imagery. This may be due to network issues or missing Cesium Ion token for enhanced imagery."
                        );
                    } else if (errorMessage.includes("Ion")) {
                        setInitError(
                            `Cesium Ion error: ${errorMessage}. Please check your Ion token configuration.`
                        );
                    } else {
                        setInitError(`Cesium initialization failed: ${errorMessage}`);
                    }
                } finally {
                    setLoading(false);
                }
            };

            initializeViewer();
        }

        return () => {
            // Cleanup on unmount
            if (viewerRef.current) {
                try {
                    viewerRef.current.destroy();
                    viewerRef.current = null;
                } catch (cleanupError) {
                    console.warn("Error during Cesium cleanup:", cleanupError);
                }
            }
        };
    }, [viewerMode, customParameters, cesiumLoaded]);

    // Helper function to configure camera for tileset viewing
    const configureCameraForTileset = useCallback((tileset: any) => {
        if (!viewerRef.current) return;

        const viewer = viewerRef.current;
        const controller = viewer.scene.screenSpaceCameraController;
        const boundingSphere = tileset.boundingSphere;
        const radius = boundingSphere.radius;

        // Adjust camera controller settings based on tileset scale
        if (radius < 100) {
            // Small architectural models
            controller.minimumZoomDistance = Math.max(radius * 0.01, 0.1);
            controller.maximumZoomDistance = radius * 50;
            controller.minimumCollisionTerrainHeight = radius * 0.1;
        } else if (radius < 1000) {
            // Medium-sized models (buildings, complexes)
            controller.minimumZoomDistance = Math.max(radius * 0.05, 1.0);
            controller.maximumZoomDistance = radius * 20;
            controller.minimumCollisionTerrainHeight = radius * 0.2;
        } else {
            // Large-scale models (city blocks, terrain)
            controller.minimumZoomDistance = Math.max(radius * 0.1, 10.0);
            controller.maximumZoomDistance = radius * 10;
            controller.minimumCollisionTerrainHeight = radius * 0.5;
        }

        console.log(`Configured camera for tileset with radius: ${radius.toFixed(2)}m`);
    }, []);

    // Helper function to create appropriate camera offset for tileset
    const createCameraOffset = useCallback((tileset: any) => {
        const boundingSphere = tileset.boundingSphere;
        const radius = boundingSphere.radius;

        // Calculate appropriate distance based on model size
        let distance: number;
        let pitch: number;
        let heading: number;

        if (radius < 50) {
            // Very small models - close view
            distance = Math.max(radius * 3, 10);
            pitch = Cesium.Math.toRadians(-25);
            heading = Cesium.Math.toRadians(45);
        } else if (radius < 200) {
            // Small to medium models - moderate view
            distance = radius * 2.5;
            pitch = Cesium.Math.toRadians(-35);
            heading = Cesium.Math.toRadians(30);
        } else if (radius < 1000) {
            // Medium models - wider view
            distance = radius * 2;
            pitch = Cesium.Math.toRadians(-45);
            heading = Cesium.Math.toRadians(15);
        } else {
            // Large models - overview
            distance = radius * 1.5;
            pitch = Cesium.Math.toRadians(-60);
            heading = Cesium.Math.toRadians(0);
        }

        return new Cesium.HeadingPitchRange(heading, pitch, distance);
    }, []);

    // Scene control functions
    const toggleWireframe = useCallback(() => {
        if (!viewerRef.current) return;

        const newWireframeMode = !wireframeMode;
        setWireframeMode(newWireframeMode);

        loadedTilesets.forEach((tileset) => {
            if (newWireframeMode) {
                // Apply wireframe style to tileset
                tileset.style = new Cesium.Cesium3DTileStyle({
                    color: 'color("white", 0.5)',
                    show: true,
                });
                // Enable debug wireframe for better visualization
                tileset.debugWireframe = true;
            } else {
                tileset.style = undefined;
                tileset.debugWireframe = false;
            }
        });

        console.log(`Wireframe mode ${newWireframeMode ? "enabled" : "disabled"}`);
    }, [wireframeMode, loadedTilesets]);

    const toggleBoundingVolumes = useCallback(() => {
        if (!viewerRef.current) return;

        const newShowBoundingVolumes = !showBoundingVolumes;
        setShowBoundingVolumes(newShowBoundingVolumes);

        loadedTilesets.forEach((tileset) => {
            tileset.debugShowBoundingVolume = newShowBoundingVolumes;
        });

        console.log(`Bounding volumes ${newShowBoundingVolumes ? "shown" : "hidden"}`);
    }, [showBoundingVolumes, loadedTilesets]);

    const toggleLighting = useCallback(() => {
        if (!viewerRef.current) return;

        const newLightingEnabled = !lightingEnabled;
        setLightingEnabled(newLightingEnabled);

        viewerRef.current.scene.globe.enableLighting = newLightingEnabled;

        console.log(`Lighting ${newLightingEnabled ? "enabled" : "disabled"}`);
    }, [lightingEnabled]);

    const toggleShadows = useCallback(() => {
        if (!viewerRef.current) return;

        const newShadowsEnabled = !shadowsEnabled;
        setShadowsEnabled(newShadowsEnabled);

        viewerRef.current.shadows = newShadowsEnabled;

        console.log(`Shadows ${newShadowsEnabled ? "enabled" : "disabled"}`);
    }, [shadowsEnabled]);

    const setCameraView = useCallback(
        (viewType: string) => {
            if (!viewerRef.current || loadedTilesets.length === 0) return;

            const tileset = loadedTilesets[0];
            const boundingSphere = tileset.boundingSphere;
            const center = boundingSphere.center;
            const radius = boundingSphere.radius;

            let heading: number, pitch: number, distance: number;

            switch (viewType) {
                case "top":
                    heading = 0;
                    pitch = Cesium.Math.toRadians(-90);
                    distance = radius * 2;
                    break;
                case "front":
                    heading = 0;
                    pitch = 0;
                    distance = radius * 2.5;
                    break;
                case "side":
                    heading = Cesium.Math.toRadians(90);
                    pitch = 0;
                    distance = radius * 2.5;
                    break;
                case "isometric":
                    heading = Cesium.Math.toRadians(45);
                    pitch = Cesium.Math.toRadians(-35);
                    distance = radius * 2.5;
                    break;
                default:
                    return;
            }

            const offset = new Cesium.HeadingPitchRange(heading, pitch, distance);
            viewerRef.current.zoomTo(tileset, offset);

            // Don't persist the view mode - just temporarily highlight during animation
            setCurrentViewMode(viewType);
            setTimeout(() => setCurrentViewMode("perspective"), 1000);

            console.log(`Camera set to ${viewType} view`);
        },
        [loadedTilesets]
    );

    // Measurement tool functions
    const clearMeasurements = useCallback(() => {
        if (!viewerRef.current) return;

        // Remove all measurement entities
        measurementEntities.forEach((entity) => {
            viewerRef.current!.entities.remove(entity);
        });

        setMeasurementEntities([]);
        setMeasurementPoints([]);
        setMeasurementResults([]);
        setMeasurementMode("none");

        console.log("Cleared all measurements");
    }, [measurementEntities]);

    const resetScene = useCallback(() => {
        if (!viewerRef.current || loadedTilesets.length === 0) return;

        // Clear measurements first
        clearMeasurements();

        // Reset all scene properties
        setWireframeMode(false);
        setShowBoundingVolumes(false);
        setLightingEnabled(true);
        setShadowsEnabled(false);

        // Reset tileset styles and debug properties
        loadedTilesets.forEach((tileset) => {
            tileset.style = undefined;
            tileset.debugShowBoundingVolume = false;
            tileset.debugWireframe = false;
            tileset.show = true; // Ensure visibility
        });

        // Reset scene properties
        viewerRef.current.scene.globe.enableLighting = true;
        viewerRef.current.shadows = false;
        viewerRef.current.scene.globe.material = undefined;

        // Reset tileset visibility state
        const resetVisibility: Record<number, boolean> = {};
        loadedTilesets.forEach((_, index) => {
            resetVisibility[index] = true;
        });
        setTilesetVisibility(resetVisibility);

        // Reset camera to initial position
        const tileset = loadedTilesets[0];
        const cameraOffset = createCameraOffset(tileset);
        viewerRef.current.zoomTo(tileset, cameraOffset);
        setCurrentViewMode("perspective");

        console.log("Scene reset to default settings");
    }, [loadedTilesets, createCameraOffset, clearMeasurements]);

    const toggleTilesetVisibility = useCallback(
        (index: number) => {
            if (index >= loadedTilesets.length) return;

            const tileset = loadedTilesets[index];
            const newVisibility = !tilesetVisibility[index];

            tileset.show = newVisibility;
            setTilesetVisibility((prev) => ({
                ...prev,
                [index]: newVisibility,
            }));

            console.log(`Tileset ${index} ${newVisibility ? "shown" : "hidden"}`);
        },
        [loadedTilesets, tilesetVisibility]
    );

    const changeBackgroundColor = useCallback((color: string) => {
        if (!viewerRef.current) return;

        setBackgroundColor(color);

        // Convert hex color to Cesium Color
        const cesiumColor = Cesium.Color.fromCssColorString(color);
        viewerRef.current.scene.backgroundColor = cesiumColor;

        console.log(`Background color changed to: ${color}`);
    }, []);

    const takeScreenshot = useCallback(() => {
        if (!viewerRef.current) return;

        try {
            viewerRef.current.render();
            const canvas = viewerRef.current.scene.canvas;

            // Create download link with error handling
            canvas.toBlob((blob: any) => {
                try {
                    if (blob) {
                        const url = URL.createObjectURL(blob);
                        const link = document.createElement("a");
                        link.href = url;
                        link.download = `cesium-screenshot-${new Date()
                            .toISOString()
                            .slice(0, 19)
                            .replace(/:/g, "-")}.png`;
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                        URL.revokeObjectURL(url);

                        console.log("Screenshot saved");
                    } else {
                        console.warn("Failed to create screenshot blob");
                    }
                } catch (blobError) {
                    console.warn("Error processing screenshot blob:", blobError);
                }
            }, "image/png");
        } catch (screenshotError) {
            console.warn("Error taking screenshot:", screenshotError);
        }
    }, []);

    // Measurement tool functions
    const startMeasurement = useCallback((mode: "distance" | "area") => {
        if (!viewerRef.current) return;

        setMeasurementMode(mode);
        setMeasurementPoints([]);

        console.log(`Started ${mode} measurement`);
    }, []);

    const addMeasurementPoint = useCallback(
        (position: any) => {
            if (!viewerRef.current || measurementMode === "none") return;

            const newPoints = [...measurementPoints, position];
            setMeasurementPoints(newPoints);

            // Add simple point marker
            const pointEntity = viewerRef.current.entities.add({
                position: position,
                point: {
                    pixelSize: 8,
                    color: Cesium.Color.YELLOW,
                    outlineColor: Cesium.Color.BLACK,
                    outlineWidth: 2,
                },
            });

            const newEntities = [...measurementEntities, pointEntity];

            if (measurementMode === "distance" && newPoints.length >= 2) {
                // Calculate distance
                const distance = Cesium.Cartesian3.distance(newPoints[0], newPoints[1]);

                // Add simple line
                const lineEntity = viewerRef.current.entities.add({
                    polyline: {
                        positions: newPoints,
                        width: 3,
                        material: Cesium.Color.YELLOW,
                    },
                });

                newEntities.push(lineEntity);

                // Store result in UI state
                const newResult = {
                    type: "distance" as const,
                    value: distance,
                    unit: "m",
                    id: Date.now(),
                };
                setMeasurementResults((prev) => [...prev, newResult]);
                setMeasurementMode("none"); // Complete distance measurement

                console.log(`Distance measured: ${distance.toFixed(2)} meters`);
            } else if (measurementMode === "area" && newPoints.length >= 3) {
                // Add simple polygon outline
                const polygonEntity = viewerRef.current.entities.add({
                    polygon: {
                        hierarchy: newPoints,
                        material: Cesium.Color.YELLOW.withAlpha(0.3),
                        outline: true,
                        outlineColor: Cesium.Color.YELLOW,
                    },
                });

                // Calculate approximate area using shoelace formula
                const cartographicPoints = newPoints.map((point) =>
                    Cesium.Cartographic.fromCartesian(point)
                );

                let area = 0;
                for (let i = 0; i < cartographicPoints.length; i++) {
                    const j = (i + 1) % cartographicPoints.length;
                    area += cartographicPoints[i].longitude * cartographicPoints[j].latitude;
                    area -= cartographicPoints[j].longitude * cartographicPoints[i].latitude;
                }
                area = Math.abs(area) / 2;

                // Convert to square meters (approximate)
                const areaInSqMeters =
                    area * 111319.9 * 111319.9 * Math.cos(cartographicPoints[0].latitude);

                newEntities.push(polygonEntity);

                // Store result in UI state
                const newResult = {
                    type: "area" as const,
                    value: areaInSqMeters,
                    unit: "m²",
                    id: Date.now(),
                };
                setMeasurementResults((prev) => [...prev, newResult]);
                setMeasurementMode("none"); // Complete area measurement

                console.log(`Area measured: ${areaInSqMeters.toFixed(2)} square meters`);
            }

            setMeasurementEntities(newEntities);
        },
        [measurementMode, measurementPoints, measurementEntities]
    );

    // Handle measurement clicks with improved picking
    useEffect(() => {
        if (!viewerRef.current || measurementMode === "none") return;

        const handler = new Cesium.ScreenSpaceEventHandler(viewerRef.current.scene.canvas);

        handler.setInputAction((event: any) => {
            // Try to pick from tileset first, then fallback to ellipsoid
            let pickedPosition = viewerRef.current!.scene.pick(event.position);

            if (
                pickedPosition &&
                pickedPosition.primitive &&
                pickedPosition.primitive instanceof Cesium.Cesium3DTileset
            ) {
                // Use the picked position on the tileset
                const cartesian = viewerRef.current!.scene.pickPosition(event.position);
                if (cartesian) {
                    addMeasurementPoint(cartesian);
                    return;
                }
            }

            // Fallback to ellipsoid picking
            const ellipsoidPosition = viewerRef.current!.camera.pickEllipsoid(
                event.position,
                viewerRef.current!.scene.globe.ellipsoid
            );

            if (ellipsoidPosition) {
                addMeasurementPoint(ellipsoidPosition);
            }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

        // Handle ESC key to cancel measurement
        const keyHandler = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                clearMeasurements();
            }
        };

        document.addEventListener("keydown", keyHandler);

        return () => {
            handler.destroy();
            document.removeEventListener("keydown", keyHandler);
        };
    }, [measurementMode, addMeasurementPoint, clearMeasurements]);

    // Performance monitoring
    useEffect(() => {
        if (!viewerRef.current) return;

        let lastTime = performance.now();
        let frameCount = 0;

        const updatePerformanceStats = () => {
            if (viewerRef.current) {
                const currentTime = performance.now();
                frameCount++;

                // Calculate FPS over 1 second intervals
                if (currentTime - lastTime >= 1000) {
                    const fps = Math.round((frameCount * 1000) / (currentTime - lastTime));
                    const memory = (performance as any).memory
                        ? Math.round((performance as any).memory.usedJSHeapSize / 1024 / 1024)
                        : 0;

                    setPerformanceStats({ fps, memory });

                    frameCount = 0;
                    lastTime = currentTime;
                }
            }
        };

        const interval = setInterval(updatePerformanceStats, 100); // Check every 100ms
        return () => clearInterval(interval);
    }, [viewerRef.current]);

    // Initialize tileset visibility tracking
    useEffect(() => {
        const initialVisibility: Record<number, boolean> = {};
        loadedTilesets.forEach((_, index) => {
            initialVisibility[index] = true;
        });
        setTilesetVisibility(initialVisibility);
    }, [loadedTilesets]);

    const loadSingleTileset = useCallback(
        async (key: string) => {
            if (!viewerRef.current || !config) return;

            try {
                console.log("Loading single tileset:", key);

                // Get authentication headers
                const authHeaders = await getAuthHeaders();

                // Construct streaming URL
                const streamingUrl = constructStreamingUrl(key);
                console.log("Streaming URL:", streamingUrl);

                // Get Cesium from window
                const Cesium = (window as any).Cesium;

                // Create Cesium Resource with authentication headers
                const resource = new Cesium.Resource({
                    url: streamingUrl,
                    headers: authHeaders,
                });

                // Create 3D Tileset using the authenticated resource
                const tileset = await Cesium.Cesium3DTileset.fromUrl(resource);

                // Add tileset to scene
                viewerRef.current.scene.primitives.add(tileset);

                console.log(`Successfully loaded tileset: ${key}`);

                // Configure camera and zoom after a short delay to allow tileset to initialize
                setTimeout(() => {
                    if (viewerRef.current && tileset.boundingSphere) {
                        try {
                            // Configure camera controller for this tileset
                            configureCameraForTileset(tileset);

                            // Create appropriate camera offset
                            const cameraOffset = createCameraOffset(tileset);

                            // Zoom to tileset with proper offset
                            viewerRef.current.zoomTo(tileset, cameraOffset);

                            console.log(
                                `Zoomed to tileset with offset - Distance: ${cameraOffset.range.toFixed(
                                    2
                                )}m, Pitch: ${Cesium.Math.toDegrees(cameraOffset.pitch).toFixed(
                                    1
                                )}°`
                            );
                        } catch (zoomError) {
                            console.warn(
                                "Error during enhanced zoom, falling back to basic zoom:",
                                zoomError
                            );
                            // Fallback to basic zoom
                            if (viewerRef.current) {
                                viewerRef.current.zoomTo(tileset);
                            }
                        }
                    } else {
                        // Fallback to basic zoom if no bounding sphere
                        console.warn("No bounding sphere available, using basic zoom");
                        if (viewerRef.current) {
                            viewerRef.current.zoomTo(tileset);
                        }
                    }
                }, 500); // Wait 500ms for tileset to initialize

                setLoadedTilesets((prev) => [...prev, tileset]);
            } catch (error: any) {
                console.error(`Error loading tileset ${key}:`, error);
                const errorMessage = error?.message || error?.toString() || "Unknown error";
                setError(`Tileset loading failed for "${key}": ${errorMessage}`);
            }
        },
        [
            config,
            getAuthHeaders,
            constructStreamingUrl,
            configureCameraForTileset,
            createCameraOffset,
        ]
    );

    const loadMultipleTilesets = useCallback(
        async (keys: string[]) => {
            if (!viewerRef.current || !config) return;

            const tilesets: any[] = [];

            for (let i = 0; i < keys.length; i++) {
                const key = keys[i];
                try {
                    console.log(`Loading tileset ${i + 1}/${keys.length}:`, key);

                    // Get Cesium from window
                    const Cesium = (window as any).Cesium;

                    // Get authentication headers
                    const authHeaders = await getAuthHeaders();

                    // Construct streaming URL
                    const streamingUrl = constructStreamingUrl(key);
                    console.log(`Streaming URL for ${key}:`, streamingUrl);

                    // Create Cesium Resource with authentication headers
                    const resource = new Cesium.Resource({
                        url: streamingUrl,
                        headers: authHeaders,
                    });

                    // Create 3D Tileset using the authenticated resource
                    const tileset = await Cesium.Cesium3DTileset.fromUrl(resource);

                    // Add tileset to scene
                    viewerRef.current!.scene.primitives.add(tileset);

                    console.log(`Successfully loaded tileset ${i + 1}/${keys.length}: ${key}`);
                    tilesets.push(tileset);
                } catch (fileError) {
                    console.error(`Error loading tileset ${key}:`, fileError);
                }
            }

            if (tilesets.length > 0) {
                setLoadedTilesets(tilesets);

                // Wait a moment for tilesets to load, then configure camera and zoom
                setTimeout(() => {
                    if (viewerRef.current && tilesets.length > 0) {
                        const primaryTileset = tilesets[0];

                        if (primaryTileset.boundingSphere) {
                            try {
                                // Configure camera controller for the primary tileset
                                configureCameraForTileset(primaryTileset);

                                // Create appropriate camera offset
                                const cameraOffset = createCameraOffset(primaryTileset);

                                // Zoom to primary tileset with proper offset
                                viewerRef.current.zoomTo(primaryTileset, cameraOffset);

                                console.log(
                                    `Zoomed to multiple tilesets with offset - Distance: ${cameraOffset.range.toFixed(
                                        2
                                    )}m, Pitch: ${Cesium.Math.toDegrees(cameraOffset.pitch).toFixed(
                                        1
                                    )}°`
                                );
                            } catch (zoomError) {
                                console.warn(
                                    "Error during enhanced zoom for multiple tilesets, falling back to basic zoom:",
                                    zoomError
                                );
                                // Fallback to basic zoom
                                viewerRef.current.zoomTo(primaryTileset);
                            }
                        } else {
                            // Fallback to basic zoom if no bounding sphere
                            console.warn(
                                "No bounding sphere available for multiple tilesets, using basic zoom"
                            );
                            viewerRef.current.zoomTo(primaryTileset);
                        }
                    }
                }, 1000);
            }

            console.log(`Loaded ${tilesets.length}/${keys.length} tilesets successfully`);
        },
        [config, getAuthHeaders, constructStreamingUrl]
    );

    useEffect(() => {
        if (!viewerRef.current || !config) {
            return;
        }

        const loadTilesets = async () => {
            try {
                setLoading(true);
                setError(null);

                // Clear existing tilesets
                viewerRef.current!.scene.primitives.removeAll();
                setLoadedTilesets([]);

                if (multiFileKeys && multiFileKeys.length > 0) {
                    // Multi-file mode
                    await loadMultipleTilesets(multiFileKeys);
                } else if (assetKey && assetKey !== "") {
                    // Single file mode
                    await loadSingleTileset(assetKey);
                }
            } catch (error) {
                console.error("Error loading tilesets:", error);
                setError(error instanceof Error ? error.message : "Failed to load 3D tilesets");
            } finally {
                setLoading(false);
            }
        };

        loadTilesets();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [assetId, assetKey, multiFileKeys, databaseId, versionId, config, viewerRef.current]);

    if (error) {
        return (
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    height: "100%",
                    padding: "20px",
                    backgroundColor: "#f5f5f5",
                }}
            >
                <div style={{ textAlign: "center" }}>
                    <h3 style={{ color: "#d32f2f", marginBottom: "10px" }}>
                        Error Loading 3D Tileset
                    </h3>
                    <p style={{ color: "#666" }}>{error}</p>
                    <p style={{ color: "#999", fontSize: "0.9em", marginTop: "10px" }}>
                        Supported format: .json (3D Tileset definition files)
                    </p>
                </div>
            </div>
        );
    }

    if (!config) {
        return (
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    height: "100%",
                    padding: "20px",
                    backgroundColor: "#f5f5f5",
                }}
            >
                <div style={{ textAlign: "center" }}>
                    <h3 style={{ color: "#666", marginBottom: "10px" }}>
                        Loading Configuration...
                    </h3>
                    <p style={{ color: "#999", fontSize: "0.9em" }}>
                        Waiting for VAMS configuration to load
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div style={{ position: "relative", width: "100%", height: "100%" }}>
            {/* Display initialization errors at the top */}
            {initError && (
                <div
                    style={{
                        position: "absolute",
                        top: "0",
                        left: "0",
                        right: "0",
                        backgroundColor: "#ffebee",
                        border: "1px solid #f44336",
                        borderRadius: "4px",
                        padding: "12px 16px",
                        margin: "8px",
                        zIndex: 1001,
                        fontSize: "0.9em",
                    }}
                >
                    <div
                        style={{
                            color: "#d32f2f",
                            fontWeight: "bold",
                            marginBottom: "4px",
                        }}
                    >
                        Cesium Initialization Error
                    </div>
                    <div style={{ color: "#666" }}>{initError}</div>
                    <button
                        onClick={() => setInitError(null)}
                        style={{
                            position: "absolute",
                            top: "8px",
                            right: "8px",
                            background: "none",
                            border: "none",
                            color: "#d32f2f",
                            cursor: "pointer",
                            fontSize: "16px",
                            padding: "0",
                            width: "20px",
                            height: "20px",
                        }}
                        title="Dismiss error"
                    >
                        ×
                    </button>
                </div>
            )}

            {loading && (
                <div
                    style={{
                        position: "absolute",
                        top: "50%",
                        left: "50%",
                        transform: "translate(-50%, -50%)",
                        zIndex: 1000,
                        backgroundColor: "rgba(255, 255, 255, 0.9)",
                        padding: "20px",
                        borderRadius: "8px",
                        textAlign: "center",
                    }}
                >
                    <div>Loading 3D Tileset...</div>
                    {multiFileKeys && multiFileKeys.length > 1 && (
                        <div style={{ fontSize: "0.9em", color: "#666", marginTop: "5px" }}>
                            Loading {multiFileKeys.length} tilesets
                        </div>
                    )}
                </div>
            )}

            <div
                ref={cesiumContainer}
                style={{
                    width: "100%",
                    height: "100%",
                    backgroundColor: backgroundColor,
                }}
            />

            {viewerRef.current && loadedTilesets.length > 0 && (
                <div
                    style={{
                        position: "absolute",
                        bottom: "10px",
                        right: "10px",
                        backgroundColor: "rgba(0, 0, 0, 0.7)",
                        color: "white",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        fontSize: "0.9em",
                        zIndex: 1000,
                    }}
                >
                    {loadedTilesets.length === 1
                        ? "1 tileset loaded"
                        : `${loadedTilesets.length} tilesets loaded`}
                </div>
            )}

            {/* Scene Controls Panel */}
            {viewerRef.current && showControls && (
                <div
                    style={{
                        position: "fixed",
                        top: initError ? "50px" : "20px",
                        left: "10px",
                        bottom: "20px",
                        backgroundColor: "rgba(0, 0, 0, 0.8)",
                        color: "white",
                        padding: "16px",
                        paddingBottom: "24px", // Extra padding at bottom
                        borderRadius: "8px",
                        fontSize: "0.85em",
                        zIndex: 1000,
                        minWidth: "200px",
                        maxWidth: "250px",
                        overflowY: "auto",
                        overflowX: "hidden",
                        // Force scrollbar to be visible
                        scrollbarWidth: "thin",
                        scrollbarColor: "rgba(255, 255, 255, 0.5) transparent",
                    }}
                >
                    <div style={{ display: "flex", alignItems: "center", marginBottom: "12px" }}>
                        <button
                            onClick={() => setShowControls(false)}
                            style={{
                                background: "none",
                                border: "none",
                                color: "white",
                                cursor: "pointer",
                                fontSize: "16px",
                                padding: "0",
                                width: "20px",
                                height: "20px",
                                marginRight: "8px",
                            }}
                            title="Hide controls"
                        >
                            ×
                        </button>
                        <h4 style={{ margin: 0, fontSize: "1.1em" }}>Scene Controls</h4>
                    </div>

                    {/* View Controls */}
                    <div style={{ marginBottom: "16px" }}>
                        <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                            Camera Views
                        </h5>
                        <div
                            style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}
                        >
                            {["top", "front", "side", "isometric"].map((view) => (
                                <button
                                    key={view}
                                    onClick={() => setCameraView(view)}
                                    style={{
                                        background:
                                            currentViewMode === view
                                                ? "#4CAF50"
                                                : "rgba(255, 255, 255, 0.1)",
                                        border: "1px solid rgba(255, 255, 255, 0.2)",
                                        color: "white",
                                        padding: "6px 8px",
                                        borderRadius: "4px",
                                        cursor: "pointer",
                                        fontSize: "0.8em",
                                        textTransform: "capitalize",
                                    }}
                                >
                                    {view}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Rendering Controls */}
                    <div style={{ marginBottom: "16px" }}>
                        <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                            Rendering
                        </h5>
                        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                            <label
                                style={{ display: "flex", alignItems: "center", cursor: "pointer" }}
                            >
                                <input
                                    type="checkbox"
                                    checked={wireframeMode}
                                    onChange={toggleWireframe}
                                    style={{ marginRight: "8px" }}
                                />
                                Wireframe Mode
                            </label>
                            <label
                                style={{ display: "flex", alignItems: "center", cursor: "pointer" }}
                            >
                                <input
                                    type="checkbox"
                                    checked={showBoundingVolumes}
                                    onChange={toggleBoundingVolumes}
                                    style={{ marginRight: "8px" }}
                                />
                                Bounding Volumes
                            </label>
                            <label
                                style={{ display: "flex", alignItems: "center", cursor: "pointer" }}
                            >
                                <input
                                    type="checkbox"
                                    checked={lightingEnabled}
                                    onChange={toggleLighting}
                                    style={{ marginRight: "8px" }}
                                />
                                Lighting
                            </label>
                            <label
                                style={{ display: "flex", alignItems: "center", cursor: "pointer" }}
                            >
                                <input
                                    type="checkbox"
                                    checked={shadowsEnabled}
                                    onChange={toggleShadows}
                                    style={{ marginRight: "8px" }}
                                />
                                Shadows
                            </label>
                        </div>

                        {/* Background Color Controls */}
                        <div style={{ marginTop: "12px" }}>
                            <h6 style={{ margin: "0 0 6px 0", fontSize: "0.8em", color: "#ddd" }}>
                                Background:
                            </h6>
                            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                                {[
                                    { color: "#000000", name: "Black" },
                                    { color: "#ffffff", name: "White" },
                                    { color: "#87ceeb", name: "Light Blue" },
                                ].map(({ color, name }) => (
                                    <button
                                        key={color}
                                        onClick={() => changeBackgroundColor(color)}
                                        style={{
                                            width: "32px",
                                            height: "24px",
                                            backgroundColor: color,
                                            border:
                                                backgroundColor === color
                                                    ? "2px solid #4CAF50"
                                                    : "1px solid rgba(255, 255, 255, 0.3)",
                                            borderRadius: "3px",
                                            cursor: "pointer",
                                            position: "relative",
                                        }}
                                        title={`${name} (${color})`}
                                    >
                                        {backgroundColor === color && (
                                            <div
                                                style={{
                                                    position: "absolute",
                                                    top: "50%",
                                                    left: "50%",
                                                    transform: "translate(-50%, -50%)",
                                                    color:
                                                        color === "#ffffff" || color === "#87ceeb"
                                                            ? "#000"
                                                            : "#fff",
                                                    fontSize: "10px",
                                                    fontWeight: "bold",
                                                }}
                                            >
                                                ✓
                                            </div>
                                        )}
                                    </button>
                                ))}
                                <input
                                    type="color"
                                    value={backgroundColor}
                                    onChange={(e) => changeBackgroundColor(e.target.value)}
                                    style={{
                                        width: "32px",
                                        height: "24px",
                                        border: "1px solid rgba(255, 255, 255, 0.3)",
                                        borderRadius: "3px",
                                        cursor: "pointer",
                                        backgroundColor: "transparent",
                                    }}
                                    title="Custom color picker"
                                />
                            </div>
                        </div>
                    </div>

                    {/* Tileset Visibility */}
                    {loadedTilesets.length > 1 && (
                        <div style={{ marginBottom: "16px" }}>
                            <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                                Tilesets
                            </h5>
                            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                                {loadedTilesets.map((_, index) => (
                                    <label
                                        key={index}
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            cursor: "pointer",
                                        }}
                                    >
                                        <input
                                            type="checkbox"
                                            checked={tilesetVisibility[index] !== false}
                                            onChange={() => toggleTilesetVisibility(index)}
                                            style={{ marginRight: "8px" }}
                                        />
                                        Tileset {index + 1}
                                    </label>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Measurement Tools */}
                    <div style={{ marginBottom: "16px" }}>
                        <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                            Measurements
                        </h5>
                        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                            <button
                                onClick={() => startMeasurement("distance")}
                                style={{
                                    background:
                                        measurementMode === "distance" ? "#4CAF50" : "#9C27B0",
                                    border: "none",
                                    color: "white",
                                    padding: "8px 12px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                }}
                                disabled={measurementMode === "distance"}
                            >
                                📏{" "}
                                {measurementMode === "distance"
                                    ? "Click 2 points"
                                    : "Measure Distance"}
                            </button>
                            <button
                                onClick={() => startMeasurement("area")}
                                style={{
                                    background: measurementMode === "area" ? "#4CAF50" : "#9C27B0",
                                    border: "none",
                                    color: "white",
                                    padding: "8px 12px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                }}
                                disabled={measurementMode === "area"}
                            >
                                📐 {measurementMode === "area" ? "Click 3+ points" : "Measure Area"}
                            </button>
                            <button
                                onClick={clearMeasurements}
                                style={{
                                    background: "#F44336",
                                    border: "none",
                                    color: "white",
                                    padding: "8px 12px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                }}
                                disabled={measurementResults.length === 0}
                            >
                                🗑️ Clear Measurements
                            </button>
                        </div>

                        {/* Measurement Results */}
                        {measurementResults.length > 0 && (
                            <div
                                style={{
                                    marginTop: "12px",
                                    padding: "8px",
                                    backgroundColor: "rgba(255, 255, 255, 0.1)",
                                    borderRadius: "4px",
                                }}
                            >
                                <h6
                                    style={{
                                        margin: "0 0 6px 0",
                                        fontSize: "0.8em",
                                        color: "#ddd",
                                    }}
                                >
                                    Results:
                                </h6>
                                {measurementResults.map((result, index) => (
                                    <div
                                        key={result.id}
                                        style={{
                                            fontSize: "0.8em",
                                            color: "#fff",
                                            marginBottom: "4px",
                                            padding: "4px 6px",
                                            backgroundColor: "rgba(255, 255, 255, 0.1)",
                                            borderRadius: "3px",
                                        }}
                                    >
                                        <span style={{ marginRight: "8px" }}>
                                            {result.type === "distance" ? "📏" : "📐"}
                                        </span>
                                        <strong>
                                            {result.value.toFixed(2)} {result.unit}
                                        </strong>
                                        <span style={{ color: "#ccc", marginLeft: "8px" }}>
                                            ({result.type === "distance" ? "Distance" : "Area"} #
                                            {index + 1})
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Action Buttons */}
                    <div style={{ marginBottom: "16px" }}>
                        <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                            Actions
                        </h5>
                        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                            <button
                                onClick={takeScreenshot}
                                style={{
                                    background: "#2196F3",
                                    border: "none",
                                    color: "white",
                                    padding: "8px 12px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                }}
                            >
                                📷 Screenshot
                            </button>
                            <button
                                onClick={resetScene}
                                style={{
                                    background: "#FF9800",
                                    border: "none",
                                    color: "white",
                                    padding: "8px 12px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                }}
                            >
                                🔄 Reset Scene
                            </button>
                        </div>
                    </div>

                    {/* Performance Stats */}
                    {performanceStats && (
                        <div>
                            <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                                Performance
                            </h5>
                            <div style={{ fontSize: "0.8em", color: "#aaa" }}>
                                <div>FPS: {performanceStats.fps}</div>
                                {performanceStats.memory > 0 && (
                                    <div>Memory: {performanceStats.memory} MB</div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Controls Toggle Button */}
            {loadedTilesets.length > 0 && !showControls && (
                <button
                    onClick={() => setShowControls(true)}
                    style={{
                        position: "absolute",
                        top: initError ? "50px" : "20px",
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
                    title="Show scene controls"
                >
                    ⚙️ Controls
                </button>
            )}

            {/* Measurement Instructions */}
            {measurementMode !== "none" && (
                <div
                    style={{
                        position: "absolute",
                        bottom: "60px",
                        left: "10px",
                        backgroundColor: "rgba(76, 175, 80, 0.9)",
                        color: "white",
                        padding: "12px 16px",
                        borderRadius: "4px",
                        fontSize: "0.85em",
                        zIndex: 1000,
                        maxWidth: "300px",
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "4px" }}>
                        {measurementMode === "distance"
                            ? "📏 Distance Measurement"
                            : "📐 Area Measurement"}
                    </div>
                    <div>
                        {measurementMode === "distance"
                            ? `Click 2 points to measure distance. Points: ${measurementPoints.length}/2`
                            : `Click 3 or more points to measure area. Points: ${measurementPoints.length}/3+`}
                    </div>
                    <div style={{ fontSize: "0.8em", marginTop: "4px", opacity: 0.9 }}>
                        Press ESC or click "Clear Measurements" to cancel
                    </div>
                </div>
            )}

            {/* Tileset info panel */}
            {loadedTilesets.length > 0 && (
                <div
                    style={{
                        position: "absolute",
                        bottom: "10px",
                        left: "10px",
                        backgroundColor: "rgba(0, 0, 0, 0.7)",
                        color: "white",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        fontSize: "0.8em",
                        zIndex: 1000,
                        maxWidth: "300px",
                    }}
                >
                    <div>
                        <strong>Controls:</strong> Left-drag to rotate, Wheel to zoom,
                        Ctrl+Left-drag to tilt
                    </div>
                    {!customParameters?.cesiumIonToken && (
                        <div style={{ color: "#ffeb3b", marginTop: "4px" }}>
                            ⚠ Enhanced features available with Cesium Ion token
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default CesiumViewerComponent;
