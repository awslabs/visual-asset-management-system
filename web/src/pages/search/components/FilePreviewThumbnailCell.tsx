/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, Spinner } from "@cloudscape-design/components";
import { downloadAsset, fetchFileInfo } from "../../../services/APIService";
import { previewFileFormats } from "../../../common/constants/fileFormats";
import "./PreviewThumbnailCell.css"; // Reuse the same CSS as PreviewThumbnailCell

interface FilePreviewThumbnailCellProps {
    databaseId: string;
    assetId: string;
    fileKey: string;
    fileName: string;
    fileSize?: number;
    onOpenFullPreview: (previewUrl: string, fileName: string, previewKey: string) => void;
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

    useEffect(() => {
        // Reset states when props change
        setUrl(null);
        setLoading(true);
        setError(false);
        setPreviewKey(null);

        const loadPreviewImage = async () => {
            try {
                // First, fetch the file info to get the previewFile
                const [success, fileInfo] = await fetchFileInfo({ databaseId, assetId, fileKey });

                if (!success || !fileInfo) {
                    console.error("Error fetching file info:", fileInfo);
                    setError(true);
                    setLoading(false);
                    return;
                }

                // Check if the file has a previewFile
                let filePreviewKey = fileInfo.previewFile;
                let downloadType = "assetFile";
                let keyToUse = fileKey;

                if (filePreviewKey) {
                    // If there's a dedicated preview file, use it
                    keyToUse = filePreviewKey;
                    setPreviewKey(filePreviewKey);
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
                        setError(true);
                        setLoading(false);
                        return;
                    }

                    // Use the file itself as the preview
                    setPreviewKey(fileKey);
                }

                console.log(`Loading preview for file ${fileName} with key: ${keyToUse}`);

                // Now download the preview image
                const response = await downloadAsset({
                    databaseId,
                    assetId,
                    key: keyToUse,
                    versionId: "",
                    downloadType: downloadType,
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
                        alt={`Preview of ${fileName}`}
                        onError={handleImageError}
                        onClick={() => onOpenFullPreview(url, fileName, previewKey || "")}
                        className="preview-thumbnail-image"
                    />
                </div>
            )}
        </Box>
    );
};

export default FilePreviewThumbnailCell;
