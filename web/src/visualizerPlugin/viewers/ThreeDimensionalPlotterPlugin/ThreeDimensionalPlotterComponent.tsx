/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

const ThreeDimensionalPlotterComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
}) => {
    const [loaded, setLoaded] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [dataUrl, setDataUrl] = useState<string>("");

    useEffect(() => {
        const loadAsset = async () => {
            if (!assetKey) return;

            try {
                setError(null);

                console.log("ThreeDimensionalPlotterComponent loading file:", {
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
                        throw new Error("Failed to download data file");
                    } else {
                        setDataUrl(response[1]);
                        console.log("Successfully loaded data URL:", response[1]);
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error loading 3D plot data:", error);
                setError(error instanceof Error ? error.message : "Failed to load data");
            }
        };

        if (!loaded && assetKey !== "") {
            loadAsset();
            setLoaded(true);
        }
    }, [loaded, assetKey, assetId, databaseId, versionId]);

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

    if (!loaded || !dataUrl) {
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
                Loading 3D plot data...
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
            >
                3D Data Plotter
            </div>
            <div
                style={{
                    padding: "20px",
                    border: "2px dashed #ccc",
                    borderRadius: "8px",
                    textAlign: "center",
                    color: "#666",
                }}
            >
                <p>3D plotting functionality is being loaded...</p>
                <p style={{ fontSize: "14px", marginTop: "10px" }}>
                    File: {assetKey?.split("/").pop()}
                </p>
                <p style={{ fontSize: "12px", marginTop: "5px" }}>Data URL: {dataUrl}</p>
            </div>
        </div>
    );
};

export default ThreeDimensionalPlotterComponent;
