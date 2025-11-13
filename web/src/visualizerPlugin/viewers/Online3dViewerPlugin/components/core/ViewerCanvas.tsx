/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useCallback } from "react";
import { useViewerContext } from "../../context/ViewerContext";
import { Online3dViewerDependencyManager } from "../../dependencies";

// Declare the global OV object
declare const OV: any;

export const ViewerCanvas: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<any>(null);
    const modelLoadedRef = useRef<boolean>(false);
    const ovLibRef = useRef<any>(null);
    const pendingFilesRef = useRef<string[]>([]); // Store files from container
    const { state, settings, cameraSettings, updateState, selection, setSelection } =
        useViewerContext();

    // Initialize the viewer using EmbeddedViewer like the working implementation
    const initializeViewer = useCallback(async () => {
        if (!containerRef.current || state.viewerInitialized) {
            return;
        }

        try {
            console.log("Initializing Online3DViewer EmbeddedViewer...");

            // Load the online-3d-viewer library dynamically
            await Online3dViewerDependencyManager.loadOnline3dViewer();

            // Get OV from window
            const OVLib = (window as any).OV;
            if (!OVLib) {
                throw new Error("OV library not loaded");
            }
            ovLibRef.current = OVLib;

            // Create EmbeddedViewer instance exactly like the working implementation
            const viewer = new OVLib.EmbeddedViewer(containerRef.current, {
                backgroundColor: new OVLib.RGBAColor(
                    settings.backgroundColor.r,
                    settings.backgroundColor.g,
                    settings.backgroundColor.b,
                    255
                ),
                defaultColor: new OVLib.RGBColor(
                    settings.defaultColor.r,
                    settings.defaultColor.g,
                    settings.defaultColor.b
                ),
                edgeSettings: new OVLib.EdgeSettings(
                    settings.showEdges,
                    new OVLib.RGBColor(
                        settings.edgeSettings.edgeColor.r,
                        settings.edgeSettings.edgeColor.g,
                        settings.edgeSettings.edgeColor.b
                    ),
                    settings.edgeSettings.edgeThreshold
                ),
                onModelLoaded: () => {
                    console.log("Model loaded successfully in EmbeddedViewer");
                    const model = viewer.GetModel();

                    // Use files from ref (set by container) instead of state
                    const preservedFiles =
                        pendingFilesRef.current.length > 0
                            ? pendingFilesRef.current
                            : state.model?.files || state.model?.fileNames || [];

                    console.log("ViewerCanvas onModelLoaded - preserving files:", preservedFiles);
                    console.log("Files from ref:", pendingFilesRef.current);
                    console.log("Files from state:", state.model?.files);

                    updateState({
                        model: {
                            loaded: true,
                            files: preservedFiles, // Preserve actual file names from container
                            urls: state.model?.urls || [],
                            fileNames: preservedFiles, // Use same as files for consistency
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

            // Set up click event handler for mesh selection on the underlying viewer
            const underlyingViewer = viewer.GetViewer();
            if (underlyingViewer && (underlyingViewer as any).SetMouseClickHandler) {
                (underlyingViewer as any).SetMouseClickHandler(
                    (button: number, mouseCoordinates: any) => {
                        if (button === 1) {
                            // Left click
                            // Use GetMeshUserDataUnderMouse with IntersectionMode like the reference implementation
                            const IntersectionMode = { MeshOnly: 1, MeshAndLine: 2 };
                            const meshUserData = (underlyingViewer as any).GetMeshUserDataUnderMouse
                                ? (underlyingViewer as any).GetMeshUserDataUnderMouse(
                                      IntersectionMode.MeshAndLine,
                                      mouseCoordinates
                                  )
                                : null;

                            console.log("Viewer clicked, mesh user data:", meshUserData);

                            if (meshUserData !== null && meshUserData !== undefined) {
                                // Extract the mesh instance ID from the user data
                                const meshInstanceId = meshUserData.originalMeshInstance?.id;
                                console.log("Mesh clicked, mesh instance ID:", meshInstanceId);

                                if (meshInstanceId) {
                                    // Convert MeshInstanceId to string format for UI compatibility
                                    // Check if it has a meshIndex property
                                    let selectionId = meshInstanceId;
                                    if (meshInstanceId.meshIndex !== undefined) {
                                        selectionId = `mesh_${meshInstanceId.meshIndex}`;
                                        console.log(
                                            "Converted mesh ID to string format:",
                                            selectionId
                                        );
                                    }

                                    setSelection({
                                        type: "Mesh",
                                        meshInstanceId: selectionId,
                                        materialIndex: undefined,
                                    });
                                }
                            } else {
                                // Clear selection when clicking on empty space
                                setSelection({
                                    type: null,
                                    meshInstanceId: undefined,
                                    materialIndex: undefined,
                                });
                            }
                        }
                    }
                );
            }

            // Create a comprehensive viewer object that exposes all necessary methods
            const viewerWrapper = {
                embeddedViewer: viewer,
                viewer: viewer.GetViewer(),
                LoadModelFromUrlList: (urls: string[]) => viewer.LoadModelFromUrlList(urls),
                GetViewer: () => viewer.GetViewer(),
                GetModel: () => viewer.GetModel(),
                // Expose OV library for color and settings creation
                OV: OVLib,
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
    }, [state.viewerInitialized, settings, updateState, state.model]);

    // Watch for file updates from container and store in ref
    useEffect(() => {
        if (state.model?.files && state.model.files.length > 0) {
            console.log("Storing files in ref:", state.model.files);
            pendingFilesRef.current = state.model.files;
        } else if (state.model?.fileNames && state.model.fileNames.length > 0) {
            console.log("Storing fileNames in ref:", state.model.fileNames);
            pendingFilesRef.current = state.model.fileNames;
        }
    }, [state.model?.files, state.model?.fileNames]);

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
