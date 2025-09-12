/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Box, Button, Modal, SpaceBetween, Spinner } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";
import "./AssetPreviewModal.css";

interface AssetPreviewModalProps {
    visible: boolean;
    onDismiss: () => void;
    assetId: string;
    databaseId: string;
    previewKey?: string;
    assetName: string;
}

/**
 * Modal component that displays a full-size preview of an asset
 * Includes controls for different view modes (normal, wide, fullscreen)
 */
export const AssetPreviewModal: React.FC<AssetPreviewModalProps> = ({
    visible,
    onDismiss,
    assetId,
    databaseId,
    previewKey,
    assetName,
}) => {
    const [url, setUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<boolean>(false);
    const [viewerMode, setViewerMode] = useState<"normal" | "wide" | "fullscreen">("normal");

    useEffect(() => {
        // Reset states when modal opens
        if (visible) {
            setLoading(true);
            setError(false);
            setUrl(null);

            // Don't attempt to load if no preview key is provided
            if (!previewKey) {
                console.log("No preview key provided to AssetPreviewModal");
                setLoading(false);
                setError(true);
                return;
            }

            console.log("Loading preview modal with key:", previewKey);
            const loadPreviewImage = async () => {
                try {
                    const response = await downloadAsset({
                        databaseId,
                        assetId,
                        key: previewKey,
                        versionId: "",
                        downloadType: "assetPreview",
                    });

                    if (response !== false && Array.isArray(response)) {
                        if (response[0] === false) {
                            console.error("Error downloading preview:", response[1]);
                            setError(true);
                        } else {
                            setUrl(response[1]);
                        }
                    } else {
                        setError(true);
                    }
                } catch (err) {
                    console.error("Error loading preview:", err);
                    setError(true);
                } finally {
                    setLoading(false);
                }
            };

            loadPreviewImage();
        }
    }, [visible, assetId, databaseId, previewKey]);

    // Handle image load error
    const handleImageError = () => {
        setError(true);
    };

    // Get appropriate modal size based on viewer mode
    const getModalSize = (): "small" | "medium" | "large" | "max" => {
        switch (viewerMode) {
            case "wide":
                return "max";
            case "fullscreen":
                return "max";
            default:
                return "large";
        }
    };

    // Calculate image container style based on viewer mode
    const getImageContainerStyle = () => {
        const baseStyle = {
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            overflow: "auto",
            margin: "0 auto",
        };

        switch (viewerMode) {
            case "wide":
                return {
                    ...baseStyle,
                    height: "60vh",
                    maxWidth: "1100px",
                };
            case "fullscreen":
                return {
                    ...baseStyle,
                    height: "80vh",
                    maxWidth: "100%",
                };
            default:
                return {
                    ...baseStyle,
                    height: "50vh",
                    maxWidth: "700px",
                };
        }
    };

    // Calculate image style based on viewer mode
    const getImageStyle = () => {
        const baseStyle = {
            maxWidth: "100%",
            objectFit: "contain" as const,
        };

        switch (viewerMode) {
            case "fullscreen":
                return {
                    ...baseStyle,
                    maxHeight: "75vh",
                };
            default:
                return {
                    ...baseStyle,
                    maxHeight: "45vh",
                };
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Preview: ${assetName}`}
            size={getModalSize()}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss}>
                            Close
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <div className={`asset-preview-modal-content viewer-mode-${viewerMode}`}>
                <div className="asset-preview-image-container">
                    {loading && (
                        <div className="asset-preview-loading" style={{ textAlign: "center" }}>
                            <Box margin={{ bottom: "l" }}>
                                <Spinner size="large" />
                            </Box>
                            <div>Loading preview...</div>
                        </div>
                    )}

                    {!loading && error && (
                        <div className="asset-preview-error" style={{ textAlign: "center" }}>
                            <Box margin={{ bottom: "l" }}>
                                <img
                                    src="/error-icon.png"
                                    alt="Error"
                                    style={{ width: "50px", height: "50px" }}
                                />
                            </Box>
                            <div>Preview not available</div>
                        </div>
                    )}

                    {!loading && !error && url && (
                        <img
                            src={url}
                            alt={`Preview of ${assetName}`}
                            onError={handleImageError}
                            style={getImageStyle()}
                        />
                    )}
                </div>

                <div
                    className="asset-preview-controls"
                    style={{
                        display: "flex",
                        justifyContent: "center",
                        marginTop: "1rem",
                    }}
                >
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            iconName="view-vertical"
                            variant={viewerMode === "normal" ? "primary" : "normal"}
                            onClick={() => setViewerMode("normal")}
                        >
                            Normal
                        </Button>
                        <Button
                            iconName="view-horizontal"
                            variant={viewerMode === "wide" ? "primary" : "normal"}
                            onClick={() => setViewerMode("wide")}
                        >
                            Wide
                        </Button>
                        <Button
                            iconName="external"
                            variant={viewerMode === "fullscreen" ? "primary" : "normal"}
                            onClick={() => setViewerMode("fullscreen")}
                        >
                            Fullscreen
                        </Button>
                    </SpaceBetween>
                </div>
            </div>
        </Modal>
    );
};

export default AssetPreviewModal;
