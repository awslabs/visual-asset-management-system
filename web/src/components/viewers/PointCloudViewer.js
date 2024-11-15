/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { Auth, Cache } from "aws-amplify";

// load Potree into the global window object
const Potree = window.Potree;

export default function PointCloudViewer(props) {
    const engineElement = useRef(null);
    const { assetKey } = props;
    const [loaded, setLoaded] = useState(false);
    const [showNoAssetMessage, setShowNoAssetMessage] = useState(false);
    const [config] = useState(Cache.getItem("config"));

    useEffect(() => {
        const loadAsset = async () => {
            let url = `${config.api}visualizerAssets/${assetKey}/metadata.json`;

            const authHeader = {
                Authorization: `Bearer ${Auth.Credentials.Auth.user.signInUserSession.idToken.jwtToken}`,
            };

            engineElement.current.setAttribute("pc", url);

            setTimeout(() => {
                let parentDiv = engineElement.current;
                let viewer = new Potree.Viewer(parentDiv);
                viewer.setEDLEnabled(true);
                viewer.setFOV(60);
                viewer.setPointBudget(1000000);
                viewer.setClipTask(Potree.ClipTask.SHOW_INSIDE);
                viewer.loadSettingsFromURL();
                viewer.useHQ = true;

                viewer.setControls(viewer.orbitControls);

                viewer.loadGUI(() => {
                    viewer.setLanguage("en");
                });

                // Load and add point cloud to scene
                Potree.loadPointCloud(url, "Point Cloud", authHeader)
                    .then(
                        (e) => {
                            let pointcloud = e.pointcloud;
                            let material = pointcloud.material;

                            material.activeAttributeName = "rgba";
                            material.minSize = 2;
                            material.pointSizeType = Potree.PointSizeType.FIXED;

                            viewer.scene.addPointCloud(pointcloud);

                            viewer.fitToScreen();
                        },
                        (e) => console.err("ERROR: ", e)
                    )
                    .catch((error) => {
                        setLoaded(true);
                        setShowNoAssetMessage(!showNoAssetMessage);
                    });
            }, 100);
        };
        if (!loaded && assetKey !== "") {
            loadAsset();
            setLoaded(true);
        }
    }, [config, loaded, assetKey, showNoAssetMessage]);

    return (
        <div style={{ position: "relative", height: "100%" }} id="potree-root">
            <div>
                {showNoAssetMessage && (
                    <div
                        style={{
                            color: "white",
                            fontSize: "1.5em",
                            lineHeight: "1.5",
                            width: "100%",
                            position: "absolute",
                            top: "50%",
                            left: "50%",
                            transform: "translate(-50%, -50%)",
                        }}
                    >
                        Visualizer files not currently available
                        <br></br>
                        Please run the Point Cloud Visualizer Pipeline to enable visualization for
                        this asset
                    </div>
                )}
            </div>
            <div>
                {!showNoAssetMessage && (
                    <div id="potree_container">
                        <div id="potree_render_area" ref={engineElement}></div>
                        <div id="potree_sidebar_container"></div>
                    </div>
                )}
            </div>
        </div>
    );
}
