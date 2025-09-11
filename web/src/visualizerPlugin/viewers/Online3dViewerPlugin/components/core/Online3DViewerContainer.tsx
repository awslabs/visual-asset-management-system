/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { downloadAsset } from "../../../../../services/APIService";
import { Online3DViewerProps } from "../../types/viewer.types";
import { ViewerProvider, useViewerContext } from "../../context/ViewerContext";
import { ViewerCanvas } from "./ViewerCanvas";
import { Header } from "../layout/Header";
import { Toolbar } from "../layout/Toolbar";
import { LeftPanel } from "../layout/LeftPanel";
import { RightPanel } from "../layout/RightPanel";
import { LoadingOverlay } from "./LoadingOverlay";
import "./Online3DViewerContainer.css";

// Inner component that uses the context
const Online3DViewerInner: React.FC<Online3DViewerProps> = ({
    assetId,
    databaseId,
    assetKey,
    multiFileKeys,
    versionId,
}) => {
    const { state, settings, updateState, loadModel, clearModel } = useViewerContext();

    const [modelUrls, setModelUrls] = useState<string[]>([]);
    const [mainFileName, setMainFileName] = useState<string>("");

    // Load assets following the same pattern as the original component
    useEffect(() => {
        const loadAssets = async () => {
            if (!assetKey && (!multiFileKeys || multiFileKeys.length === 0)) {
                updateState({ isLoading: false });
                return;
            }

            console.log("Online3DViewer loading assets:", {
                assetId,
                databaseId,
                assetKey,
                multiFileKeys,
                versionId,
            });

            try {
                updateState({ error: null, isLoading: true });
                const urls: string[] = [];
                let fileName = "";

                if (multiFileKeys && multiFileKeys.length > 0) {
                    // Load multiple files
                    console.log("Loading multiple assets:", multiFileKeys);

                    for (const key of multiFileKeys) {
                        try {
                            const response = await downloadAsset({
                                assetId: assetId,
                                databaseId: databaseId,
                                key: key,
                                versionId: versionId || "",
                                downloadType: "assetFile",
                            });

                            if (response !== false && Array.isArray(response)) {
                                if (response[0] !== false) {
                                    urls.push(response[1]);
                                    if (!fileName) fileName = key; // Use first file as main name
                                    console.log(`Successfully loaded file: ${key}`);
                                } else {
                                    console.error(`Failed to load file: ${key}`, response[1]);
                                }
                            }
                        } catch (fileError) {
                            console.error(`Error loading file ${key}:`, fileError);
                        }
                    }
                } else if (assetKey && assetKey !== "") {
                    // Load single file
                    console.log("Loading single asset:", assetKey);

                    const response = await downloadAsset({
                        assetId: assetId,
                        databaseId: databaseId,
                        key: assetKey || "",
                        versionId: versionId || "",
                        downloadType: "assetFile",
                    });

                    if (response !== false && Array.isArray(response)) {
                        if (response[0] !== false) {
                            urls.push(response[1]);
                            fileName = assetKey;
                        } else {
                            console.error("Error loading single asset:", response[1]);
                            throw new Error("Failed to download 3D model file");
                        }
                    } else {
                        throw new Error("Invalid response format");
                    }
                }

                if (urls.length > 0) {
                    console.log(`Successfully loaded ${urls.length} model URLs:`, urls);
                    setModelUrls(urls);
                    setMainFileName(fileName);

                    // Don't set model as loaded here - let the viewer's onModelLoaded callback handle it
                    // Just store the URLs for loading
                } else {
                    throw new Error("No files could be loaded successfully");
                }
            } catch (error) {
                console.error("Error loading assets:", error);
                updateState({
                    error: error instanceof Error ? error.message : "Failed to load 3D model files",
                    isLoading: false,
                });
            }
        };

        loadAssets();
    }, [assetId, assetKey, databaseId, versionId, multiFileKeys, updateState]);

    // Load model URLs into viewer when both viewer and URLs are ready
    useEffect(() => {
        if (state.viewerInitialized && state.viewer && modelUrls.length > 0) {
            console.log(`Loading ${modelUrls.length} files into Online3DViewer:`, modelUrls);

            try {
                // Store file information first
                const fileNames =
                    multiFileKeys && multiFileKeys.length > 0 ? multiFileKeys : [assetKey];
                const cleanFileNames = fileNames.filter((name) => name && name !== "");

                // Update the model state with file information
                updateState({
                    model: {
                        loaded: false,
                        files: cleanFileNames,
                        urls: modelUrls,
                        fileNames: cleanFileNames,
                    },
                    isLoading: true,
                });

                // Clear any existing model first
                if (state.viewer.embeddedViewer) {
                    try {
                        state.viewer.embeddedViewer.Clear();
                    } catch (error) {
                        console.warn("Could not clear existing model:", error);
                    }
                }

                // Use the EmbeddedViewer's LoadModelFromUrlList method
                if (state.viewer.LoadModelFromUrlList) {
                    // Direct method available
                    state.viewer.LoadModelFromUrlList(modelUrls);
                } else if (state.viewer.embeddedViewer) {
                    // Use embeddedViewer reference
                    state.viewer.embeddedViewer.LoadModelFromUrlList(modelUrls);
                } else {
                    // Fallback - viewer should be the EmbeddedViewer itself
                    state.viewer.LoadModelFromUrlList(modelUrls);
                }
            } catch (error) {
                console.error("Error loading model into viewer:", error);
                updateState({
                    error: "Failed to load model into viewer",
                    isLoading: false,
                });
            }
        }
    }, [state.viewerInitialized, state.viewer, modelUrls, updateState, multiFileKeys, assetKey]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            clearModel();
        };
    }, [clearModel]);

    // Show error state
    if (state.error) {
        return (
            <div className="ov-error-container">
                <div className="ov-error-content">
                    <div>Error: {state.error}</div>
                </div>
            </div>
        );
    }

    return (
        <div
            className="ov-container"
            data-theme={settings.themeId}
            data-has-model={state.model ? "true" : "false"}
        >
            {/* Header */}
            <Header fileName={mainFileName} />

            {/* Toolbar */}
            <Toolbar />

            {/* Main content area */}
            <div className="ov-main">
                {/* Left panel (Navigator) */}
                <LeftPanel />

                {/* 3D Viewer */}
                <ViewerCanvas />

                {/* Right panel (Sidebar) */}
                <RightPanel />
            </div>

            {/* Loading overlay */}
            {(state.isLoading || !state.viewerInitialized) && (
                <LoadingOverlay
                    scriptsLoaded={state.scriptsLoaded}
                    viewerInitialized={state.viewerInitialized}
                    assetsLoaded={!state.isLoading}
                />
            )}
        </div>
    );
};

// Main container component with provider
const Online3DViewerContainer: React.FC<Online3DViewerProps> = (props) => {
    return (
        <ViewerProvider>
            <Online3DViewerInner {...props} />
        </ViewerProvider>
    );
};

export default Online3DViewerContainer;
