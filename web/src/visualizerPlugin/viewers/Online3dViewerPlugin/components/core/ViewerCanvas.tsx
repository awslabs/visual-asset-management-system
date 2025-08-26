/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useCallback } from "react";
import { useViewerContext } from "../../context/ViewerContext";

export const ViewerCanvas: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<any>(null);
    const modelLoadedRef = useRef<boolean>(false);
    const ovLibRef = useRef<any>(null);
    const { state, settings, cameraSettings, updateState, selection, setSelection } =
        useViewerContext();

    // Initialize the viewer using EmbeddedViewer like the working implementation
    const initializeViewer = useCallback(async () => {
        if (!containerRef.current || state.viewerInitialized) {
            return;
        }

        try {
            console.log("Initializing Online3DViewer EmbeddedViewer...");

            // Import the online-3d-viewer engine
            const OV = await import("online-3d-viewer");
            ovLibRef.current = OV;

            // Make OV globally available for other components
            window.OV = OV;

            // Create EmbeddedViewer instance exactly like the working implementation
            const viewer = new OV.EmbeddedViewer(containerRef.current, {
                backgroundColor: new OV.RGBAColor(
                    settings.backgroundColor.r,
                    settings.backgroundColor.g,
                    settings.backgroundColor.b,
                    255
                ),
                defaultColor: new OV.RGBColor(
                    settings.defaultColor.r,
                    settings.defaultColor.g,
                    settings.defaultColor.b
                ),
                edgeSettings: new OV.EdgeSettings(
                    settings.showEdges,
                    new OV.RGBColor(
                        settings.edgeSettings.edgeColor.r,
                        settings.edgeSettings.edgeColor.g,
                        settings.edgeSettings.edgeColor.b
                    ),
                    settings.edgeSettings.edgeThreshold
                ),
                onModelLoaded: () => {
                    console.log("Model loaded successfully in EmbeddedViewer");
                    const model = viewer.GetModel();

                    // Get current state to preserve file information
                    const currentModel = state.model;

                    updateState({
                        model: {
                            loaded: true,
                            files: currentModel?.files || ["Model"], // Keep existing file names from container
                            urls: currentModel?.urls || [],
                            fileNames: currentModel?.fileNames || ["Model"],
                            ovModel: model, // Store the actual OV model
                            // Add methods that panels expect
                            MaterialCount: () => {
                                try {
                                    return model?.MaterialCount ? model.MaterialCount() : 0;
                                } catch (error) {
                                    console.warn("Error getting material count:", error);
                                    return 0;
                                }
                            },
                            GetMaterial: (index: number) => {
                                try {
                                    return model?.GetMaterial ? model.GetMaterial(index) : null;
                                } catch (error) {
                                    console.warn("Error getting material:", error);
                                    return null;
                                }
                            },
                            MeshCount: () => {
                                try {
                                    return model?.MeshCount ? model.MeshCount() : 0;
                                } catch (error) {
                                    console.warn("Error getting mesh count:", error);
                                    return 0;
                                }
                            },
                            GetMesh: (index: number) => {
                                try {
                                    return model?.GetMesh ? model.GetMesh(index) : null;
                                } catch (error) {
                                    console.warn("Error getting mesh:", error);
                                    return null;
                                }
                            },
                        },
                        isLoading: false,
                    });
                    modelLoadedRef.current = true;
                },
            });

            viewerRef.current = viewer;

            // Create a comprehensive viewer object that exposes all necessary methods
            const viewerWrapper = {
                embeddedViewer: viewer,
                viewer: viewer.GetViewer(),
                LoadModelFromUrlList: (urls: string[]) => viewer.LoadModelFromUrlList(urls),
                GetViewer: () => viewer.GetViewer(),
                GetModel: () => viewer.GetModel(),
                // Expose OV library for color and settings creation
                OV: OV,
            };

            updateState({
                viewer: viewerWrapper,
                viewerInitialized: true,
                scriptsLoaded: true,
            });

            console.log("Online3DViewer EmbeddedViewer initialized successfully");
        } catch (error) {
            console.error("Error initializing Online3DViewer:", error);
            updateState({
                error: "Failed to initialize 3D viewer",
                viewerInitialized: false,
            });
        }
    }, [state.viewerInitialized, settings, updateState]);

    // Load model into viewer - this is called from the container
    const loadModelIntoViewer = useCallback(
        (modelUrls: string[]) => {
            if (!viewerRef.current) {
                return;
            }

            try {
                console.log("Loading model into EmbeddedViewer:", modelUrls);

                // Reset the model loaded flag to allow new loading
                modelLoadedRef.current = false;

                updateState({ isLoading: true });

                // Use the EmbeddedViewer's LoadModelFromUrlList method
                viewerRef.current.LoadModelFromUrlList(modelUrls);
            } catch (error) {
                console.error("Error loading model into viewer:", error);
                updateState({
                    error: "Failed to load model into viewer",
                    isLoading: false,
                });
            }
        },
        [updateState]
    );
    // Reset model loaded flag when viewer is cleared
    useEffect(() => {
        if (!state.model?.loaded) {
            modelLoadedRef.current = false;
        }
    }, [state.model?.loaded]);

    // Update viewer settings when context settings change
    useEffect(() => {
        if (viewerRef.current && ovLibRef.current && state.viewerInitialized) {
            const viewer = viewerRef.current.GetViewer();
            if (viewer) {
                try {
                    // Update background color
                    const bgColor = new ovLibRef.current.RGBAColor(
                        settings.backgroundColor.r,
                        settings.backgroundColor.g,
                        settings.backgroundColor.b,
                        255
                    );
                    viewer.SetBackgroundColor(bgColor);

                    // Update edge settings
                    const edgeColor = new ovLibRef.current.RGBColor(
                        settings.edgeSettings.edgeColor.r,
                        settings.edgeSettings.edgeColor.g,
                        settings.edgeSettings.edgeColor.b
                    );
                    const edgeSettings = new ovLibRef.current.EdgeSettings(
                        settings.showEdges,
                        edgeColor,
                        settings.edgeSettings.edgeThreshold
                    );
                    viewer.SetEdgeSettings(edgeSettings);

                    viewer.Render();
                } catch (error) {
                    console.warn("Error updating viewer settings:", error);
                }
            }
        }
    }, [settings, state.viewerInitialized]);

    // Expose viewer methods to parent components
    useEffect(() => {
        if (viewerRef.current && state.viewerInitialized && ovLibRef.current) {
            // Store the load function and expose the underlying viewer
            (viewerRef.current as any).loadModelUrls = loadModelIntoViewer;

            // Update context with comprehensive viewer wrapper
            const underlyingViewer = viewerRef.current.GetViewer();
            const viewerWrapper = {
                embeddedViewer: viewerRef.current,
                viewer: underlyingViewer,
                LoadModelFromUrlList: (urls: string[]) =>
                    viewerRef.current.LoadModelFromUrlList(urls),
                GetViewer: () => viewerRef.current.GetViewer(),
                GetModel: () => viewerRef.current.GetModel(),
                OV: ovLibRef.current,
            };

            updateState({
                viewer: viewerWrapper,
            });
        }
    }, [state.viewerInitialized, loadModelIntoViewer, updateState]);

    // Initialize viewer on mount
    useEffect(() => {
        initializeViewer();
    }, [initializeViewer]);

    // Handle window resize
    useEffect(() => {
        const handleResize = () => {
            if (viewerRef.current) {
                try {
                    viewerRef.current.Resize();
                } catch (error) {
                    console.warn("Resize method not available on EmbeddedViewer");
                }
            }
        };

        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, []);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (viewerRef.current) {
                try {
                    viewerRef.current.Destroy();
                } catch (error) {
                    console.error("Error during viewer cleanup:", error);
                }
            }
        };
    }, []);

    return (
        <div className="ov-viewer-container">
            <div
                ref={containerRef}
                className="online_3d_viewer ov-viewer-canvas"
                style={{
                    width: "100%",
                    height: "100%",
                    display: "block",
                    outline: "none",
                }}
            />
        </div>
    );
};
