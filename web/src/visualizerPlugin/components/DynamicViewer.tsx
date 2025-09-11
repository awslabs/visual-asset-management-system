/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useState, useEffect, useRef } from "react";
import { Container, Grid, Header, Spinner, Box } from "@cloudscape-design/components";
import {
    PluginRegistry,
    getFileExtensions,
    ViewerPlugin,
    ViewerPluginMetadata,
} from "../core/PluginRegistry";
import { FileInfo } from "../core/types";
import { StylesheetManager } from "../core/StylesheetManager";
import ViewerSelector from "./ViewerSelector";

export interface DynamicViewerProps {
    files: FileInfo[];
    assetId: string;
    databaseId: string;
    viewerMode: string;
    onViewerModeChange: (mode: string) => void;
    showViewerSelector?: boolean;
    isPreviewMode?: boolean;
    onDeletePreview?: () => void;
    hideFullscreenControls?: boolean;
}

export const DynamicViewer: React.FC<DynamicViewerProps> = ({
    files,
    assetId,
    databaseId,
    viewerMode,
    onViewerModeChange,
    showViewerSelector = true,
    isPreviewMode = false,
    onDeletePreview,
    hideFullscreenControls = false,
}) => {
    const [selectedViewerId, setSelectedViewerId] = useState<string | null>(null);
    const [compatibleViewers, setCompatibleViewers] = useState<ViewerPluginMetadata[]>([]);
    const [loadedViewer, setLoadedViewer] = useState<ViewerPlugin | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [registryInitialized, setRegistryInitialized] = useState(false);
    const [viewerLoading, setViewerLoading] = useState(false);
    const mountedRef = useRef(true);

    // Initialize plugin registry
    useEffect(() => {
        const initializeRegistry = async () => {
            try {
                const registry = PluginRegistry.getInstance();
                if (!registry.isInitialized()) {
                    await registry.initialize();
                }
                setRegistryInitialized(true);
            } catch (error) {
                console.error("Failed to initialize plugin registry:", error);
                setError("Failed to initialize viewer plugins");
            }
        };

        initializeRegistry();
    }, []);

    // Find compatible viewers when files or registry changes
    useEffect(() => {
        if (!registryInitialized || files.length === 0) return;

        const registry = PluginRegistry.getInstance();

        // Get all unique file extensions from the files
        const fileExtensions = getFileExtensions(files);
        const isMultiFile = files.length > 1;

        console.log("Finding viewers for:", { fileExtensions, isMultiFile, isPreviewMode });

        const viewerMetadata = registry.getCompatibleViewers(
            fileExtensions,
            isMultiFile,
            isPreviewMode
        );
        setCompatibleViewers(viewerMetadata);

        // Auto-select the highest priority viewer only if no viewer is currently selected
        if (viewerMetadata.length > 0 && !selectedViewerId) {
            setSelectedViewerId(viewerMetadata[0].config.id);
        } else if (viewerMetadata.length === 0) {
            setError(`No compatible viewers found for file types: ${fileExtensions.join(", ")}`);
            setLoading(false); // Stop loading when no viewers are found
        }
    }, [files, isPreviewMode, registryInitialized]); // Removed selectedViewerId from dependencies

    // Load selected viewer lazily
    useEffect(() => {
        if (!selectedViewerId || !registryInitialized) return;

        const loadViewer = async () => {
            if (!mountedRef.current) return;

            setViewerLoading(true);
            setError(null);
            setLoadedViewer(null); // Clear previous viewer immediately

            try {
                const registry = PluginRegistry.getInstance();

                // Switch to the new plugin (this handles unloading the previous one)
                const viewer = await registry.switchToPlugin(selectedViewerId);

                if (!mountedRef.current) return; // Check if component is still mounted

                // Load dependencies if needed
                await registry.loadPluginDependencies(selectedViewerId);

                if (!mountedRef.current) return; // Check again after async operation

                setLoadedViewer(viewer);
                console.log(`Loaded viewer: ${viewer.config.name}`, viewer);
                console.log(`Viewer component:`, viewer.component);
            } catch (error) {
                if (!mountedRef.current) return;

                console.error("Error loading viewer:", error);
                setError(
                    `Failed to load viewer: ${
                        error instanceof Error ? error.message : "Unknown error"
                    }`
                );
            } finally {
                if (mountedRef.current) {
                    setViewerLoading(false);
                    setLoading(false);
                }
            }
        };

        loadViewer();
    }, [selectedViewerId, registryInitialized]);

    const handleViewerChange = (newViewerId: string) => {
        if (newViewerId !== selectedViewerId) {
            console.log(`Switching viewer from ${selectedViewerId} to ${newViewerId}`);
            setSelectedViewerId(newViewerId);
            // Don't need to manually reset loadedViewer - the effect will handle the switch
        }
    };

    // Cleanup on unmount
    useEffect(() => {
        // Set mounted to true when component mounts
        mountedRef.current = true;

        return () => {
            mountedRef.current = false;

            // Cleanup current plugin when component unmounts
            const registry = PluginRegistry.getInstance();
            const currentPlugin = registry.getCurrentlyLoadedPlugin();
            if (currentPlugin) {
                console.log("DynamicViewer unmounting, cleaning up plugin:", currentPlugin);
                try {
                    // Use synchronous cleanup to avoid race conditions during unmount
                    registry.cleanup();
                } catch (error) {
                    console.error("Error during cleanup:", error);
                }
            }
        };
    }, []);

    // Show loading state
    if (!registryInitialized || loading || viewerLoading) {
        return (
            <Container>
                <Box textAlign="center" padding="xl">
                    <Spinner size="large" />
                    <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                        {!registryInitialized
                            ? "Initializing viewers..."
                            : viewerLoading
                            ? "Loading viewer component..."
                            : "Loading viewer..."}
                    </Box>
                </Box>
            </Container>
        );
    }

    // Show error state
    if (error) {
        return (
            <Container>
                <Box textAlign="center" padding="xl">
                    <Box variant="h3" color="text-status-error">
                        Viewer Error
                    </Box>
                    <Box variant="p" color="text-status-error" margin={{ top: "s" }}>
                        {error}
                    </Box>
                </Box>
            </Container>
        );
    }

    // Show no viewers available
    if (compatibleViewers.length === 0) {
        return (
            <Container>
                <Box textAlign="center" padding="xl">
                    <Box variant="h3">No Viewers Available</Box>
                    <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                        No compatible viewers found for the selected file(s).
                    </Box>
                </Box>
            </Container>
        );
    }

    return (
        <div style={{ height: "100%", width: "100%", display: "flex", flexDirection: "column" }}>
            <Container
                header={
                    <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                        <Box margin={{ bottom: "m" }}>
                            <Header variant="h2">Visualizer</Header>
                        </Box>
                        <Box textAlign="right" margin={{ bottom: "m" }}>
                            {showViewerSelector && compatibleViewers.length > 0 && (
                                <ViewerSelector
                                    viewers={compatibleViewers.map((metadata) => ({
                                        config: metadata.config,
                                        component: null as any, // Not needed for selector
                                        isLoaded: metadata.isLoaded,
                                    }))}
                                    selectedViewerId={selectedViewerId}
                                    onViewerChange={handleViewerChange}
                                    className="visualizer-segment-control"
                                />
                            )}
                        </Box>
                    </Grid>
                }
            >
                <Suspense
                    fallback={
                        <Box textAlign="center" padding="xl">
                            <Spinner size="large" />
                            <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                                Loading viewer component...
                            </Box>
                        </Box>
                    }
                >
                    <div
                        className={`visualizer-container ${
                            loadedViewer
                                ? StylesheetManager.getScopedClassName(loadedViewer.config.id)
                                : ""
                        }`}
                        style={{
                            height: "calc(100vh - 300px)", // Use viewport height minus space for modal header/footer/container header
                            width: "100%",
                            //minHeight: '400px', // Fallback minimum height
                        }}
                    >
                        <div
                            className="visualizer-container-canvases"
                            style={{ height: "100%", width: "100%" }}
                        >
                            {loadedViewer ? (
                                <loadedViewer.component
                                    assetId={assetId}
                                    databaseId={databaseId}
                                    assetKey={files.length === 1 ? files[0].key : undefined}
                                    multiFileKeys={
                                        files.length > 1 ? files.map((f) => f.key) : undefined
                                    }
                                    versionId={files.length === 1 ? files[0].versionId : undefined}
                                    viewerMode={viewerMode}
                                    onViewerModeChange={onViewerModeChange}
                                    onDeletePreview={onDeletePreview}
                                    isPreviewFile={isPreviewMode}
                                    customParameters={loadedViewer.config.customParameters}
                                />
                            ) : (
                                <Box textAlign="center" padding="xl">
                                    <Box variant="p" color="text-status-info">
                                        No viewer component loaded
                                    </Box>
                                </Box>
                            )}
                        </div>

                        {/* Viewer controls footer - only show if viewer supports fullscreen and controls are not hidden */}
                        {loadedViewer &&
                            loadedViewer.config.canFullscreen &&
                            !hideFullscreenControls && (
                                <div className="visualizer-footer">
                                    <a
                                        title="View Wide"
                                        onClick={() => onViewerModeChange("wide")}
                                        className={viewerMode === "wide" ? "selected" : ""}
                                    >
                                        <svg
                                            xmlns="http://www.w3.org/2000/svg"
                                            enableBackground="new 0 0 24 24"
                                            height="24px"
                                            viewBox="0 0 24 24"
                                            width="24px"
                                            fill="#000000"
                                        >
                                            <g>
                                                <rect fill="none" height="24" width="24" />
                                            </g>
                                            <g>
                                                <g>
                                                    <path d="M2,4v16h20V4H2z M20,18H4V6h16V18z" />
                                                </g>
                                            </g>
                                        </svg>
                                    </a>
                                    <a
                                        title="View Fullscreen"
                                        onClick={() => onViewerModeChange("fullscreen")}
                                        className={viewerMode === "fullscreen" ? "selected" : ""}
                                    >
                                        <svg
                                            xmlns="http://www.w3.org/2000/svg"
                                            height="24px"
                                            viewBox="0 0 24 24"
                                            width="24px"
                                            fill="#000000"
                                        >
                                            <path d="M0 0h24v24H0V0z" fill="none" />
                                            <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z" />
                                        </svg>
                                    </a>
                                </div>
                            )}
                    </div>
                </Suspense>
            </Container>
        </div>
    );
};

export default DynamicViewer;
