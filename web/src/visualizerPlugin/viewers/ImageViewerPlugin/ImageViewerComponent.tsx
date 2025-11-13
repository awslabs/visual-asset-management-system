/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Button } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

const ImageViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    onDeletePreview,
    isPreviewFile = false,
}) => {
    const init = "placeholder.jpg";
    const [url, setUrl] = useState(init);
    const [err, setErr] = useState<any>(null);

    useEffect(() => {
        if (url !== init) {
            return;
        }

        const loadImage = async () => {
            console.log("ImageViewerComponent loading file:", {
                assetId,
                databaseId,
                key: assetKey,
                versionId: isPreviewFile ? "" : versionId || "",
                downloadType: "assetFile",
                isPreviewFile,
            });

            try {
                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey || "",
                    versionId: isPreviewFile ? "" : versionId || "", // Don't use versionId for preview files
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading file:", response);
                        throw new Error("Failed to download file");
                    } else {
                        console.log("Successfully loaded file URL:", response[1]);
                        setUrl(response[1]);
                        return; // Success - exit early
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in image download:", error);
                setErr(error);
            }
        };

        if (assetKey) {
            loadImage();
        }
    }, [assetId, assetKey, databaseId, url, versionId, isPreviewFile]);

    const fallback = (error: any) => {
        console.log("Image load error:", error);
        if (err === null) {
            setErr(error);
        }
    };

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                height: "100%",
            }}
        >
            <img
                src={url}
                style={{ maxWidth: "100%", maxHeight: "100%", height: "100%" }}
                onError={fallback}
                alt="Asset preview"
            />
            {onDeletePreview && (
                <div style={{ marginTop: "10px" }}>
                    <Button iconName="remove" variant="link" onClick={onDeletePreview}>
                        Delete Preview File
                    </Button>
                </div>
            )}
        </div>
    );
};

export default ImageViewerComponent;
