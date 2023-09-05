/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { getPresignedKey } from "../../common/auth/s3";

export default function HTMLViewer(props) {
    const shadowElement = useRef(null);
    const { assetId, databaseId, assetKey } = props;
    const [loaded, setLoaded] = useState(false);

    useEffect(() => {
        const loadAsset = async () => {
            await getPresignedKey(assetId, databaseId, assetKey).then((remoteFileUrl) => {
                const request = new XMLHttpRequest();
                request.onload = function (e) {
                    const cleanContent = DOMPurify.sanitize(request.response);
                    const containerElement = document.createElement("div");
                    containerElement.innerHTML = cleanContent;
                    const htmlContent = document.importNode(containerElement, true);
                    const shadowRoot = shadowElement.current.attachShadow({ mode: "open" });
                    shadowRoot.appendChild(htmlContent);
                };
                request.open("get", remoteFileUrl, true);
                request.send();
            });
        };
        if (!loaded && assetKey !== "") {
            loadAsset();
            setLoaded(true);
        }
    }, [loaded, assetKey]);

    return (
        <div
            style={{
                overflow: "auto",
                backgroundColor: "white",
                height: "calc(100% - 30px)",
                padding: "15px 20px",
            }}
        >
            <div ref={shadowElement} />
        </div>
    );
}
