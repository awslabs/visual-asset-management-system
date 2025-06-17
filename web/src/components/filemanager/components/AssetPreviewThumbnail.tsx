/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, Button, Spinner } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";
import "./AssetPreviewThumbnail.css";

interface AssetPreviewThumbnailProps {
  assetId: string;
  databaseId: string;
  previewKey?: string;
  onOpenFullPreview: () => void;
}

/**
 * Component that displays a thumbnail preview of an asset
 * Used in the EnhancedFileManager when the top-level Asset Node is selected
 */
export const AssetPreviewThumbnail: React.FC<AssetPreviewThumbnailProps> = ({
  assetId,
  databaseId,
  previewKey,
  onOpenFullPreview
}) => {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<boolean>(false);

  useEffect(() => {
    // Reset states when props change
    setUrl(null);
    setLoading(true);
    setError(false);
    
    // Don't attempt to load if no preview key is provided
    if (!previewKey) {
      console.log("No preview key provided to AssetPreviewThumbnail");
      setLoading(false);
      setError(true);
      return;
    }

    console.log("Loading preview with key:", previewKey);
    const loadPreviewImage = async () => {
      try {
        const response = await downloadAsset({
          databaseId,
          assetId,
          key: previewKey,
          versionId: "",
          downloadType: "assetPreview"
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
  }, [assetId, databaseId, previewKey]);

  // Handle image load error
  const handleImageError = () => {
    setError(true);
  };

  // If no preview key is provided
  if (!previewKey) {
    console.log("No preview key available for rendering");
    return (
      <Box padding="s" textAlign="center">
        <div>No preview available for this asset</div>
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
                alt="Asset preview" 
                onError={handleImageError}
                onClick={onOpenFullPreview}
                style={{ 
                  maxWidth: "100%", 
                  maxHeight: "150px", 
                  cursor: "pointer" 
                }}
              />
            </div>
            <div className="asset-preview-actions">
              <Button 
                iconName="external"
                variant="link"
                onClick={onOpenFullPreview}
              >
                View full preview
              </Button>
            </div>
          </>
        )}
      </div>
    </Box>
  );
};

export default AssetPreviewThumbnail;
