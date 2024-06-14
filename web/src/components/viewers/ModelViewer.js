/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import * as OV from "online-3d-viewer";
import { downloadAsset } from "../../services/APIService";

export default function ModelViewer(props) {
    const engineElement = useRef(null);
    const { assetId, databaseId, assetKey } = props;
    const [loaded, setLoaded] = useState(false);

    useEffect(() => {
        const loadAsset = async () => {
            try {
                console.log(assetKey);
                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey,
                    version: "",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        // TODO: error handling (response[1] has error message)
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
                console.error(error);
            }
        };

        if (!loaded && assetKey !== "") {
            loadAsset();
            setLoaded(true);
        }
    }, [loaded, assetKey, assetId, databaseId]);

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
