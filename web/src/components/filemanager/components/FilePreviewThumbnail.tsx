/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, Button, SpaceBetween, Spinner } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";
import "./AssetPreviewThumbnail.css"; // Reusing the same CSS as AssetPreviewThumbnail

interface FilePreviewThumbnailProps {
    assetId: string;
    databaseId: string;
    fileKey: string;
    onOpenFullPreview: (previewUrl: string) => void;
    isPreviewFile?: boolean;
    onDeletePreview?: () => void;
}

/**
 * Component that displays a thumbnail preview of a file
 * Used in the EnhancedFileManager when a previewable file node is selected
 */
export const FilePreviewThumbnail: React.FC<FilePreviewThumbnailProps> = ({
    assetId,
    databaseId,
    fileKey,
    onOpenFullPreview,
    isPreviewFile = false,
    onDeletePreview,
}) => {
    const [url, setUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<boolean>(false);

    useEffect(() => {
        // Reset states when props change
        setUrl(null);
        setLoading(true);
        setError(false);

        // Don't attempt to load if no file key is provided
        if (!fileKey) {
            console.log("No file key provided to FilePreviewThumbnail");
            setLoading(false);
            setError(true);
            return;
        }

        console.log("Loading file preview with key:", fileKey);
        const loadPreviewImage = async () => {
            try {
                const response = await downloadAsset({
                    databaseId,
                    assetId,
                    key: fileKey,
                    versionId: "",
                    downloadType: isPreviewFile ? "assetFile" : "assetFile", // Using "assetFile" for both regular files and preview files
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading file preview:", response[1]);
                        setError(true);
                    } else {
                        setUrl(response[1]);
                    }
                } else {
                    setError(true);
                }
            } catch (err) {
                console.error("Error loading file preview:", err);
                setError(true);
            } finally {
                setLoading(false);
            }
        };

        loadPreviewImage();
    }, [assetId, databaseId, fileKey]);

    // Handle image load error
    const handleImageError = () => {
        setError(true);
    };

    // If no file key is provided
    if (!fileKey) {
        console.log("No file key available for rendering");
        return (
            <Box padding="s" textAlign="center">
                <div>No preview available for this file</div>
            </Box>
        );
    }

    return (
        <Box padding="s">
            <div className="asset-preview-thumbnail-container">
                {loading && (
                    <div className="asset-preview-loading">
                        <Spinner size="normal" />
                        <div>Loading preview...</div>
                    </div>
                )}

                {!loading && error && (
                    <div className="asset-preview-error">
                        <div>Preview not available</div>
                    </div>
                )}

                {!loading && !error && url && (
                    <>
                        <div className="asset-preview-thumbnail">
                            <img
                                src={url}
                                alt="File preview"
                                onError={handleImageError}
                                onClick={() => onOpenFullPreview(url)}
                                style={{
                                    maxWidth: "100%",
                                    maxHeight: "150px",
                                    cursor: "pointer",
                                }}
                            />
                        </div>
                        <div className="asset-preview-actions">
                            <SpaceBetween direction="vertical" size="xs">
                                <Button
                                    iconName="external"
                                    variant="link"
                                    onClick={() => onOpenFullPreview(url)}
                                >
                                    View full preview
                                </Button>
                                {onDeletePreview && (
                                    <Button
                                        iconName="remove"
                                        variant="link"
                                        onClick={onDeletePreview}
                                    >
                                        Delete Preview File
                                    </Button>
                                )}
                            </SpaceBetween>
                        </div>
                    </>
                )}
            </div>
        </Box>
    );
};

export default FilePreviewThumbnail;
