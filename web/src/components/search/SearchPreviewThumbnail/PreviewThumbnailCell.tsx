/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, Spinner } from "@cloudscape-design/components";
import { downloadAsset, fetchAsset } from "../../../services/APIService";
import cacheManager from "./SearchCacheManager";
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
        const loadPreviewImage = async () => {
            try {
                // Create cache key for asset details
                const assetCacheKey = `asset:${databaseId}:${assetId}`;
                
                // Check asset cache first BEFORE resetting state
                const cachedAsset = cacheManager.getAsset(assetCacheKey);
                
                let assetPreviewKey: string;
                let downloadType: 'assetPreview' | 'assetFile';
                
                if (cachedAsset) {
                    // Use cached asset details
                    console.log(`[Cache HIT] Asset details for ${assetId}`);
                    assetPreviewKey = cachedAsset.previewKey;
                    downloadType = cachedAsset.downloadType;
                    
                    // Check if cached asset has no preview
                    if (!assetPreviewKey || assetPreviewKey === '') {
                        console.log(`[Cache HIT] Asset ${assetId} has no preview (cached)`);
                        setUrl(null);
                        setError(true);
                        setLoading(false);
                        return;
                    }
                    
                    // Check preview cache
                    const previewCacheKey = `preview:${databaseId}:${assetId}:${assetPreviewKey}`;
                    const cachedPreview = cacheManager.getPreview(previewCacheKey);
                    
                    if (cachedPreview) {
                        // Use cached preview image - set state directly without loading
                        console.log(`[Cache HIT] Preview image for ${assetId}`);
                        setUrl(cachedPreview.dataUrl);
                        setAssetPreviewKey(assetPreviewKey);
                        setLoading(false);
                        setError(false);
                        return;
                    }
                    
                    // Preview not cached, but we have asset details
                    setAssetPreviewKey(assetPreviewKey);
                } else {
                    // Reset states when we need to fetch
                    setUrl(null);
                    setLoading(true);
                    setError(false);
                    // Cache miss - fetch asset details from API
                    console.log(`[Cache MISS] Asset details for ${assetId}`);
                    const assetDetails = await fetchAsset({ databaseId, assetId, showArchived: false });
                    console.log(`[DEBUG] fetchAsset returned:`, assetDetails);

                    if (!assetDetails || typeof assetDetails === "string") {
                        console.error("Error fetching asset details:", assetDetails);
                        setError(true);
                        setLoading(false);
                        return;
                    }

                    // Get the preview key from the asset details
                    assetPreviewKey =
                        assetDetails.previewLocation?.Key ||
                        assetDetails.previewLocation?.key ||
                        assetDetails.previewFile;

                    console.log(`[DEBUG] Extracted preview key: ${assetPreviewKey}`);

                    if (!assetPreviewKey) {
                        console.log(`No preview key found for asset ${assetId}`);
                        
                        // Cache that this asset has no preview to avoid repeated API calls
                        cacheManager.setAsset(assetCacheKey, {
                            previewKey: '', // Empty string indicates no preview
                            downloadType: 'assetPreview',
                        });
                        
                        setError(true);
                        setLoading(false);
                        return;
                    }

                    // Determine download type
                    downloadType = assetDetails.previewFile === assetPreviewKey ? "assetFile" : "assetPreview";
                    
                    console.log(`[DEBUG] About to call setAsset with key: ${assetCacheKey}`);
                    // Cache the asset details
                    cacheManager.setAsset(assetCacheKey, {
                        previewKey: assetPreviewKey,
                        downloadType: downloadType,
                    });
                    console.log(`[DEBUG] setAsset completed`);
                    
                    setAssetPreviewKey(assetPreviewKey);
                }

                console.log(`Loading preview for asset ${assetId} with key: ${assetPreviewKey}`);

                // Check preview cache before downloading
                const previewCacheKey = `preview:${databaseId}:${assetId}:${assetPreviewKey}`;
                const cachedPreview = cacheManager.getPreview(previewCacheKey);
                
                if (cachedPreview) {
                    // Use cached preview image
                    console.log(`[Cache HIT] Preview image for ${assetId} (after asset fetch)`);
                    setUrl(cachedPreview.dataUrl);
                    setLoading(false);
                    return;
                }

                // Cache miss - download the preview image
                console.log(`[Cache MISS] Preview image for ${assetId}`);
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
                        const imageDataUrl = response[1];
                        setUrl(imageDataUrl);
                        
                        // Cache the preview image
                        const imageSize = cacheManager.estimateDataUrlSize(imageDataUrl);
                        cacheManager.setPreview(previewCacheKey, { dataUrl: imageDataUrl }, imageSize);
                        console.log(`[Cache SET] Preview image for ${assetId} (${(imageSize / 1024).toFixed(2)} KB)`);
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

    // If we're not loading and there's an error or no URL, show blank
    if (!loading && (error || !url)) {
        return <Box padding="s" />;
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
                    <div></div>
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
