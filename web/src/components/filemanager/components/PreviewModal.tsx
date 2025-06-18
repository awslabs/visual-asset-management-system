import React, { useEffect, useState } from "react";
import { Box, Button, Modal, Spinner } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";

interface PreviewModalProps {
  visible: boolean;
  onDismiss: () => void;
  assetId: string;
  databaseId: string;
  previewKey?: string;
}

/**
 * Modal component for displaying a full-size preview of an asset
 */
const PreviewModal: React.FC<PreviewModalProps> = ({
  visible,
  onDismiss,
  assetId,
  databaseId,
  previewKey
}) => {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<boolean>(false);

  useEffect(() => {
    // Reset states when props change
    if (visible) {
      setPreviewUrl(null);
      setLoading(true);
      setError(false);
      
      if (!previewKey) {
        console.log("No preview key provided to PreviewModal");
        setLoading(false);
        setError(true);
        return;
      }

      console.log("Loading full preview with key:", previewKey);
      
      const loadPreviewImage = async () => {
        try {
          const response = await downloadAsset({
            databaseId,
            assetId,
            key: previewKey,
            versionId: "",
            downloadType: "assetPreview"
          });

          console.log("Download asset response for full preview:", response);

          if (response !== false && Array.isArray(response)) {
            if (response[0] === false) {
              console.error("Error downloading full preview:", response[1]);
              setError(true);
            } else {
              console.log("Full preview URL set:", response[1]);
              setPreviewUrl(response[1]);
            }
          } else {
            console.error("Invalid response format from downloadAsset:", response);
            setError(true);
          }
        } catch (err) {
          console.error("Error loading full preview:", err);
          setError(true);
        } finally {
          setLoading(false);
        }
      };

      loadPreviewImage();
    }
  }, [visible, assetId, databaseId, previewKey]);

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      size="large"
      header="Asset Preview"
      footer={
        <Box float="right">
          <Button onClick={onDismiss}>Close</Button>
        </Box>
      }
    >
      <Box padding="l" textAlign="center">
        {loading && (
          <div style={{ padding: "40px" }}>
            <Spinner size="large" />
            <div style={{ marginTop: "20px" }}>Loading preview...</div>
          </div>
        )}
        
        {!loading && error && (
          <div style={{ padding: "40px" }}>
            <div>Preview not available</div>
          </div>
        )}
        
        {!loading && !error && previewUrl && (
          <img 
            src={previewUrl} 
            alt="Asset preview" 
            style={{ maxWidth: "100%", maxHeight: "80vh" }}
            onError={(e) => {
              console.error("Error loading preview image");
              setError(true);
            }}
          />
        )}
      </Box>
    </Modal>
  );
};

export default PreviewModal;
