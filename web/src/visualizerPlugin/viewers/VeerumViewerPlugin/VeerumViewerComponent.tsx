/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState, lazy, Suspense } from "react";
import { Cache } from "aws-amplify";
import { VeerumDependencyManager } from "./dependencies";
import { VeerumViewerProps } from "./types/viewer.types";
import LoadingSpinner from "../../components/LoadingSpinner";
import { getDualAuthorizationHeader } from "../../../utils/authTokenUtils";

// Lazy load unified panel to avoid circular dependency issues
const VeerumPanel = lazy(() => import("./VeerumPanel"));

const VeerumViewerComponent: React.FC<VeerumViewerProps> = ({
    assetId,
    databaseId,
    assetKey,
    multiFileKeys,
    versionId,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerControllerRef = useRef<any>(null);
    const initializationRef = useRef(false);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");
    const [error, setError] = useState<string | null>(null);
    const [initError, setInitError] = useState<string | null>(null);
    const [fileErrors, setFileErrors] = useState<Array<{ file: string; error: string }>>([]);
    const [loadedModels, setLoadedModels] = useState<any[]>([]);
    const [showPanel, setShowPanel] = useState(true);

    useEffect(() => {
        // Check if we have any files to load
        const hasFiles = assetKey || (multiFileKeys && multiFileKeys.length > 0);
        if (!hasFiles || initializationRef.current) return;
        initializationRef.current = true;

        const initViewer = async () => {
            try {
                console.log("VEERUM Viewer: Starting initialization");
                setInitError(null);
                setFileErrors([]);
                setLoadingMessage("Initializing viewer...");

                // Load VEERUM viewer library
                setLoadingMessage("Loading VEERUM viewer library...");
                const veerumModule = await VeerumDependencyManager.loadVeerum();
                console.log("VEERUM Viewer: Library loaded successfully", veerumModule);

                // Get the VeerumViewer component and model classes from the module
                const VeerumViewer =
                    veerumModule.VeerumViewer || veerumModule.default?.VeerumViewer;
                const PointCloudModel = veerumModule.PointCloudModel;
                const TileModel = veerumModule.TileModel;

                if (!VeerumViewer) {
                    const errorMsg = "VeerumViewer component not found in loaded module";
                    setInitError(errorMsg);
                    throw new Error(errorMsg);
                }
                if (!PointCloudModel) {
                    const errorMsg = "PointCloudModel not found in Veerum module";
                    setInitError(errorMsg);
                    throw new Error(errorMsg);
                }
                if (!TileModel) {
                    const errorMsg = "TileModel not found in Veerum module";
                    setInitError(errorMsg);
                    throw new Error(errorMsg);
                }

                // Get config for API endpoint
                const config = Cache.getItem("config");
                if (!config) {
                    const errorMsg = "Configuration not available";
                    setInitError(errorMsg);
                    throw new Error(errorMsg);
                }

                // Get a valid, fresh authorization header (automatically refreshes token if expired)
                const authorizationHeader = await getDualAuthorizationHeader();
                const headers = new Headers();
                headers.append("Authorization", authorizationHeader);

                setLoadingMessage("Creating viewer...");

                // Create a container for the Veerum viewer
                if (!containerRef.current) {
                    const errorMsg = "Container ref not available";
                    setInitError(errorMsg);
                    throw new Error(errorMsg);
                }

                // Initialize the Veerum viewer using the ViewerController pattern
                const viewerContainer = document.createElement("div");
                viewerContainer.style.width = "100%";
                viewerContainer.style.height = "100%";
                containerRef.current.appendChild(viewerContainer);

                // Create viewer controller ref
                const viewerControllerRefInternal = { current: null };

                // Render the VeerumViewer component using React 17 API
                const ReactDOMModule = (window as any).ReactDOM;
                if (!ReactDOMModule || !ReactDOMModule.render) {
                    const errorMsg = "ReactDOM.render not available. Ensure ReactDOM is loaded.";
                    setInitError(errorMsg);
                    throw new Error(errorMsg);
                }

                ReactDOMModule.render(
                    React.createElement(VeerumViewer, {
                        viewerControllerRef: viewerControllerRefInternal,
                        style: { width: "100%", height: "100%" },
                    }),
                    viewerContainer
                );

                // Wait for viewer controller to initialize
                let attempts = 0;
                while (!viewerControllerRefInternal.current && attempts < 50) {
                    await new Promise((resolve) => setTimeout(resolve, 100));
                    attempts++;
                }

                if (!viewerControllerRefInternal.current) {
                    const errorMsg = "ViewerController failed to initialize after 5 seconds";
                    setInitError(errorMsg);
                    throw new Error(errorMsg);
                }

                viewerControllerRef.current = viewerControllerRefInternal.current;

                // Determine which files to load
                const filesToLoad =
                    multiFileKeys && multiFileKeys.length > 0
                        ? multiFileKeys
                        : assetKey
                        ? [assetKey]
                        : [];

                if (filesToLoad.length === 0) {
                    throw new Error("No files specified to load");
                }

                console.log("VEERUM Viewer: Loading files:", filesToLoad);

                // Helper function to get file extension
                const getFileExtension = (filename: string): string => {
                    return filename.toLowerCase().substring(filename.lastIndexOf("."));
                };

                // Load each file
                setLoadingMessage(`Loading ${filesToLoad.length} file(s)...`);
                const loadedModels: any[] = [];
                const errors: Array<{ file: string; error: string }> = [];

                for (let i = 0; i < filesToLoad.length; i++) {
                    const fileKey = filesToLoad[i];
                    const ext = getFileExtension(fileKey);

                    console.log(
                        `VEERUM Viewer: Loading file ${i + 1}/${filesToLoad.length}: ${fileKey}`
                    );
                    setLoadingMessage(`Loading file ${i + 1}/${filesToLoad.length}...`);

                    try {
                        if (ext === ".json") {
                            // 3D Tileset file - use TileModel with streaming URL (similar to Cesium)
                            // Construct streaming URL similar to Cesium viewer
                            const pathSegments = fileKey.split("/");
                            const encodedSegments = pathSegments.map((segment) =>
                                encodeURIComponent(segment)
                            );
                            const encodedFileKey = encodedSegments.join("/");
                            let assetUrl = `${config.api}database/${databaseId}/assets/${assetId}/download/stream/${encodedFileKey}`;

                            // Add versionId query parameter for single file mode
                            const isSingleFile = filesToLoad.length === 1;
                            if (isSingleFile && versionId) {
                                assetUrl += `?versionId=${encodeURIComponent(versionId)}`;
                            }

                            console.log(`VEERUM Viewer: Loading 3D tileset from ${assetUrl}`);

                            // TileModel constructor: new TileModel(id, url, type?, headers?)
                            const tileModel = new TileModel(
                                `tileset-${assetId}-${i}`,
                                assetUrl,
                                "3DTILES", // type parameter
                                headers // headers is the fourth parameter
                            );

                            // Set the file name for display in Scene Graph
                            tileModel.name = fileKey.split("/").pop() || fileKey;

                            // Wrap add() call with explicit promise handling to catch all errors
                            await Promise.resolve(viewerControllerRef.current.add(tileModel)).catch(
                                (addError) => {
                                    console.error(
                                        `VEERUM Viewer: Error adding tileset to viewer:`,
                                        addError
                                    );
                                    throw addError;
                                }
                            );

                            loadedModels.push(tileModel);
                            console.log(`VEERUM Viewer: 3D tileset ${fileKey} loaded successfully`);
                        } else {
                            // All other file types - use PointCloudModel with Potree metadata path
                            // This allows the plugin viewer system to control supported file types
                            // and makes it easier to add new point cloud formats later
                            const potreeFileKey = fileKey + "/preview/PotreeViewer/metadata.json";
                            const assetUrl = `${config.api}database/${databaseId}/assets/${assetId}/auxiliaryPreviewAssets/stream/${potreeFileKey}`;

                            console.log(`VEERUM Viewer: Validating point cloud URL ${assetUrl}`);

                            // Pre-flight validation: Check if the asset URL is accessible before creating model
                            // This catches CORS errors, network failures, and HTTP errors that the viewer library doesn't expose
                            try {
                                const response = await fetch(assetUrl, {
                                    method: "HEAD", // Use HEAD to avoid downloading the full file
                                    headers: headers,
                                });

                                // Check for successful response (2xx) or redirect (3xx)
                                if (!response.ok && response.status >= 400) {
                                    throw new Error(
                                        `HTTP ${response.status}: ${
                                            response.statusText || "Request failed"
                                        }`
                                    );
                                }

                                console.log(
                                    `VEERUM Viewer: Point cloud URL validation successful (${response.status})`
                                );
                            } catch (fetchError: any) {
                                // Handle all fetch errors: CORS, network failures, HTTP errors
                                const errorDetail =
                                    fetchError?.message ||
                                    fetchError?.toString() ||
                                    "Unknown error";
                                const errorMsg = `Auxiliary Preview Files (potree) are not currently available for this point cloud. Run the Potree Pipeline to generate: ${errorDetail}`;

                                console.error(
                                    `VEERUM Viewer: Point cloud URL validation failed for ${fileKey}:`,
                                    fetchError
                                );

                                // Add to errors array for user display
                                throw new Error(errorMsg);
                            }

                            console.log(`VEERUM Viewer: Loading point cloud from ${assetUrl}`);

                            const pointCloudModel = new PointCloudModel(
                                `pointcloud-${assetId}-${i}`,
                                assetUrl,
                                headers
                            );

                            // Set the file name for display in Scene Graph
                            pointCloudModel.name = fileKey.split("/").pop() || fileKey;

                            // Wrap add() call with explicit promise handling to catch all errors
                            // The PointCloudModel will fail to load if Potree files don't exist
                            await Promise.resolve(
                                viewerControllerRef.current.add(pointCloudModel)
                            ).catch((addError) => {
                                console.error(
                                    `VEERUM Viewer: Error adding point cloud to viewer:`,
                                    addError
                                );
                                // Provide user-friendly error message for missing Potree files
                                const errorMessage =
                                    addError?.message || addError?.toString() || "";
                                if (
                                    errorMessage.includes("404") ||
                                    errorMessage.includes("not found") ||
                                    errorMessage.includes("metadata")
                                ) {
                                    throw new Error(
                                        "Potree Viewer Auxiliary Preview files not currently available"
                                    );
                                }
                                throw addError;
                            });

                            loadedModels.push(pointCloudModel);
                            console.log(
                                `VEERUM Viewer: Point cloud ${fileKey} loaded successfully`
                            );
                        }
                    } catch (fileError: any) {
                        const errorMsg =
                            fileError?.message || fileError?.toString() || "Unknown error";
                        console.error(`VEERUM Viewer: Error loading file ${fileKey}:`, fileError);
                        errors.push({ file: fileKey, error: errorMsg });
                        // Continue loading other files even if one fails
                    }
                }

                // Store file errors for display
                if (errors.length > 0) {
                    setFileErrors(errors);
                }

                if (loadedModels.length === 0) {
                    // Don't throw an error - let the file errors display in the warning banner
                    console.error("VEERUM Viewer: No files could be loaded successfully");
                    setIsLoading(false);
                    return; // Exit early without throwing
                }

                // Store loaded models in state
                setLoadedModels(loadedModels);

                // Set initial point size to 1
                if (viewerControllerRef.current) {
                    viewerControllerRef.current.setModelVisuals({
                        pointCloudOptions: {
                            pointSize: 1,
                        },
                    });
                }

                // Zoom camera to fit all loaded models
                setLoadingMessage("Positioning camera...");
                if (loadedModels.length === 1) {
                    await viewerControllerRef.current.zoomCameraToObject(loadedModels[0]);
                } else {
                    // For multiple models, zoom to the first one (could be enhanced to fit all)
                    await viewerControllerRef.current.zoomCameraToObject(loadedModels[0]);
                }

                console.log(`VEERUM Viewer: Successfully loaded ${loadedModels.length} model(s)`);
                setIsLoading(false);
            } catch (error) {
                console.error("VEERUM Viewer: Initialization error:", error);
                const errorMessage =
                    error instanceof Error ? error.message : "Unknown error occurred";
                setError(errorMessage);
                setIsLoading(false);
            }
        };

        initViewer();

        // Cleanup function
        return () => {
            console.log("VEERUM Viewer: Cleaning up");
            if (viewerControllerRef.current) {
                try {
                    viewerControllerRef.current.dispose?.();
                } catch (error) {
                    console.error("VEERUM Viewer: Error disposing controller:", error);
                }
            }
        };
    }, [assetKey, multiFileKeys, assetId, databaseId, versionId]);

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
            {/* Initialization error banner (dismissible) */}
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
                        VEERUM Viewer Initialization Error
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

            {/* File loading errors (dismissible warnings) */}
            {fileErrors.length > 0 && !error && (
                <div
                    style={{
                        position: "absolute",
                        top: initError ? "80px" : "0",
                        left: "0",
                        right: "0",
                        backgroundColor: "#fff3cd",
                        border: "1px solid #ffc107",
                        borderRadius: "4px",
                        padding: "12px 16px",
                        margin: "8px",
                        zIndex: 1001,
                        fontSize: "0.85em",
                        maxHeight: "150px",
                        overflowY: "auto",
                    }}
                >
                    <div
                        style={{
                            color: "#856404",
                            fontWeight: "bold",
                            marginBottom: "8px",
                        }}
                    >
                        ⚠️{" "}
                        {loadedModels.length === 0
                            ? "All files failed to load"
                            : "Some files failed to load"}{" "}
                        ({fileErrors.length}/{multiFileKeys?.length || 1})
                    </div>
                    {fileErrors.map((err, idx) => (
                        <div
                            key={idx}
                            style={{
                                color: "#666",
                                marginBottom: "4px",
                                paddingLeft: "8px",
                                borderLeft: "2px solid #ffc107",
                            }}
                        >
                            <strong>{err.file}</strong>: {err.error}
                        </div>
                    ))}
                    <button
                        onClick={() => setFileErrors([])}
                        style={{
                            position: "absolute",
                            top: "8px",
                            right: "8px",
                            background: "none",
                            border: "none",
                            color: "#856404",
                            cursor: "pointer",
                            fontSize: "16px",
                            padding: "0",
                            width: "20px",
                            height: "20px",
                        }}
                        title="Dismiss warnings"
                    >
                        ×
                    </button>
                </div>
            )}

            {/* Loading overlay */}
            {isLoading && !error && <LoadingSpinner message={loadingMessage} />}

            {/* Critical error message (center screen) */}
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
                        zIndex: 1000,
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "10px" }}>
                        Error Loading VEERUM Viewer
                    </div>
                    <div>{error}</div>
                    <div style={{ fontSize: "0.85em", marginTop: "10px", opacity: 0.9 }}>
                        Check browser console for detailed error information
                    </div>
                </div>
            )}

            {/* Unified Panel (Controls + Scene Graph Tabs) */}
            {viewerControllerRef.current && loadedModels.length > 0 && showPanel && (
                <Suspense fallback={<div />}>
                    <VeerumPanel
                        viewerController={viewerControllerRef.current}
                        loadedModels={loadedModels}
                        initError={initError}
                        onClose={() => setShowPanel(false)}
                    />
                </Suspense>
            )}

            {/* Panel Toggle Button */}
            {viewerControllerRef.current && loadedModels.length > 0 && !showPanel && (
                <button
                    onClick={() => setShowPanel(true)}
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
                    title="Show controls panel"
                >
                    ⚙️ Panel
                </button>
            )}

            {/* Info Panel */}
            {!isLoading && !error && (
                <div
                    style={{
                        position: "absolute",
                        top: initError || fileErrors.length > 0 ? "auto" : "10px",
                        bottom: initError || fileErrors.length > 0 ? "10px" : "auto",
                        right: "10px",
                        color: "white",
                        fontSize: "12px",
                        backgroundColor: "rgba(0,0,0,0.7)",
                        padding: "8px",
                        borderRadius: "4px",
                        zIndex: 1000,
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "4px" }}>VEERUM 3D Viewer</div>
                    <div style={{ fontSize: "0.9em", opacity: 0.9 }}>
                        Mouse: Rotate | Wheel: Zoom | Right-click: Pan
                    </div>
                    {fileErrors.length > 0 && (
                        <div style={{ fontSize: "0.85em", marginTop: "6px", color: "#ffc107" }}>
                            ⚠️ {fileErrors.length} file(s) failed to load
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default VeerumViewerComponent;
