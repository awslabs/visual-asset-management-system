/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useState, useEffect } from "react";
import { Container, Grid, Header, Spinner, Box } from "@cloudscape-design/components";
import { PluginRegistry, getFileExtensions, ViewerPlugin } from "../core/PluginRegistry";
import { FileInfo } from "../core/types";
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
    const [compatibleViewers, setCompatibleViewers] = useState<ViewerPlugin[]>([]);
    const [loadedViewer, setLoadedViewer] = useState<ViewerPlugin | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [registryInitialized, setRegistryInitialized] = useState(false);

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

        const viewers = registry.getCompatibleViewers(fileExtensions, isMultiFile, isPreviewMode);
        setCompatibleViewers(viewers);

        // Auto-select the highest priority viewer
        if (viewers.length > 0 && !selectedViewerId) {
            setSelectedViewerId(viewers[0].config.id);
        } else if (viewers.length === 0) {
            setError(`No compatible viewers found for file types: ${fileExtensions.join(", ")}`);
            setLoading(false); // Stop loading when no viewers are found
        }
    }, [files, selectedViewerId, isPreviewMode, registryInitialized]);

    // Load selected viewer
    useEffect(() => {
        if (!selectedViewerId || !registryInitialized) return;

        const loadViewer = async () => {
            setLoading(true);
            setError(null);

            try {
                const registry = PluginRegistry.getInstance();
                const viewer = registry.getViewer(selectedViewerId);

                if (!viewer) {
                    throw new Error(`Viewer ${selectedViewerId} not found`);
                }

                // Load dependencies if needed
                await registry.loadPluginDependencies(selectedViewerId);

                setLoadedViewer(viewer);
                console.log(`Loaded viewer: ${viewer.config.name}`);
            } catch (error) {
                console.error("Error loading viewer:", error);
                setError(
                    `Failed to load viewer: ${
                        error instanceof Error ? error.message : "Unknown error"
                    }`
                );
            } finally {
                setLoading(false);
            }
        };

        loadViewer();
    }, [selectedViewerId, registryInitialized]);

    const handleViewerChange = (newViewerId: string) => {
        if (newViewerId !== selectedViewerId) {
            setSelectedViewerId(newViewerId);
            setLoadedViewer(null); // Reset to trigger reload
        }
    };

    // Show loading state
    if (!registryInitialized || loading) {
        return (
            <Container>
                <Box textAlign="center" padding="xl">
                    <Spinner size="large" />
                    <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                        {!registryInitialized ? "Initializing viewers..." : "Loading viewer..."}
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
        <Container
            header={
                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                    <Box margin={{ bottom: "m" }}>
                        <Header variant="h2">Visualizer</Header>
                    </Box>
                    <Box textAlign="right" margin={{ bottom: "m" }}>
                        {showViewerSelector && compatibleViewers.length > 0 && (
                            <ViewerSelector
                                viewers={compatibleViewers}
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
                <div className="visualizer-container">
                    <div className="visualizer-container-canvases">
                        {loadedViewer && (
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
    );
};

export default DynamicViewer;
