/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import * as OV from "online-3d-viewer";
import { downloadAsset } from "../../services/APIService";

export default function ModelViewer(props) {
    const engineElement = useRef(null);
    const { assetId, databaseId, assetKey, multiFileKeys } = props;
    const [loaded, setLoaded] = useState(false);

    useEffect(() => {
        const loadSingleAsset = async () => {
            try {
                console.log("Loading single asset:", assetKey);
                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey,
                    version: "",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error loading single asset:", response[1]);
                    } else {
                        engineElement.current.setAttribute("model", response[1]);
                        setTimeout(() => {
                            let parentDiv = engineElement.current;
                            let viewer = new OV.EmbeddedViewer(parentDiv, {
                                backgroundColor: new OV.RGBAColor(182, 182, 182, 182),
                                defaultColor: new OV.RGBColor(200, 200, 200),
                                edgeSettings: new OV.EdgeSettings(
                                    true,
                                    new OV.RGBColor(0, 0, 255),
                                    1
                                ),
                            });
                            viewer.LoadModelFromUrlList([response[1]]);
                        }, 100);
                    }
                }
            } catch (error) {
                console.error("Error loading single asset:", error);
            }
        };

        const loadMultipleAssets = async () => {
            try {
                console.log("Loading multiple assets:", multiFileKeys);
                const urls = [];
                
                // Download all files and collect their URLs
                for (const key of multiFileKeys) {
                    try {
                        const response = await downloadAsset({
                            assetId: assetId,
                            databaseId: databaseId,
                            key: key,
                            version: "",
                        });

                        if (response !== false && Array.isArray(response)) {
                            if (response[0] !== false) {
                                urls.push(response[1]);
                                console.log(`Successfully loaded file: ${key}`);
                            } else {
                                console.error(`Failed to load file: ${key}`, response[1]);
                            }
                        }
                    } catch (fileError) {
                        console.error(`Error loading file ${key}:`, fileError);
                    }
                }

                if (urls.length > 0) {
                    // Set the first URL as the model attribute for compatibility
                    engineElement.current.setAttribute("model", urls[0]);
                    
                    setTimeout(() => {
                        let parentDiv = engineElement.current;
                        let viewer = new OV.EmbeddedViewer(parentDiv, {
                            backgroundColor: new OV.RGBAColor(182, 182, 182, 182),
                            defaultColor: new OV.RGBColor(200, 200, 200),
                            edgeSettings: new OV.EdgeSettings(
                                true,
                                new OV.RGBColor(0, 0, 255),
                                1
                            ),
                        });
                        
                        // Load all URLs into the viewer
                        console.log(`Loading ${urls.length} files into Online3DViewer:`, urls);
                        viewer.LoadModelFromUrlList(urls);
                    }, 100);
                } else {
                    console.error("No files could be loaded successfully");
                }
            } catch (error) {
                console.error("Error loading multiple assets:", error);
            }
        };

        // Determine which loading method to use
        if (!loaded) {
            if (multiFileKeys && multiFileKeys.length > 0) {
                // Multi-file mode
                loadMultipleAssets();
                setLoaded(true);
            } else if (assetKey && assetKey !== "") {
                // Single file mode
                loadSingleAsset();
                setLoaded(true);
            }
        }
    }, [loaded, assetKey, multiFileKeys, assetId, databaseId]);

    return (
        <div
            style={{
                overflow: "auto",
                backgroundColor: "white",
                height: "100%",
                padding: 0,
            }}
        >
            <div
                className="online_3d_viewer"
                style={{ width: "100%", height: "100%" }}
                ref={engineElement}
            ></div>
        </div>
    );
}
