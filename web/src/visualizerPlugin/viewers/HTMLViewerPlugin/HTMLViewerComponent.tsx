/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

const HTMLViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
}) => {
    const [htmlUrl, setHtmlUrl] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const loadHTML = async () => {
            if (!assetKey) return;

            try {
                setLoading(true);
                setError(null);

                console.log("HTMLViewerComponent loading file:", {
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
                        console.error("Error downloading HTML file:", response);
                        throw new Error("Failed to download HTML file");
                    } else {
                        console.log("Successfully loaded HTML URL:", response[1]);
                        setHtmlUrl(response[1]);
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in HTML download:", error);
                setError(error instanceof Error ? error.message : "Failed to load HTML");
            } finally {
                setLoading(false);
            }
        };

        loadHTML();
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
                Loading HTML document...
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
                width: "100%",
                height: "100%",
                border: "none",
            }}
        >
            <iframe
                src={htmlUrl}
                style={{
                    width: "100%",
                    height: "100%",
                    border: "none",
                }}
                title="HTML Document Viewer"
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                onError={() => setError("Failed to load HTML document")}
            />
        </div>
    );
};

export default HTMLViewerComponent;
