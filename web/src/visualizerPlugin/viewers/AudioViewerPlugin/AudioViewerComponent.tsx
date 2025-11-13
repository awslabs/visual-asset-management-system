/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

const AudioViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
}) => {
    const [audioUrl, setAudioUrl] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const loadAudio = async () => {
            if (!assetKey) return;

            try {
                setLoading(true);
                setError(null);

                console.log("AudioViewerComponent loading file:", {
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
                        console.error("Error downloading audio file:", response);
                        throw new Error("Failed to download audio file");
                    } else {
                        console.log("Successfully loaded audio URL:", response[1]);
                        setAudioUrl(response[1]);
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in audio download:", error);
                setError(error instanceof Error ? error.message : "Failed to load audio");
            } finally {
                setLoading(false);
            }
        };

        loadAudio();
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
                Loading audio...
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
                flexDirection: "column",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
                padding: "20px",
            }}
        >
            <div
                style={{
                    marginBottom: "20px",
                    fontSize: "18px",
                    fontWeight: "bold",
                    color: "#333",
                }}
            ></div>
            <audio
                src={audioUrl}
                controls
                style={{
                    width: "100%",
                    maxWidth: "500px",
                }}
                onError={() => setError("Failed to load audio file")}
            >
                Your browser does not support the audio tag.
            </audio>
        </div>
    );
};

export default AudioViewerComponent;
