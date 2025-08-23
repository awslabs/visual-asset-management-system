/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

const VideoViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
}) => {
    const [videoUrl, setVideoUrl] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const loadVideo = async () => {
            if (!assetKey) return;

            try {
                setLoading(true);
                setError(null);

                console.log("VideoViewerComponent loading file:", {
                    assetId,
                    databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading video file:", response);
                        throw new Error("Failed to download video file");
                    } else {
                        console.log("Successfully loaded video URL:", response[1]);
                        setVideoUrl(response[1]);
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in video download:", error);
                setError(error instanceof Error ? error.message : "Failed to load video");
            } finally {
                setLoading(false);
            }
        };

        loadVideo();
    }, [assetId, assetKey, databaseId, versionId]);

    if (loading) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    fontSize: "16px",
                    color: "#666",
                }}
            >
                Loading video...
            </div>
        );
    }

    if (error) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    fontSize: "16px",
                    color: "#d13212",
                }}
            >
                Error: {error}
            </div>
        );
    }

    return (
        <div
            style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
                backgroundColor: "#000",
            }}
        >
            <video
                src={videoUrl}
                controls
                style={{
                    maxWidth: "100%",
                    maxHeight: "100%",
                    width: "auto",
                    height: "auto",
                }}
                onError={() => setError("Failed to load video file")}
            >
                Your browser does not support the video tag.
            </video>
        </div>
    );
};

export default VideoViewerComponent;
