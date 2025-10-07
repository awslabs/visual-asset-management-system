import React, { useState, useEffect } from "react";
import {
    FormField,
    Box,
    SpaceBetween,
} from "@cloudscape-design/components";
import { AssetSearchTable, AssetSearchItem } from "../../searchSmall/AssetSearchTable";
import { fetchtagTypes } from "../../../services/APIService";

export interface Asset {
    assetId: string;
    assetName: string;
    databaseId: string;
    description: string;
}

export interface AssetSelectorProps {
    currentAssetId: string;
    currentDatabaseId: string;
    selectedAsset: Asset | null;
    onAssetSelect: (asset: Asset | null) => void;
    onAssetFilesLoad?: (assetId: string, files: any[]) => void;
}

export function AssetSelector({
    currentAssetId,
    currentDatabaseId,
    selectedAsset,
    onAssetSelect,
    onAssetFilesLoad,
}: AssetSelectorProps) {
    const [tagTypes, setTagTypes] = useState<any[]>([]);

    // Fetch tag types when component mounts
    useEffect(() => {
        const loadTagTypes = async () => {
            try {
                const result = await fetchtagTypes();
                if (result && Array.isArray(result)) {
                    setTagTypes(result);
                }
            } catch (error) {
                console.error("Error fetching tag types:", error);
            }
        };

        loadTagTypes();
    }, []);

    const handleAssetSelect = (asset: AssetSearchItem) => {
        // Convert AssetSearchItem to Asset
        const selectedAssetData: Asset = {
            assetId: asset.assetId,
            assetName: asset.assetName,
            databaseId: asset.databaseId,
            description: asset.description,
        };
        onAssetSelect(selectedAssetData);
    };

    return (
        <SpaceBetween direction="vertical" size="m">
            <AssetSearchTable
                selectionMode="single"
                currentAssetId={currentAssetId}
                currentDatabaseId={currentDatabaseId}
                onAssetSelect={handleAssetSelect}
                onAssetFilesLoad={onAssetFilesLoad}
                showDatabaseColumn={true}
                showTagsColumn={true}
                showSelectedAssets={false}
                tagTypes={tagTypes}
            />

            {selectedAsset && (
                <FormField label="Selected Asset">
                    <Box>
                        <strong>{selectedAsset.assetName}</strong>
                        <br />
                        <span style={{ color: "#687078", fontSize: "14px" }}>
                            {selectedAsset.description}
                        </span>
                    </Box>
                </FormField>
            )}
        </SpaceBetween>
    );
}
