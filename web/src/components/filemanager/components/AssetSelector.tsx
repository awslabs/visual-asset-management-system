import React, { useState, useEffect } from "react";
import { FormField, Box, SpaceBetween, Alert } from "@cloudscape-design/components";
import { AssetSearchTable, AssetSearchItem } from "../../searchSmall/AssetSearchTable";
import { fetchtagTypes } from "../../../services/APIService";
import Synonyms from "../../../synonyms";

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
    restrictToCurrentDatabase?: boolean;
}

export function AssetSelector({
    currentAssetId,
    currentDatabaseId,
    selectedAsset,
    onAssetSelect,
    onAssetFilesLoad,
    restrictToCurrentDatabase,
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
                restrictToCurrentDatabase={restrictToCurrentDatabase}
            />

            {selectedAsset && (
                <SpaceBetween direction="vertical" size="xs">
                    <FormField label={`Selected ${Synonyms.Asset}`}>
                        <Box>
                            <strong>{selectedAsset.assetName}</strong>
                            <br />
                            <span style={{ color: "var(--vams-text-secondary)", fontSize: "14px" }}>
                                {selectedAsset.description}
                            </span>
                            <br />
                            <span style={{ color: "var(--vams-text-secondary)", fontSize: "14px" }}>
                                {`${Synonyms.Database}: `}
                                <strong>{selectedAsset.databaseId}</strong>
                            </span>
                        </Box>
                    </FormField>
                    {selectedAsset.databaseId !== currentDatabaseId && (
                        <Alert type="warning">
                            {`Cross-${Synonyms.database} operation: The selected ${Synonyms.asset} is in ${Synonyms.database} `}
                            <strong>{selectedAsset.databaseId}</strong>
                            {`, which differs from the current ${Synonyms.database} `}
                            <strong>{currentDatabaseId}</strong>
                            {`. Ensure you have the required permissions on both ${Synonyms.databases}.`}
                        </Alert>
                    )}
                </SpaceBetween>
            )}
        </SpaceBetween>
    );
}
