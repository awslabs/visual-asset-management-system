/* eslint-disable jsx-a11y/alt-text */
import React, { useEffect, useState } from "react";
import { Button, SpaceBetween } from "@cloudscape-design/components";
import { downloadAsset } from "../../services/APIService";

class ImgViewerProps {
    assetId!: string;
    databaseId!: string;
    assetKey!: string;
    altAssetKey!: string;
    versionId?: string;
    onDeletePreview?: () => void;
    isPreviewFile?: boolean;
}

export default function ImgViewer({
    assetId,
    databaseId,
    assetKey,
    altAssetKey,
    versionId,
    onDeletePreview,
    isPreviewFile = false,
}: ImgViewerProps) {
    const init = "placeholder.jpg";
    const [url, setUrl] = useState(init);
    const [err, setErr] = useState(null);

    useEffect(() => {
        if (url !== init) {
            return;
        }
        
        const loadImage = async () => {
            console.log("ImgViewer loading file:", {
                assetId,
                databaseId,
                key: assetKey,
                versionId: isPreviewFile ? "" : (versionId || ""),
                downloadType: "assetFile",
                isPreviewFile
            });
            
            try {
                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey,
                    versionId: isPreviewFile ? "" : (versionId || ""), // Don't use versionId for preview files
                    downloadType: "assetFile",
                });
                
                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading file:", response);
                        throw new Error("Failed to download file");
                    } else {
                        console.log("Successfully loaded file URL:", response[1]);
                        setUrl(response[1]);
                        return; // Success - exit early
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in primary download:", error);
                
                // Only try fallback if we're not already using a preview file
                if (!isPreviewFile && altAssetKey && altAssetKey !== assetKey) {
                    console.log("Trying fallback with altAssetKey:", altAssetKey);
                    
                    try {
                        const fallbackResponse = await downloadAsset({
                            assetId: assetId,
                            databaseId: databaseId,
                            key: altAssetKey,
                            versionId: "", // Don't use versionId for fallback
                            downloadType: "assetFile",
                        });
                        
                        if (fallbackResponse !== false && Array.isArray(fallbackResponse)) {
                            if (fallbackResponse[0] === false) {
                                console.error("Error downloading fallback file:", fallbackResponse);
                            } else {
                                console.log("Successfully loaded fallback URL:", fallbackResponse[1]);
                                setUrl(fallbackResponse[1]);
                            }
                        }
                    } catch (fallbackError) {
                        console.error("Error in fallback download:", fallbackError);
                    }
                }
            }
        };

        loadImage();
    }, [assetId, assetKey, databaseId, url, versionId, isPreviewFile, altAssetKey]);

    const fallback = (error: any) => {
        console.log("Image load error:", error);
        if (err === null) {
            setErr(error);
        }
    };
    return (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
            <img
                src={url}
                style={{ maxWidth: "100%", maxHeight: "100%", height: "100%" }}
                onError={fallback}
            />
            {onDeletePreview && (
                <div style={{ marginTop: "10px" }}>
                    <Button
                        iconName="remove"
                        variant="link"
                        onClick={onDeletePreview}
                    >
                        Delete Preview File
                    </Button>
                </div>
            )}
        </div>
    );
}
