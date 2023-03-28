/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { Storage } from "aws-amplify";
import DOMPurify from "dompurify";

export default function HTMLViewer(props) {
    const shadowElement = useRef(null);
    const { assetKey, ...rest } = props;
    const [loaded, setLoaded] = useState(false);

    useEffect(() => {
        let config = {
            download: false,
            expires: 10,
        };
        const loadAsset = async () => {
            await Storage.get(assetKey, config).then((remoteFileUrl) => {
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
