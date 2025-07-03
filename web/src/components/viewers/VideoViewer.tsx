/* eslint-disable jsx-a11y/media-has-caption */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../services/APIService";

interface VideoViewerProps {
    assetId: string;
    databaseId: string;
    assetKey: string;
    versionId?: string;
}

export default function VideoViewer({
    assetId,
    databaseId,
    assetKey,
    versionId,
}: VideoViewerProps) {
    const [url, setUrl] = useState<string>("");
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);

    useEffect(() => {
        const fetchVideoUrl = async () => {
            setLoading(true);
            try {
                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        // Handle error
                        console.error("Error fetching video:", response[1]);
                        setError(`Failed to load video: ${response[1]}`);
                    } else {
                        // Set the pre-signed URL
                        setUrl(response[1]);
                        setError(null);
                    }
                } else {
                    setError("Invalid response from server");
                }
            } catch (err) {
                console.error("Error in video viewer:", err);
                setError("Failed to load video");
            } finally {
                setLoading(false);
            }
        };

        fetchVideoUrl();
    }, [assetId, databaseId, assetKey, versionId]);

    if (loading) {
        return (
            <div className="video-loading" style={{ textAlign: "center", padding: "2rem" }}>
                Loading video...
            </div>
        );
    }

    if (error) {
        return (
            <div
                className="video-error"
                style={{ textAlign: "center", padding: "2rem", color: "red" }}
            >
                {error}
            </div>
        );
    }

    return (
        <div className="video-container" style={{ width: "100%", height: "100%" }}>
            <video
                controls
                autoPlay={false}
                style={{ maxWidth: "100%", maxHeight: "100%", height: "100%" }}
                src={url}
                onError={() => setError("Failed to play video")}
            >
                Your browser does not support the video tag.
            </video>
        </div>
    );
}
