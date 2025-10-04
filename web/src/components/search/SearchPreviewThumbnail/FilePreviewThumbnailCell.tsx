/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, Spinner } from "@cloudscape-design/components";
import { downloadAsset, fetchFileInfo } from "../../../services/APIService";
import { previewFileFormats } from "../../../common/constants/fileFormats";
import cacheManager from "./SearchCacheManager";
import "./PreviewThumbnailCell.css"; // Reuse the same CSS as PreviewThumbnailCell

interface FilePreviewThumbnailCellProps {
    databaseId: string;
    assetId: string;
    fileKey: string;
    fileName: string;
    fileSize?: number;
    onOpenFullPreview: (
        previewUrl: string,
        fileName: string,
        previewKey: string,
        downloadType?: "assetPreview" | "assetFile"
    ) => void;
}

/**
 * Component that displays a thumbnail preview of a file in the search results
 */
export const FilePreviewThumbnailCell: React.FC<FilePreviewThumbnailCellProps> = ({
    databaseId,
    assetId,
    fileKey,
    fileName,
    fileSize,
    onOpenFullPreview,
}) => {
    const [url, setUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<boolean>(false);
    const [previewKey, setPreviewKey] = useState<string | null>(null);
    const [downloadType, setDownloadType] = useState<"assetPreview" | "assetFile">("assetFile");

    useEffect(() => {
        const loadPreviewImage = async () => {
            try {
                // Create cache key for file details
                const fileCacheKey = `file:${databaseId}:${assetId}:${fileKey}`;

                // Check file cache first BEFORE resetting state
                const cachedFile = cacheManager.getFile(fileCacheKey);

                let filePreviewKey: string;
                let currentDownloadType: "assetPreview" | "assetFile";
                let keyToUse: string;

                if (cachedFile) {
                    // Use cached file details
                    console.log(`[Cache HIT] File details for ${fileName}`);

                    if (!cachedFile.hasPreview) {
                        // File has no preview (cached)
                        console.log(`File ${fileName} has no preview (cached)`);
                        setUrl(null);
                        setError(true);
                        setLoading(false);
                        return;
                    }

                    filePreviewKey = cachedFile.previewKey;
                    currentDownloadType = cachedFile.downloadType;
                    keyToUse = filePreviewKey;

                    // Check preview cache
                    const previewCacheKey = `preview:${databaseId}:${assetId}:${filePreviewKey}`;
                    const cachedPreview = cacheManager.getPreview(previewCacheKey);

                    if (cachedPreview) {
                        // Use cached preview image - set state directly without loading
                        console.log(`[Cache HIT] Preview image for ${fileName}`);
                        setUrl(cachedPreview.dataUrl);
                        setPreviewKey(filePreviewKey);
                        setDownloadType(currentDownloadType);
                        setLoading(false);
                        setError(false);
                        return;
                    }

                    // Preview not cached, but we have file details
                    setPreviewKey(filePreviewKey);
                    setDownloadType(currentDownloadType);
                } else {
                    // Reset states when we need to fetch
                    setUrl(null);
                    setLoading(true);
                    setError(false);
                    setPreviewKey(null);
                    // Cache miss - fetch file info from API
                    console.log(`[Cache MISS] File details for ${fileName}`);
                    const [success, fileInfo] = await fetchFileInfo({
                        databaseId,
                        assetId,
                        fileKey,
                    });

                    if (!success || !fileInfo) {
                        console.error("Error fetching file info:", fileInfo);
                        setError(true);
                        setLoading(false);
                        return;
                    }

                    // Check if the file has a previewFile
                    filePreviewKey = fileInfo.previewFile;
                    currentDownloadType = "assetFile";
                    keyToUse = fileKey;

                    if (filePreviewKey) {
                        // If there's a dedicated preview file, use it
                        keyToUse = filePreviewKey;
                        currentDownloadType = "assetPreview";
                        setPreviewKey(filePreviewKey);
                        setDownloadType(currentDownloadType);

                        // Cache the file details
                        cacheManager.setFile(fileCacheKey, {
                            previewKey: filePreviewKey,
                            downloadType: currentDownloadType,
                            hasPreview: true,
                        });
                    } else {
                        // If no preview file, check if the file itself is a previewable image
                        const fileExt = fileName.substring(fileName.lastIndexOf(".")).toLowerCase();

                        // Check if file extension is in previewFileFormats
                        const isPreviewFormat = previewFileFormats.includes(fileExt);

                        // Check if file size is less than 5MB
                        const isSizeOk = fileSize !== undefined && fileSize < 5 * 1024 * 1024;

                        if (!isPreviewFormat || !isSizeOk) {
                            console.log(
                                `File ${fileName} is not previewable (format: ${isPreviewFormat}, size OK: ${isSizeOk})`
                            );

                            // Cache that this file has no preview
                            cacheManager.setFile(fileCacheKey, {
                                previewKey: "",
                                downloadType: "assetFile",
                                hasPreview: false,
                            });

                            setError(true);
                            setLoading(false);
                            return;
                        }

                        // Use the file itself as the preview
                        setPreviewKey(fileKey);
                        setDownloadType("assetFile");

                        // Cache the file details
                        cacheManager.setFile(fileCacheKey, {
                            previewKey: fileKey,
                            downloadType: "assetFile",
                            hasPreview: true,
                        });
                    }
                }

                console.log(`Loading preview for file ${fileName} with key: ${keyToUse}`);

                // Check preview cache before downloading
                const previewCacheKey = `preview:${databaseId}:${assetId}:${keyToUse}`;
                const cachedPreview = cacheManager.getPreview(previewCacheKey);

                if (cachedPreview) {
                    // Use cached preview image
                    console.log(`[Cache HIT] Preview image for ${fileName} (after file fetch)`);
                    setUrl(cachedPreview.dataUrl);
                    setLoading(false);
                    return;
                }

                // Cache miss - download the preview image
                console.log(`[Cache MISS] Preview image for ${fileName}`);
                const response = await downloadAsset({
                    databaseId,
                    assetId,
                    key: keyToUse,
                    versionId: "",
                    downloadType: currentDownloadType,
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
                        cacheManager.setPreview(
                            previewCacheKey,
                            { dataUrl: imageDataUrl },
                            imageSize
                        );
                        console.log(
                            `[Cache SET] Preview image for ${fileName} (${(
                                imageSize / 1024
                            ).toFixed(2)} KB)`
                        );
                    }
                } else {
                    setError(true);
                }
            } catch (err) {
                console.error(`Error loading preview for file ${fileName}:`, err);
                setError(true);
            } finally {
                setLoading(false);
            }
        };

        // Only attempt to load if we have all required parameters
        if (databaseId && assetId && fileKey && fileName) {
            loadPreviewImage();
        } else {
            console.log(`Missing required parameters for file ${fileName}`);
            setError(true);
            setLoading(false);
        }
    }, [databaseId, assetId, fileKey, fileName, fileSize]);

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
                        alt={`Preview of ${fileName}`}
                        onError={handleImageError}
                        onClick={() =>
                            onOpenFullPreview(url, fileName, previewKey || "", downloadType)
                        }
                        className="preview-thumbnail-image"
                    />
                </div>
            )}
        </Box>
    );
};

export default FilePreviewThumbnailCell;
