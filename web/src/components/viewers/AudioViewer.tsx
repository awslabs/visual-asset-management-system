/* eslint-disable jsx-a11y/media-has-caption */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../services/APIService";

interface AudioViewerProps {
    assetId: string;
    databaseId: string;
    assetKey: string;
    versionId?: string;
}

export default function AudioViewer({
    assetId,
    databaseId,
    assetKey,
    versionId,
}: AudioViewerProps) {
    const [url, setUrl] = useState<string>("");
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);

    useEffect(() => {
        const fetchAudioUrl = async () => {
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
                        console.error("Error fetching audio:", response[1]);
                        setError(`Failed to load audio: ${response[1]}`);
                    } else {
                        // Set the pre-signed URL
                        setUrl(response[1]);
                        setError(null);
                    }
                } else {
                    setError("Invalid response from server");
                }
            } catch (err) {
                console.error("Error in audio viewer:", err);
                setError("Failed to load audio");
            } finally {
                setLoading(false);
            }
        };

        fetchAudioUrl();
    }, [assetId, databaseId, assetKey, versionId]);

    if (loading) {
        return (
            <div className="audio-loading" style={{ textAlign: "center", padding: "2rem" }}>
                Loading audio...
            </div>
        );
    }

    if (error) {
        return (
            <div
                className="audio-error"
                style={{ textAlign: "center", padding: "2rem", color: "red" }}
            >
                {error}
            </div>
        );
    }

    return (
        <div
            className="audio-container"
            style={{ width: "100%", padding: "2rem", textAlign: "center" }}
        >
            <audio
                controls
                autoPlay={false}
                style={{ width: "100%", maxWidth: "500px" }}
                src={url}
                onError={() => setError("Failed to play audio")}
            >
                Your browser does not support the audio tag.
            </audio>
        </div>
    );
}
