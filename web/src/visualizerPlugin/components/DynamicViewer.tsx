/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useState, useEffect, useMemo, useRef, Component } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Grid from "@cloudscape-design/components/grid";
import Header from "@cloudscape-design/components/header";
import Spinner from "@cloudscape-design/components/spinner";

/**
 * Error boundary that catches render errors from viewer plugins.
 * Prevents viewer crashes from propagating to the file manager or parent components.
 */
class ViewerErrorBoundary extends Component<
    { children: React.ReactNode },
    { hasError: boolean; error: Error | null }
> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error) {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error("Viewer plugin render error:", error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <Box padding="l" textAlign="center">
                    <Alert type="error" header="Viewer Error">
                        The viewer encountered an error and could not render.
                        <br />
                        <Box variant="small" padding={{ top: "xs" }}>
                            {this.state.error?.message || "Unknown error"}
                        </Box>
                    </Alert>
                </Box>
            );
        }
        return this.props.children;
    }
}
import {
    PluginRegistry,
    getFileExtensions,
    deriveCompareContext,
    ViewerPlugin,
    ViewerPluginMetadata,
    ViewerMode,
} from "../core/PluginRegistry";
import { FileInfo } from "../core/types";
import { StylesheetManager } from "../core/StylesheetManager";
import ViewerSelector from "./ViewerSelector";

export interface DynamicViewerProps {
    files: FileInfo[];
    assetId: string;
    databaseId: string;
    assetVersionId?: string;
    viewerMode: string;
    onViewerModeChange: (mode: string) => void;
    showViewerSelector?: boolean;
    isPreviewMode?: boolean;
    onDeletePreview?: () => void;
    hideFullscreenControls?: boolean;
    /** "viewport" uses calc(100vh - 300px) for modals, "container" uses 100% to fill parent */
    sizingMode?: "viewport" | "container";
    /** Which surface this render serves. "compare" filters to compare-capable viewers and passes
     *  the ordered `files` to the viewer as compareFiles. Defaults to "visualize" (unchanged path). */
    mode?: ViewerMode;
}

