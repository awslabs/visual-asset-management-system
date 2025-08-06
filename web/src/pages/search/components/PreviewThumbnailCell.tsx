/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, Spinner } from "@cloudscape-design/components";
import { downloadAsset, fetchAsset } from "../../../services/APIService";
import "./PreviewThumbnailCell.css";

interface PreviewThumbnailCellProps {
    assetId: string;
    databaseId: string;
    onOpenFullPreview: (previewUrl: string, assetName: string, previewKey: string) => void;
    assetName: string;
}

/**
 * Component that displays a thumbnail preview of an asset in the search results
 */
export const PreviewThumbnailCell: React.FC<PreviewThumbnailCellProps> = ({
    assetId,
    databaseId,
    onOpenFullPreview,
    assetName,
}) => {
    const [url, setUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<boolean>(false);
    const [assetPreviewKey, setAssetPreviewKey] = useState<string | null>(null);

    useEffect(() => {
        // Reset states when props change
        setUrl(null);
        setLoading(true);
        setError(false);

        const loadPreviewImage = async () => {
            try {
                // First, fetch the asset details to get the previewLocation
                const assetDetails = await fetchAsset({ databaseId, assetId, showArchived: false });

                if (!assetDetails || typeof assetDetails === "string") {
                    console.error("Error fetching asset details:", assetDetails);
                    setError(true);
                    setLoading(false);
                    return;
                }

                // Get the preview key from the asset details
                const assetPreviewKey =
                    assetDetails.previewLocation?.Key ||
                    assetDetails.previewLocation?.key ||
                    assetDetails.previewFile;

                if (!assetPreviewKey) {
                    console.log(`No preview key found for asset ${assetId}`);
                    setError(true);
                    setLoading(false);
                    return;
                }

                console.log(`Loading preview for asset ${assetId} with key: ${assetPreviewKey}`);

                // Now download the preview image
                // If the preview key comes from previewFile, use assetFile download type
                const downloadType =
                    assetDetails.previewFile === assetPreviewKey ? "assetFile" : "assetPreview";

                const response = await downloadAsset({
                    databaseId,
                    assetId,
                    key: assetPreviewKey,
                    versionId: "",
                    downloadType: downloadType,
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading preview:", response[1]);
                        setError(true);
                    } else {
                        setUrl(response[1]);
                        // Store the preview key for later use
                        setAssetPreviewKey(assetPreviewKey);
                    }
                } else {
                    setError(true);
                }
            } catch (err) {
                console.error(`Error loading preview for asset ${assetId}:`, err);
                setError(true);
            } finally {
                setLoading(false);
            }
        };

        // Only attempt to load if we have both assetId and databaseId
        if (assetId && databaseId) {
            loadPreviewImage();
        } else {
            console.log(`Missing required parameters for asset ${assetId}`);
            setError(true);
            setLoading(false);
        }
    }, [assetId, databaseId]);

    // Handle image load error
    const handleImageError = () => {
        setError(true);
    };

    // If we're not loading and there's an error or no URL, show "No preview available"
    if (!loading && (error || !url)) {
        return (
            <Box padding="s" textAlign="center" className="preview-thumbnail-no-preview">
                <div>No preview available</div>
            </Box>
        );
    }

    return (
        <Box padding="s" className="preview-thumbnail-container">
            {loading && (
                <div className="preview-thumbnail-loading">
                    <Spinner size="normal" />
                </div>
            )}

            {!loading && error && (
                <div className="preview-thumbnail-error">
                    <div>No preview available</div>
                </div>
            )}

            {!loading && !error && url && (
                <div className="preview-thumbnail">
                    <img
                        src={url}
                        alt={`Preview of ${assetName}`}
                        onError={handleImageError}
                        onClick={() => onOpenFullPreview(url, assetName, assetPreviewKey || "")}
                        className="preview-thumbnail-image"
                    />
                </div>
            )}
        </Box>
    );
};

export default PreviewThumbnailCell;
