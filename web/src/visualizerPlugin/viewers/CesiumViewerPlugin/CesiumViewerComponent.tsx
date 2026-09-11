/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import { getDualValidAccessToken } from "../../../utils/authTokenUtils";
import { appCache } from "../../../services/appCache";
import { ViewerPluginProps } from "../../core/types";
import { CesiumDependencyManager } from "./dependencies";
import CesiumSceneGraph from "./components/CesiumSceneGraph";

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
    assetVersionId,
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
    const [config] = useState(appCache.getItem("config"));
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
    const [sceneMode, setSceneMode] = useState<"3d" | "2d" | "columbus">("3d");
    const [allTilesetsGeolocated, setAllTilesetsGeolocated] = useState(true);
    const [activePanelTab, setActivePanelTab] = useState<"sceneGraph" | "controls">("controls");
    const [selectedTiles, setSelectedTiles] = useState<any[]>([]);
    // Bumped whenever per-tile visibility changes so the scene graph re-renders
    const [sceneVersion, setSceneVersion] = useState(0);
    // Hidden/selected tiles live in refs so the per-frame tileVisible listener
    // reads current state without re-subscribing
    const hiddenTilesRef = useRef<Set<any>>(new Set());
    const selectedTilesRef = useRef<Set<any>>(new Set());
    const [pickedFeatureInfo, setPickedFeatureInfo] = useState<{
        title: string;
        properties: Array<[string, string]>;
    } | null>(null);
    const [measurementPoints, setMeasurementPoints] = useState<any[]>([]);
    const [measurementEntities, setMeasurementEntities] = useState<any[]>([]);
    const [dismissedVersionWarning, setDismissedVersionWarning] = useState(false);
    const [measurementResults, setMeasurementResults] = useState<
        Array<{ type: "distance" | "area"; value: number; unit: string; id: number }>
    >([]);
    const [backgroundColor, setBackgroundColor] = useState<string>("#1e1e1e");

    // Helper function to get authentication headers
    const getAuthHeaders = useCallback(async (): Promise<Record<string, string>> => {
        try {
            const idToken = await getDualValidAccessToken();
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

            let url = `${config.api}database/${databaseId}/assets/${assetId}/download/stream/${encodedFileKey}`;
            if (assetVersionId) {
                url += `?assetVersionId=${encodeURIComponent(assetVersionId)}`;
            }
            // Note: versionId (S3 file version) is NOT passed for Cesium — tileset viewers
            // require all files from the same version context, which only assetVersionId provides
            return url;
        },
        [config, databaseId, assetId, assetVersionId]
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

                    // Create widget with error handling; UI controls (home, scene mode,
                    // fullscreen, picked-feature info) are provided by the custom panel below.
                    // Render errors surface through the VAMS error banner instead of the
                    // widget's blocking overlay panel
                    const widgetOptions: any = { showRenderLoopErrors: false };

                    // Without an Ion token there is no imagery to show — skip the default
                    // base layer (avoids Ion request errors) and hide the globe surface,
                    // atmosphere, and skybox/sun/moon so the model renders against the
                    // configurable background color instead of a blue ellipsoid or starfield
                    if (!hasValidToken) {
                        widgetOptions.baseLayer = false;
                    }

                    viewerRef.current = new Cesium.CesiumWidget(
                        cesiumContainer.current!,
                        widgetOptions
                    );

                    // Globe/sky visibility is decided per tileset once content loads
                    // (shown for geo-referenced content, hidden for local-coordinate
                    // models); start hidden so nothing occludes the model meanwhile
                    const initScene = viewerRef.current.scene;
                    initScene.globe.show = false;
                    if (initScene.skyAtmosphere) initScene.skyAtmosphere.show = false;
                    if (initScene.skyBox) initScene.skyBox.show = false;
                    if (initScene.sun) initScene.sun.show = false;
                    if (initScene.moon) initScene.moon.show = false;
                    initScene.backgroundColor = Cesium.Color.fromCssColorString(backgroundColor);
                    if (!hasValidToken) {
                        // No imagery without an Ion token — use a neutral surface color
                        // for geographic context instead of the default bright blue
                        initScene.globe.baseColor = Cesium.Color.fromCssColorString("#2a2d33");
                    }

                    // Add error event listeners; the widget stops its render loop on a
                    // render error, so recover by snapping back to 3D and restarting it
                    viewerRef.current.scene.renderError.addEventListener(
                        (scene: any, error: any) => {
                            console.error("Cesium render error:", error);
                            setInitError(`Render error: ${error.message || error}`);
                            try {
                                if (viewerRef.current) {
                                    viewerRef.current.scene.morphTo3D(0);
                                    setSceneMode("3d");
                                    viewerRef.current.useDefaultRenderLoop = true;
                                }
                            } catch (recoveryError) {
                                console.warn("Cesium render loop recovery failed:", recoveryError);
                            }
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
    const configureCameraForTileset = useCallback((tileset: any, geolocated: boolean) => {
        if (!viewerRef.current) return;

        const viewer = viewerRef.current;
        const scene = viewer.scene;
        const controller = scene.screenSpaceCameraController;
        const boundingSphere = tileset.boundingSphere;
        const radius = boundingSphere.radius;

        if (geolocated) {
            // Globe-referenced content: allow close inspection of the model but keep
            // the maximum zoom at globe scale so the camera can pull out for context
            controller.minimumZoomDistance = Math.max(radius * 0.01, 0.5);
            controller.maximumZoomDistance = 50000000.0;
            controller.minimumCollisionTerrainHeight = 15000;
        } else if (radius < 100) {
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

        // Terrain collision only makes sense for globe-relative content; for a
        // local-coordinate model it clamps the camera against an ellipsoid the
        // model does not sit on, blocking movement and hiding the model
        controller.enableCollisionDetection = geolocated;

        // The globe and atmosphere provide geographic context for globe-referenced
        // content but occlude local-coordinate models sitting at the earth's center
        scene.globe.show = geolocated;
        if (scene.skyAtmosphere) {
            scene.skyAtmosphere.show = geolocated;
        }
        if (scene.skyBox) {
            scene.skyBox.show = geolocated;
        }
        if (scene.sun) {
            scene.sun.show = geolocated;
        }
        if (scene.moon) {
            scene.moon.show = geolocated;
        }

        // Globe-referenced content uses free globe controls; release any turntable
        // lookAt lock a previously viewed local-coordinate tileset left behind
        if (geolocated) {
            viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
        }

        console.log(
            `Configured camera for ${
                geolocated ? "geo-referenced" : "local-coordinate"
            } tileset with radius: ${radius.toFixed(2)}m`
        );
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

    // Geo-located content sits within ~100km of the WGS84 surface; local-coordinate
    // models sit near the earth's center instead
    const isTilesetGeolocated = useCallback((tileset: any) => {
        const center = tileset?.boundingSphere?.center;
        if (!center) return false;
        const magnitude = Cesium.Cartesian3.magnitude(center);
        return Math.abs(magnitude - 6378137.0) < 100000.0;
    }, []);

    // Point the camera at a tileset. Geo-located tilesets fly with globe-relative
    // controls; local-coordinate models get a lookAt transform so the camera orbits
    // and zooms around the model itself (turntable controls) instead of the globe
    const focusCameraOnTileset = useCallback(
        (tileset: any, offset?: any) => {
            if (!viewerRef.current) return;

            const cameraOffset = offset || createCameraOffset(tileset);
            if (isTilesetGeolocated(tileset)) {
                viewerRef.current.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
                viewerRef.current.zoomTo(tileset, cameraOffset);
            } else {
                viewerRef.current.camera.lookAt(tileset.boundingSphere.center, cameraOffset);
            }
        },
        [createCameraOffset, isTilesetGeolocated]
    );

    // Zoom the camera to an individual tile within a tileset (scene graph double-click)
    const zoomToTile = useCallback(
        (tile: any) => {
            if (!viewerRef.current || !tile?.boundingSphere) return;

            const sphere = tile.boundingSphere;
            const range = Math.max(sphere.radius * 2.5, 5);
            const offset = new Cesium.HeadingPitchRange(
                Cesium.Math.toRadians(30),
                Cesium.Math.toRadians(-30),
                range
            );
            if (allTilesetsGeolocated) {
                viewerRef.current.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
                viewerRef.current.camera.flyToBoundingSphere(sphere, {
                    duration: 1.0,
                    offset,
                });
            } else {
                viewerRef.current.camera.lookAt(sphere.center, offset);
            }
        },
        [allTilesetsGeolocated]
    );

    // Per-tile visibility/selection uses tile color (Cesium has no per-tile show):
    // alpha 0 hides, yellow tint highlights the selection, white is default
    const applyTileAppearance = useCallback((tile: any) => {
        if (hiddenTilesRef.current.has(tile)) {
            tile.color = Cesium.Color.WHITE.withAlpha(0.0);
        } else if (selectedTilesRef.current.has(tile)) {
            tile.color = Cesium.Color.YELLOW.withAlpha(0.85);
        } else {
            tile.color = Cesium.Color.WHITE;
        }
    }, []);

    const forEachTileInSubtree = useCallback((tile: any, fn: (t: any) => void) => {
        fn(tile);
        if (tile.children) {
            tile.children.forEach((child: any) => forEachTileInSubtree(child, fn));
        }
    }, []);

    const selectTiles = useCallback(
        (tiles: any[]) => {
            const previous = Array.from(selectedTilesRef.current);
            selectedTilesRef.current = new Set(tiles);
            previous.forEach(applyTileAppearance);
            tiles.forEach(applyTileAppearance);
            setSelectedTiles(tiles);
        },
        [applyTileAppearance]
    );

    const handleTileGraphClick = useCallback(
        (tile: any, ctrlKey: boolean) => {
            const isSelected = selectedTilesRef.current.has(tile);
            if (ctrlKey) {
                const next = Array.from(selectedTilesRef.current);
                selectTiles(isSelected ? next.filter((t) => t !== tile) : [...next, tile]);
            } else {
                selectTiles(isSelected && selectedTilesRef.current.size === 1 ? [] : [tile]);
            }
        },
        [selectTiles]
    );

    const toggleTileVisibility = useCallback(
        (tile: any) => {
            const hide = !hiddenTilesRef.current.has(tile);
            forEachTileInSubtree(tile, (t) => {
                if (hide) {
                    hiddenTilesRef.current.add(t);
                } else {
                    hiddenTilesRef.current.delete(t);
                }
                applyTileAppearance(t);
            });
            setSceneVersion((v) => v + 1);
        },
        [forEachTileInSubtree, applyTileAppearance]
    );

    const isTileHidden = useCallback((tile: any) => hiddenTilesRef.current.has(tile), []);

    const setAllTilesVisibility = useCallback(
        (visible: boolean) => {
            loadedTilesets.forEach((tileset) => {
                tileset.show = visible;
                if (visible && tileset.root) {
                    // Also clear any per-tile hides when showing all
                    forEachTileInSubtree(tileset.root, (t) => {
                        hiddenTilesRef.current.delete(t);
                        applyTileAppearance(t);
                    });
                }
            });
            setSceneVersion((v) => v + 1);
        },
        [loadedTilesets, forEachTileInSubtree, applyTileAppearance]
    );

    // Re-apply appearance to tiles as they stream in after a hide/selection
    useEffect(() => {
        const removers: Array<() => void> = [];
        loadedTilesets.forEach((tileset) => {
            const onTileVisible = (tile: any) => {
                if (hiddenTilesRef.current.has(tile) || selectedTilesRef.current.has(tile)) {
                    applyTileAppearance(tile);
                }
            };
            tileset.tileVisible.addEventListener(onTileVisible);
            removers.push(() => tileset.tileVisible.removeEventListener(onTileVisible));
        });
        return () => removers.forEach((remove) => remove());
    }, [loadedTilesets, applyTileAppearance]);

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

        viewerRef.current.scene.shadowMap.enabled = newShadowsEnabled;

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
            focusCameraOnTileset(tileset, offset);

            // Don't persist the view mode - just temporarily highlight during animation
            setCurrentViewMode(viewType);
            setTimeout(() => setCurrentViewMode("perspective"), 1000);

            console.log(`Camera set to ${viewType} view`);
        },
        [loadedTilesets, focusCameraOnTileset]
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
        viewerRef.current.scene.shadowMap.enabled = false;
        viewerRef.current.scene.globe.material = undefined;

        // Reset tileset visibility state
        const resetVisibility: Record<number, boolean> = {};
        loadedTilesets.forEach((_, index) => {
            resetVisibility[index] = true;
        });
        setTilesetVisibility(resetVisibility);

        // Reset camera to initial position
        focusCameraOnTileset(loadedTilesets[0]);
        setCurrentViewMode("perspective");

        console.log("Scene reset to default settings");
    }, [loadedTilesets, focusCameraOnTileset, clearMeasurements]);

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

    const flyHome = useCallback(() => {
        if (!viewerRef.current) return;

        if (loadedTilesets.length > 0) {
            focusCameraOnTileset(loadedTilesets[0]);
        } else {
            viewerRef.current.camera.flyHome(1.5);
        }
    }, [loadedTilesets, focusCameraOnTileset]);

    const changeSceneMode = useCallback(
        (mode: "3d" | "2d" | "columbus") => {
            if (!viewerRef.current) return;

            // 2D/Columbus projection requires geo-located content; local-coordinate
            // tilesets produce NaN positions when projected, crashing the render loop
            if (mode !== "3d" && !allTilesetsGeolocated) {
                return;
            }

            const scene = viewerRef.current.scene;
            if (mode === "2d") {
                scene.morphTo2D(1.0);
            } else if (mode === "columbus") {
                scene.morphToColumbusView(1.0);
            } else {
                scene.morphTo3D(1.0);
            }
            setSceneMode(mode);
        },
        [allTilesetsGeolocated]
    );

    const toggleFullscreen = useCallback(() => {
        if (Cesium.Fullscreen.fullscreen) {
            Cesium.Fullscreen.exitFullscreen();
        } else if (cesiumContainer.current) {
            Cesium.Fullscreen.requestFullscreen(cesiumContainer.current.parentElement);
        }
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
            const pickedPosition = viewerRef.current!.scene.pick(event.position);

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

    // Scene picking: selects the picked tile in the scene graph (with highlight)
    // and shows feature properties in the info panel (disabled while measuring)
    useEffect(() => {
        if (!viewerRef.current || !cesiumLoaded || measurementMode !== "none") return;

        const handler = new Cesium.ScreenSpaceEventHandler(viewerRef.current.scene.canvas);

        handler.setInputAction((event: any) => {
            const picked = viewerRef.current!.scene.pick(event.position);

            // Resolve the Cesium3DTile that owns whatever was picked
            const pickedTile =
                picked?.content?.tile || // model/content picks
                (picked instanceof Cesium.Cesium3DTileFeature
                    ? (picked as any).content?.tile
                    : undefined);

            if (pickedTile) {
                handleTileGraphClick(pickedTile, false);
            } else if (!picked) {
                selectTiles([]);
            }

            if (picked && picked instanceof Cesium.Cesium3DTileFeature) {
                const properties: Array<[string, string]> = [];
                const propertyIds = picked.getPropertyIds();
                propertyIds.forEach((propertyId: string) => {
                    const value = picked.getProperty(propertyId);
                    if (value !== undefined && value !== null) {
                        properties.push([propertyId, String(value)]);
                    }
                });
                setPickedFeatureInfo({
                    title: picked.getProperty("name") || "Feature",
                    properties,
                });
            } else {
                setPickedFeatureInfo(null);
            }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

        return () => {
            handler.destroy();
        };
    }, [measurementMode, cesiumLoaded, loadedTilesets, handleTileGraphClick, selectTiles]);

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
                tileset.vamsFileKey = key;

                // Add tileset to scene
                viewerRef.current.scene.primitives.add(tileset);

                console.log(`Successfully loaded tileset: ${key}`);

                // Configure camera and zoom after a short delay to allow tileset to initialize
                setTimeout(() => {
                    if (viewerRef.current && tileset.boundingSphere) {
                        try {
                            const geolocated = isTilesetGeolocated(tileset);
                            setAllTilesetsGeolocated(geolocated);

                            // Configure camera controller for this tileset
                            configureCameraForTileset(tileset, geolocated);

                            // Create appropriate camera offset
                            const cameraOffset = createCameraOffset(tileset);

                            // Point camera at tileset (turntable orbit for local models)
                            focusCameraOnTileset(tileset, cameraOffset);

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
            isTilesetGeolocated,
            focusCameraOnTileset,
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
                    tileset.vamsFileKey = key;

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
                                const geolocated = tilesets.every(isTilesetGeolocated);
                                setAllTilesetsGeolocated(geolocated);

                                // Configure camera controller for the primary tileset
                                configureCameraForTileset(primaryTileset, geolocated);

                                // Create appropriate camera offset
                                const cameraOffset = createCameraOffset(primaryTileset);

                                // Point camera at tileset (turntable orbit for local models)
                                focusCameraOnTileset(primaryTileset, cameraOffset);

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
        [
            config,
            getAuthHeaders,
            constructStreamingUrl,
            configureCameraForTileset,
            createCameraOffset,
            isTilesetGeolocated,
            focusCameraOnTileset,
        ]
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
    }, [
        assetId,
        assetKey,
        multiFileKeys,
        databaseId,
        versionId,
        assetVersionId,
        config,
        viewerRef.current,
    ]);

    if (error) {
        return (
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    height: "100%",
                    padding: "20px",
                    backgroundColor: "var(--vams-bg-secondary)",
                }}
            >
                <div style={{ textAlign: "center" }}>
                    <h3 style={{ color: "var(--vams-color-error)", marginBottom: "10px" }}>
                        Error Loading 3D Tileset
                    </h3>
                    <p style={{ color: "var(--vams-text-secondary)" }}>{error}</p>
                    <p
                        style={{
                            color: "var(--vams-text-secondary)",
                            fontSize: "0.9em",
                            marginTop: "10px",
                        }}
                    >
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
                    backgroundColor: "var(--vams-bg-secondary)",
                }}
            >
                <div style={{ textAlign: "center" }}>
                    <h3 style={{ color: "var(--vams-text-secondary)", marginBottom: "10px" }}>
                        Loading Configuration...
                    </h3>
                    <p style={{ color: "var(--vams-text-secondary)", fontSize: "0.9em" }}>
                        Waiting for VAMS configuration to load
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div style={{ position: "relative", width: "100%", height: "100%" }}>
            {/* File version warning — Cesium only supports assetVersionId, not individual file versions */}
            {versionId && !assetVersionId && !dismissedVersionWarning && (
                <div
                    style={{
                        position: "absolute",
                        top: "0",
                        left: "0",
                        right: "0",
                        backgroundColor: "#e8f4fd",
                        border: "1px solid #0972d3",
                        borderRadius: "4px",
                        padding: "8px 12px",
                        margin: "8px",
                        zIndex: 1001,
                        fontSize: "0.85em",
                        color: "#0972d3",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                    }}
                >
                    <span style={{ textAlign: "center", flex: 1 }}>
                        Specific file versions cannot be viewed except for when looking at files
                        under an asset version ID. Viewing the latest version of this file.
                    </span>
                    <button
                        onClick={() => setDismissedVersionWarning(true)}
                        style={{
                            background: "none",
                            border: "none",
                            color: "#0972d3",
                            cursor: "pointer",
                            fontSize: "1.1em",
                            fontWeight: "bold",
                            padding: "0 0 0 12px",
                        }}
                    >
                        ×
                    </button>
                </div>
            )}

            {/* Display initialization errors at the top */}
            {initError && (
                <div
                    style={{
                        position: "absolute",
                        top: "0",
                        left: "0",
                        right: "0",
                        backgroundColor: "var(--vams-bg-secondary)",
                        border: "1px solid var(--vams-color-error)",
                        borderRadius: "4px",
                        padding: "12px 16px",
                        margin: "8px",
                        zIndex: 1001,
                        fontSize: "0.9em",
                    }}
                >
                    <div
                        style={{
                            color: "var(--vams-color-error)",
                            fontWeight: "bold",
                            marginBottom: "4px",
                        }}
                    >
                        Cesium Initialization Error
                    </div>
                    <div style={{ color: "var(--vams-text-secondary)" }}>{initError}</div>
                    <button
                        onClick={() => setInitError(null)}
                        style={{
                            position: "absolute",
                            top: "8px",
                            right: "8px",
                            background: "none",
                            border: "none",
                            color: "var(--vams-color-error)",
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
                        backgroundColor:
                            "color-mix(in srgb, var(--vams-bg-primary) 90%, transparent)",
                        padding: "20px",
                        borderRadius: "8px",
                        textAlign: "center",
                    }}
                >
                    <div>Loading 3D Tileset...</div>
                    {multiFileKeys && multiFileKeys.length > 1 && (
                        <div
                            style={{
                                fontSize: "0.9em",
                                color: "var(--vams-text-secondary)",
                                marginTop: "5px",
                            }}
                        >
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

            {/* Scene Panel (tabbed: Scene Graph / Controls) */}
            {viewerRef.current && showControls && (
                <div
                    style={{
                        position: "fixed",
                        top: initError ? "50px" : "20px",
                        left: "10px",
                        bottom: "20px",
                        backgroundColor: "rgba(0, 0, 0, 0.85)",
                        color: "white",
                        borderRadius: "8px",
                        fontSize: "0.85em",
                        zIndex: 1000,
                        minWidth: "280px",
                        maxWidth: "320px",
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                    }}
                >
                    {/* Header with Tabs */}
                    <div
                        style={{
                            display: "flex",
                            alignItems: "center",
                            borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
                        }}
                    >
                        <button
                            onClick={() => setShowControls(false)}
                            style={{
                                background: "none",
                                border: "none",
                                color: "white",
                                cursor: "pointer",
                                fontSize: "16px",
                                padding: "16px 12px",
                                width: "auto",
                                height: "auto",
                            }}
                            title="Hide panel"
                        >
                            ×
                        </button>
                        <div style={{ display: "flex", flex: 1, overflowX: "auto" }}>
                            <button
                                onClick={() => setActivePanelTab("sceneGraph")}
                                style={{
                                    flex: 1,
                                    minWidth: "70px",
                                    background:
                                        activePanelTab === "sceneGraph"
                                            ? "rgba(76, 175, 80, 0.3)"
                                            : "transparent",
                                    border: "none",
                                    borderBottom:
                                        activePanelTab === "sceneGraph"
                                            ? "2px solid #4CAF50"
                                            : "2px solid transparent",
                                    color: "white",
                                    padding: "12px 8px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                    fontWeight: activePanelTab === "sceneGraph" ? "bold" : "normal",
                                }}
                                title="Scene Graph"
                            >
                                🌳
                            </button>
                            <button
                                onClick={() => setActivePanelTab("controls")}
                                style={{
                                    flex: 1,
                                    minWidth: "70px",
                                    background:
                                        activePanelTab === "controls"
                                            ? "rgba(33, 150, 243, 0.3)"
                                            : "transparent",
                                    border: "none",
                                    borderBottom:
                                        activePanelTab === "controls"
                                            ? "2px solid #2196F3"
                                            : "2px solid transparent",
                                    color: "white",
                                    padding: "12px 8px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                    fontWeight: activePanelTab === "controls" ? "bold" : "normal",
                                }}
                                title="Controls"
                            >
                                ⚙️
                            </button>
                        </div>
                    </div>

                    {/* Scene Graph Tab */}
                    {activePanelTab === "sceneGraph" && (
                        <CesiumSceneGraph
                            tilesets={loadedTilesets}
                            selectedTiles={selectedTiles}
                            sceneVersion={sceneVersion}
                            isTileHidden={isTileHidden}
                            onTileClick={handleTileGraphClick}
                            onClearSelection={() => selectTiles([])}
                            onToggleTileVisibility={toggleTileVisibility}
                            onSetAllVisibility={setAllTilesVisibility}
                            onZoomToTile={zoomToTile}
                        />
                    )}

                    {/* Controls Tab */}
                    {activePanelTab === "controls" && (
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
                            {/* View Controls */}
                            <div style={{ marginBottom: "16px" }}>
                                <h5
                                    style={{
                                        margin: "0 0 8px 0",
                                        fontSize: "0.9em",
                                        color: "#ccc",
                                    }}
                                >
                                    Camera Views
                                </h5>
                                <div
                                    style={{
                                        display: "grid",
                                        gridTemplateColumns: "1fr 1fr",
                                        gap: "4px",
                                    }}
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
                                <button
                                    onClick={flyHome}
                                    style={{
                                        background: "rgba(255, 255, 255, 0.1)",
                                        border: "1px solid rgba(255, 255, 255, 0.2)",
                                        color: "white",
                                        padding: "6px 8px",
                                        borderRadius: "4px",
                                        cursor: "pointer",
                                        fontSize: "0.8em",
                                        width: "100%",
                                        marginTop: "4px",
                                    }}
                                    title="Reset camera to home view"
                                >
                                    🏠 Home
                                </button>
                            </div>

                            {/* Scene Mode — 2D/2.5D projections only work for geo-located tilesets */}
                            {allTilesetsGeolocated && (
                                <div style={{ marginBottom: "16px" }}>
                                    <h5
                                        style={{
                                            margin: "0 0 8px 0",
                                            fontSize: "0.9em",
                                            color: "#ccc",
                                        }}
                                    >
                                        Scene Mode
                                    </h5>
                                    <div
                                        style={{
                                            display: "grid",
                                            gridTemplateColumns: "1fr 1fr 1fr",
                                            gap: "4px",
                                        }}
                                    >
                                        {(
                                            [
                                                { mode: "3d", label: "3D" },
                                                { mode: "2d", label: "2D" },
                                                { mode: "columbus", label: "2.5D" },
                                            ] as const
                                        ).map(({ mode, label }) => (
                                            <button
                                                key={mode}
                                                onClick={() => changeSceneMode(mode)}
                                                style={{
                                                    background:
                                                        sceneMode === mode
                                                            ? "#4CAF50"
                                                            : "rgba(255, 255, 255, 0.1)",
                                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                                    color: "white",
                                                    padding: "6px 8px",
                                                    borderRadius: "4px",
                                                    cursor: "pointer",
                                                    fontSize: "0.8em",
                                                }}
                                            >
                                                {label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Rendering Controls */}
                            <div style={{ marginBottom: "16px" }}>
                                <h5
                                    style={{
                                        margin: "0 0 8px 0",
                                        fontSize: "0.9em",
                                        color: "#ccc",
                                    }}
                                >
                                    Rendering
                                </h5>
                                <div
                                    style={{ display: "flex", flexDirection: "column", gap: "6px" }}
                                >
                                    <label
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            cursor: "pointer",
                                        }}
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
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            cursor: "pointer",
                                        }}
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
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            cursor: "pointer",
                                        }}
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
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            cursor: "pointer",
                                        }}
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
                                    <h6
                                        style={{
                                            margin: "0 0 6px 0",
                                            fontSize: "0.8em",
                                            color: "#ddd",
                                        }}
                                    >
                                        Background:
                                    </h6>
                                    <div
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            gap: "4px",
                                        }}
                                    >
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
                                                                color === "#ffffff" ||
                                                                color === "#87ceeb"
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
                                    <h5
                                        style={{
                                            margin: "0 0 8px 0",
                                            fontSize: "0.9em",
                                            color: "#ccc",
                                        }}
                                    >
                                        Tilesets
                                    </h5>
                                    <div
                                        style={{
                                            display: "flex",
                                            flexDirection: "column",
                                            gap: "4px",
                                        }}
                                    >
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
                                <h5
                                    style={{
                                        margin: "0 0 8px 0",
                                        fontSize: "0.9em",
                                        color: "#ccc",
                                    }}
                                >
                                    Measurements
                                </h5>
                                <div
                                    style={{ display: "flex", flexDirection: "column", gap: "6px" }}
                                >
                                    <button
                                        onClick={() => startMeasurement("distance")}
                                        style={{
                                            background:
                                                measurementMode === "distance"
                                                    ? "#4CAF50"
                                                    : "#9C27B0",
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
                                            background:
                                                measurementMode === "area" ? "#4CAF50" : "#9C27B0",
                                            border: "none",
                                            color: "white",
                                            padding: "8px 12px",
                                            borderRadius: "4px",
                                            cursor: "pointer",
                                            fontSize: "0.8em",
                                        }}
                                        disabled={measurementMode === "area"}
                                    >
                                        📐{" "}
                                        {measurementMode === "area"
                                            ? "Click 3+ points"
                                            : "Measure Area"}
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
                                                    (
                                                    {result.type === "distance"
                                                        ? "Distance"
                                                        : "Area"}{" "}
                                                    #{index + 1})
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Action Buttons */}
                            <div style={{ marginBottom: "16px" }}>
                                <h5
                                    style={{
                                        margin: "0 0 8px 0",
                                        fontSize: "0.9em",
                                        color: "#ccc",
                                    }}
                                >
                                    Actions
                                </h5>
                                <div
                                    style={{ display: "flex", flexDirection: "column", gap: "6px" }}
                                >
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
                                    <button
                                        onClick={toggleFullscreen}
                                        style={{
                                            background: "#607D8B",
                                            border: "none",
                                            color: "white",
                                            padding: "8px 12px",
                                            borderRadius: "4px",
                                            cursor: "pointer",
                                            fontSize: "0.8em",
                                        }}
                                    >
                                        ⛶ Fullscreen
                                    </button>
                                </div>
                            </div>

                            {/* Performance Stats */}
                            {performanceStats && (
                                <div>
                                    <h5
                                        style={{
                                            margin: "0 0 8px 0",
                                            fontSize: "0.9em",
                                            color: "#ccc",
                                        }}
                                    >
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

            {/* Picked feature info panel */}
            {pickedFeatureInfo && (
                <div
                    style={{
                        position: "absolute",
                        top: "20px",
                        right: "10px",
                        backgroundColor: "rgba(0, 0, 0, 0.8)",
                        color: "white",
                        padding: "12px 16px",
                        borderRadius: "8px",
                        fontSize: "0.85em",
                        zIndex: 1000,
                        maxWidth: "300px",
                        maxHeight: "50%",
                        overflowY: "auto",
                    }}
                >
                    <div
                        style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            marginBottom: "8px",
                        }}
                    >
                        <strong>{pickedFeatureInfo.title}</strong>
                        <button
                            onClick={() => setPickedFeatureInfo(null)}
                            style={{
                                background: "none",
                                border: "none",
                                color: "white",
                                cursor: "pointer",
                                fontSize: "14px",
                                padding: "0 0 0 12px",
                            }}
                            title="Close"
                        >
                            ×
                        </button>
                    </div>
                    {pickedFeatureInfo.properties.length === 0 ? (
                        <div style={{ color: "#ccc" }}>No properties</div>
                    ) : (
                        pickedFeatureInfo.properties.map(([key, value]) => (
                            <div key={key} style={{ marginBottom: "4px" }}>
                                <span style={{ color: "#aaa" }}>{key}: </span>
                                <span>{value}</span>
                            </div>
                        ))
                    )}
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