export const DynamicViewer: React.FC<DynamicViewerProps> = ({
    files,
    assetId,
    databaseId,
    assetVersionId,
    viewerMode,
    onViewerModeChange,
    showViewerSelector = true,
    isPreviewMode = false,
    onDeletePreview,
    hideFullscreenControls = false,
    sizingMode = "viewport",
    mode = "visualize",
}) => {
    const [selectedViewerId, setSelectedViewerId] = useState<string | null>(null);
    const [compatibleViewers, setCompatibleViewers] = useState<ViewerPluginMetadata[]>([]);
    const [loadedViewer, setLoadedViewer] = useState<ViewerPlugin | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [registryInitialized, setRegistryInitialized] = useState(false);
    const [viewerLoading, setViewerLoading] = useState(false);
    const mountedRef = useRef(true);
    // Mirrors selectedViewerId so the compatibility effect can read the current
    // selection without taking it as a dependency (re-running that effect on a
    // selection change would clobber the user's pick).
    const selectedViewerIdRef = useRef<string | null>(null);
    selectedViewerIdRef.current = selectedViewerId;

    // Compare entries with their owning database/asset resolved. An entry that names its own pair keeps
    // it; one that does not (legacy callers) takes the caller's TOP-LEVEL pair — never files[0]'s, which
    // is what the visualize-path effectiveAssetId below falls back to. In compare mode that fallback
    // would silently re-home a second entry under the first entry's asset. The shape/cross-asset
    // classification and the viewer both consume this resolved list. Memoized on the inputs so the
    // compatibility effect and the viewer's own effects see a stable reference between renders.
    const compareFiles = useMemo<FileInfo[] | undefined>(() => {
        if (mode !== "compare") {
            return undefined;
        }
        return files.map((file) => ({
            ...file,
            assetId: file.assetId ?? assetId,
            databaseId: file.databaseId ?? databaseId,
        }));
    }, [mode, files, assetId, databaseId]);

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

        console.log("Finding viewers for:", { fileExtensions, isMultiFile, isPreviewMode, mode });

        const viewerMetadata =
            mode === "compare"
                ? registry.getCompatibleViewers(
                      fileExtensions,
                      isMultiFile,
                      false,
                      "compare",
                      // Classified on the RESOLVED entries so a missing per-entry asset id does not
                      // read as "a different asset" (or hide a real cross-asset selection).
                      deriveCompareContext(compareFiles ?? files)
                  )
                : registry.getCompatibleViewers(fileExtensions, isMultiFile, isPreviewMode);
        setCompatibleViewers(viewerMetadata);

        if (viewerMetadata.length === 0) {
            // Customize error message based on whether multiple files are selected
            const errorMessage =
                mode === "compare"
                    ? `No compatible compare viewers found for file types: ${fileExtensions.join(
                          ", "
                      )}`
                    : isMultiFile
                    ? `No compatible multi-file viewers found for file types: ${fileExtensions.join(
                          ", "
                      )}`
                    : `No compatible viewers found for file types: ${fileExtensions.join(", ")}`;
            setSelectedViewerId(null);
            setLoadedViewer(null);
            setError(errorMessage);
            setLoading(false); // Stop loading when no viewers are found
            return;
        }

        // The previous selection's error (including "no compatible viewers" for
        // a file the user has already left) does not describe this selection.
        setError(null);

        // Auto-select only if there's exactly one viewer available
        // If multiple viewers exist, force user to choose to avoid loading performance-heavy viewers
        const currentId = selectedViewerIdRef.current;
        const selectionStillCompatible =
            !!currentId && viewerMetadata.some((metadata) => metadata.config.id === currentId);
        if (selectionStillCompatible) {
            return;
        }
        if (viewerMetadata.length === 1) {
            setSelectedViewerId(viewerMetadata[0].config.id);
        } else {
            // Multiple viewers available - don't auto-select, force user choice.
            // The previously selected viewer cannot render these files, so its
            // component must come down with the selection.
            setSelectedViewerId(null);
            setLoadedViewer(null);
            setLoading(false); // Stop loading state to show the selector
        }
    }, [files, compareFiles, isPreviewMode, registryInitialized, mode]); // Removed selectedViewerId from dependencies

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

    // Determine the content to show inside the viewer container
    const isShowingViewer =
        registryInitialized && !loading && !viewerLoading && !error && compatibleViewers.length > 0;

    // Per-file asset context (Decision #3) for the VISUALIZE path. For a single file, prefer its own
    // context. For multi-file, use the first file's context as the shared pair (same-asset case);
    // top-level props remain the fallback for legacy callers that don't set per-file context.
    // Compare mode does not use this: each compare entry is resolved individually (see compareFiles),
    // so the viewer receives the caller's top-level pair untouched and must never read files[0]'s.
    const effectiveAssetId =
        mode === "compare"
            ? assetId
            : (files.length === 1 ? files[0].assetId : files[0]?.assetId) ?? assetId;
    const effectiveDatabaseId =
        mode === "compare"
            ? databaseId
            : (files.length === 1 ? files[0].databaseId : files[0]?.databaseId) ?? databaseId;

    const renderStatusContent = () => {
        if (!registryInitialized || loading || viewerLoading) {
            return (
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        height: "100%",
                        minHeight: "200px",
                    }}
                >
                    <Box textAlign="center">
                        <Spinner size="large" />
                        <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                            {!registryInitialized
                                ? "Initializing viewers..."
                                : viewerLoading
                                ? "Loading viewer component..."
                                : "Loading viewer..."}
                        </Box>
                    </Box>
                </div>
            );
        }
        if (error) {
            return (
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        height: "100%",
                        minHeight: "200px",
                    }}
                >
                    <Box textAlign="center">
                        <Box variant="h3" color="text-status-error">
                            Viewer Error
                        </Box>
                        <Box variant="p" color="text-status-error" margin={{ top: "s" }}>
                            {error}
                        </Box>
                    </Box>
                </div>
            );
        }
        if (compatibleViewers.length === 0) {
            return (
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        height: "100%",
                        minHeight: "200px",
                    }}
                >
                    <Box textAlign="center">
                        <Box variant="h3">No Viewers Available</Box>
                        <Box variant="p" color="text-status-info" margin={{ top: "s" }}>
                            No compatible viewers found for the selected file(s).
                        </Box>
                    </Box>
                </div>
            );
        }
        return null;
    };

    return (
        <div
            style={{
                height: "100%",
                width: "100%",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
            }}
        >
            <Container
                fitHeight={true}
                disableContentPaddings={sizingMode === "container"}
                header={
                    <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                        <Box>
                            <Header variant="h2">
                                {mode === "compare" ? "Compare" : "Visualizer"}
                            </Header>
                        </Box>
                        <Box textAlign="right">
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
                                    mode={mode}
                                />
                            )}
                        </Box>
                    </Grid>
                }
            >
                <ViewerErrorBoundary>
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
                        {!isShowingViewer ? (
                            renderStatusContent()
                        ) : (
                            <div
                                style={
                                    sizingMode === "container"
                                        ? {
                                              padding: "2px",
                                              height: "100%",
                                              boxSizing: "border-box",
                                              overflow: "hidden",
                                          }
                                        : { height: "100%" }
                                }
                            >
                                <div
                                    className={`visualizer-container ${
                                        loadedViewer
                                            ? StylesheetManager.getScopedClassName(
                                                  loadedViewer.config.id
                                              )
                                            : ""
                                    }`}
                                    style={{
                                        height:
                                            sizingMode === "container"
                                                ? "100%"
                                                : "calc(100vh - 300px)",
                                        width: "100%",
                                        ...(sizingMode === "container"
                                            ? { background: "none", marginTop: 0 }
                                            : {}),
                                    }}
                                >
                                    <div
                                        className="visualizer-container-canvases"
                                        style={{ height: "100%", width: "100%" }}
                                    >
                                        {loadedViewer ? (
                                            <loadedViewer.component
                                                assetId={effectiveAssetId}
                                                databaseId={effectiveDatabaseId}
                                                assetKey={
                                                    files.length === 1 ? files[0].key : undefined
                                                }
                                                multiFileKeys={
                                                    files.length > 1
                                                        ? files.map((f) => f.key)
                                                        : undefined
                                                }
                                                multiFiles={files.length > 1 ? files : undefined}
                                                versionId={
                                                    files.length === 1
                                                        ? files[0].versionId
                                                        : undefined
                                                }
                                                assetVersionId={assetVersionId}
                                                viewerMode={viewerMode}
                                                onViewerModeChange={onViewerModeChange}
                                                onDeletePreview={onDeletePreview}
                                                isPreviewFile={isPreviewMode}
                                                compareMode={mode === "compare"}
                                                compareFiles={compareFiles}
                                                customParameters={
                                                    loadedViewer.config.customParameters
                                                }
                                            />
                                        ) : (
                                            <Box textAlign="center" padding="xl">
                                                <Box variant="p" color="text-status-error">
                                                    No Viewer Component Selected
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
                                                    className={
                                                        viewerMode === "wide" ? "selected" : ""
                                                    }
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
                                                            <rect
                                                                fill="none"
                                                                height="24"
                                                                width="24"
                                                            />
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
                                                    className={
                                                        viewerMode === "fullscreen"
                                                            ? "selected"
                                                            : ""
                                                    }
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
                            </div>
                        )}
                    </Suspense>
                </ViewerErrorBoundary>
            </Container>
        </div>
    );
};

export default DynamicViewer;
