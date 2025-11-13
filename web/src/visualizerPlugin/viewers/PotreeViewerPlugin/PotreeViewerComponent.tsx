/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { Auth, Cache } from "aws-amplify";
import { ViewerPluginProps } from "../../core/types";
import { PotreeDependencyManager } from "./dependencies";

const PotreeViewerComponent: React.FC<ViewerPluginProps> = ({ assetId, databaseId, assetKey }) => {
    const engineElement = useRef<HTMLDivElement>(null);
    const [loaded, setLoaded] = useState(false);
    const [showNoAssetMessage, setShowNoAssetMessage] = useState(false);
    const [config] = useState(Cache.getItem("config"));
    const [potreeInstance, setPotreeInstance] = useState<any>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const initializePotree = async () => {
            try {
                const Potree = await PotreeDependencyManager.loadPotree();
                setPotreeInstance(Potree);
            } catch (error) {
                console.error("Failed to initialize Potree:", error);
                setError("Failed to load Potree viewer");
                setShowNoAssetMessage(true);
            }
        };

        if (!potreeInstance) {
            initializePotree();
        }
    }, [potreeInstance]);

    useEffect(() => {
        const loadAsset = async () => {
            if (!potreeInstance || !assetKey || loaded || !config) return;

            try {
                let fileKey = assetKey + "/preview/PotreeViewer/metadata.json";
                let url = `${config.api}database/${databaseId}/assets/${assetId}/auxiliaryPreviewAssets/stream/${fileKey}`;

                const authHeader = {
                    Authorization: `Bearer ${Auth.Credentials.Auth.user.signInUserSession.idToken.jwtToken}`,
                };

                // If we get here, the files are available, proceed with loading
                if (engineElement.current) {
                    engineElement.current.setAttribute("pc", url);

                    setTimeout(() => {
                        let parentDiv = engineElement.current;
                        if (!parentDiv || !potreeInstance) return;

                        try {
                            let viewer = new potreeInstance.Viewer(parentDiv);
                            viewer.setEDLEnabled(true);
                            viewer.setFOV(60);
                            viewer.setPointBudget(1000000);
                            viewer.setClipTask(potreeInstance.ClipTask.SHOW_INSIDE);
                            viewer.loadSettingsFromURL();
                            viewer.useHQ = true;

                            viewer.setControls(viewer.orbitControls);

                            viewer.loadGUI(() => {
                                viewer.setLanguage("en");
                            });

                            // Load and add point cloud to scene
                            potreeInstance
                                .loadPointCloud(url, "Point Cloud", authHeader)
                                .then(
                                    (e: any) => {
                                        let pointcloud = e.pointcloud;
                                        let material = pointcloud.material;

                                        material.activeAttributeName = "rgba";
                                        material.minSize = 2;
                                        material.pointSizeType = potreeInstance.PointSizeType.FIXED;

                                        viewer.scene.addPointCloud(pointcloud);
                                        viewer.fitToScreen();

                                        console.log("Point cloud loaded successfully");
                                    },
                                    (e: any) => {
                                        console.error("Potree loading error: ", e);
                                        setError(
                                            "Potree Viewer Auxiliary Preview files not currently available"
                                        );
                                        setShowNoAssetMessage(true);
                                    }
                                )
                                .catch((error: any) => {
                                    console.error("Point cloud loading error:", error);
                                    setError(
                                        "Potree Viewer Auxiliary Preview files not currently available"
                                    );
                                    setShowNoAssetMessage(true);
                                });
                        } catch (viewerError) {
                            console.error("Error creating Potree viewer:", viewerError);
                            setError(
                                "Potree Viewer Auxiliary Preview files not currently available"
                            );
                            setShowNoAssetMessage(true);
                        }
                    }, 100);
                }

                setLoaded(true);
            } catch (error) {
                console.error("Error loading point cloud asset:", error);
                setError("Potree Viewer Auxiliary Preview files not currently available");
                setShowNoAssetMessage(true);
                setLoaded(true);
            }
        };

        loadAsset();
    }, [potreeInstance, loaded, assetKey, assetId, databaseId, config]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (potreeInstance) {
                PotreeDependencyManager.cleanup();
            }
        };
    }, [potreeInstance]);

    if (error || showNoAssetMessage) {
        return (
            <div style={{ position: "relative", height: "100%" }} id="potree-root">
                <div
                    style={{
                        color: "#d13212",
                        fontSize: "1.4em",
                        lineHeight: "1.5",
                        width: "100%",
                        position: "absolute",
                        top: "50%",
                        left: "50%",
                        transform: "translate(-50%, -50%)",
                        textAlign: "center",
                        padding: "20px",
                    }}
                >
                    {error || "Potree Viewer Auxiliary Preview files not currently available"}
                    <br />
                    <br />
                    <span style={{ fontSize: ".9em", color: "#d13212" }}>
                        Please run the Point Cloud Potree Viewer Pipeline to enable visualization
                        for this asset file
                    </span>
                </div>
            </div>
        );
    }

    if (!potreeInstance) {
        return (
            <div style={{ position: "relative", height: "100%" }} id="potree-root">
                <div
                    style={{
                        color: "#666",
                        fontSize: "1.2em",
                        lineHeight: "1.5",
                        width: "100%",
                        position: "absolute",
                        top: "50%",
                        left: "50%",
                        transform: "translate(-50%, -50%)",
                        textAlign: "center",
                    }}
                >
                    Loading Potree viewer...
                </div>
            </div>
        );
    }

    return (
        <div style={{ position: "relative", height: "100%" }} id="potree-root">
            <div id="potree_container">
                <div id="potree_render_area" ref={engineElement}></div>
                <div id="potree_sidebar_container"></div>
            </div>
        </div>
    );
};

export default PotreeViewerComponent;
