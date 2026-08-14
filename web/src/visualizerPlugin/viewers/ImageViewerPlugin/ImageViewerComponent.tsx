/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Button } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";
import Synonyms from "../../../synonyms";
import { ViewerPluginProps } from "../../core/types";

const ImageViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
    onDeletePreview,
    isPreviewFile = false,
}) => {
    // No placeholder source: an <img> rendered before the presigned URL resolves would issue a real
    // request for it, and the file does not exist in the web bundle — the browser reported a failed
    // load for every image opened, and that failure also tripped the error state below. The image
    // element is not rendered until there is a URL to give it.
    const [url, setUrl] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState<any>(null);

    useEffect(() => {
        setUrl("");
        setLoading(true);
        setErr(null);

        const loadImage = async () => {
            console.log("ImageViewerComponent loading file:", {
                assetId,
                databaseId,
                key: assetKey,
                versionId: versionId,
                assetVersionId: assetVersionId,
                downloadType: "assetFile",
                isPreviewFile,
            });

            try {
                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey || "",
                    versionId: versionId,
                    assetVersionId: assetVersionId as any,
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading file:", response);
                        throw new Error("Failed to download file");
                    } else {
                        console.log("Successfully loaded file URL:", response[1]);
                        setUrl(response[1]);
                        return;
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in image download:", error);
                setErr(error);
            } finally {
                // Also covers the success path's early return, so the loading state always clears.
                setLoading(false);
            }
        };

        if (assetKey) {
            loadImage();
        } else {
            // Nothing to fetch — do not sit on the loading state forever.
            setLoading(false);
        }
    }, [assetId, assetKey, databaseId, versionId, assetVersionId, isPreviewFile]);

    const fallback = (error: any) => {
        console.log("Image load error:", error);
        if (err === null) {
            setErr(error);
        }
    };

    const centered: React.CSSProperties = {
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        height: "100%",
        fontSize: "16px",
    };

    if (loading) {
        return (
            <div style={{ ...centered, color: "var(--vams-text-secondary)" }}>Loading image...</div>
        );
    }

    // The error state was previously set but never rendered, so a failed download showed a broken
    // image icon with no explanation.
    if (err || !url) {
        return (
            <div style={{ ...centered, color: "var(--vams-color-error)" }}>
                {`Unable to load this image ${Synonyms.asset} file.`}
            </div>
        );
    }

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
                alt={`${Synonyms.Asset} preview`}
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
