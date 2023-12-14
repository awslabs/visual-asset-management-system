/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import * as OV from "online-3d-viewer";
import { getPresignedKey } from "../../common/auth/s3";

export default function ModelViewer(props) {
    const engineElement = useRef(null);
    const { assetId, databaseId, assetKey } = props;
    const [loaded, setLoaded] = useState(false);

    useEffect(() => {
        const loadAsset = async () => {
            await getPresignedKey(assetId, databaseId, assetKey).then((remoteFileUrl) => {
                engineElement.current.setAttribute("model", remoteFileUrl);
                setTimeout(() => {
                    let parentDiv = engineElement.current;
                    let viewer = new OV.EmbeddedViewer(parentDiv, {
                        backgroundColor: new OV.RGBAColor(182, 182, 182, 182),
                        defaultColor: new OV.RGBColor(200, 200, 200),
                        edgeSettings: new OV.EdgeSettings(true, new OV.RGBColor(0, 0, 255), 1),
                    });
                    viewer.LoadModelFromUrlList([remoteFileUrl]);
                }, 100);
            });
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
