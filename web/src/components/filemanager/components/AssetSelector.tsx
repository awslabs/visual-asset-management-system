import React, { useState } from "react";
import {
    FormField,
    Input,
    Table,
    Link,
    Box,
    SpaceBetween,
    Spinner,
} from "@cloudscape-design/components";
import { fetchAllAssets } from "../../../services/APIService";
import { fetchAssetS3Files } from "../../../services/AssetVersionService";
import { API } from "aws-amplify";
import { Cache } from "aws-amplify";
import { featuresEnabled } from "../../../common/constants/featuresEnabled";

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
    const [searchTerm, setSearchTerm] = useState("");
    const [searchResults, setSearchResults] = useState<Asset[]>([]);
    const [showResults, setShowResults] = useState(false);
    const [isSearching, setIsSearching] = useState(false);
    const [searchError, setSearchError] = useState("");

    // Check if OpenSearch is disabled
    const config = Cache.getItem("config");
    const useNoOpenSearch = config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH);

    const handleSearch = async () => {
        if (!searchTerm.trim()) {
            setSearchResults([]);
            setShowResults(false);
            return;
        }

        setIsSearching(true);
        setSearchError("");

        try {
            let results: Asset[] = [];

            if (!useNoOpenSearch) {
                // Use OpenSearch API
                const body = {
                    tokens: [],
                    operation: "AND",
                    from: 0,
                    size: 100,
                    query: searchTerm,
                    filters: [
                        {
                            query_string: {
                                query: '(_rectype:("asset"))',
                            },
                        },
                    ],
                };

                const response = await API.post("api", "search", {
                    "Content-type": "application/json",
                    body: body,
                });

                if (response?.hits?.hits) {
                    results = response.hits.hits.map((result: any) => ({
                        assetId: result._source.str_assetid || "",
                        assetName: result._source.str_assetname || "",
                        databaseId: result._source.str_databaseid || "",
                        description: result._source.str_description || "",
                    }));
                }
            } else {
                // Use assets API
                const allAssets = await fetchAllAssets();
                if (Array.isArray(allAssets)) {
                    results = allAssets
                        .filter((asset: any) => asset.databaseId.indexOf("#deleted") === -1)
                        .filter((asset: any) =>
                            asset.assetName.toLowerCase().includes(searchTerm.toLowerCase())
                        )
                        .map((asset: any) => ({
                            assetId: asset.assetId,
                            assetName: asset.assetName,
                            databaseId: asset.databaseId,
                            description: asset.description,
                        }));
                }
            }

            // Filter out current asset and assets from different databases
            const filteredResults = results.filter(
                (asset) =>
                    asset.assetId !== currentAssetId && asset.databaseId === currentDatabaseId
            );

            setSearchResults(filteredResults);
            setShowResults(true);
        } catch (error) {
            console.error("Error searching assets:", error);
            setSearchError("Failed to search assets. Please try again.");
            setSearchResults([]);
            setShowResults(false);
        } finally {
            setIsSearching(false);
        }
    };

    const handleAssetSelection = async (asset: Asset) => {
        onAssetSelect(asset);

        // Load asset files if callback provided
        if (onAssetFilesLoad) {
            try {
                const [success, files] = await fetchAssetS3Files({
                    databaseId: asset.databaseId,
                    assetId: asset.assetId,
                    includeArchived: false,
                });
                if (success && files && Array.isArray(files)) {
                    onAssetFilesLoad(asset.assetId, files);
                }
            } catch (error) {
                console.error("Error loading asset files:", error);
            }
        }
    };

    const assetColumns = [
        {
            id: "assetName",
            header: "Asset Name",
            cell: (item: Asset) => (
                <Link
                    href="#"
                    onFollow={(e) => {
                        e.preventDefault();
                        handleAssetSelection(item);
                    }}
                >
                    {item.assetName}
                </Link>
            ),
            sortingField: "assetName",
            isRowHeader: true,
        },
        {
            id: "description",
            header: "Description",
            cell: (item: Asset) => item.description || "-",
            sortingField: "description",
        },
    ];

    return (
        <SpaceBetween direction="vertical" size="m">
            <FormField
                label="Search Assets"
                description="Enter asset name and press Enter to search"
                errorText={searchError}
            >
                <Input
                    placeholder="Search for assets..."
                    type="search"
                    value={searchTerm}
                    onChange={({ detail }) => {
                        setSearchTerm(detail.value);
                        if (!detail.value.trim()) {
                            setShowResults(false);
                            setSearchResults([]);
                        }
                    }}
                    onKeyDown={({ detail }) => {
                        if (detail.key === "Enter") {
                            handleSearch();
                        }
                    }}
                />
            </FormField>

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

            {isSearching && (
                <Box textAlign="center" padding="m">
                    <Spinner size="normal" />
                    <div>Searching assets...</div>
                </Box>
            )}

            {showResults && !isSearching && (
                <FormField label="Search Results">
                    {searchResults.length > 0 ? (
                        <Table
                            columnDefinitions={assetColumns}
                            items={searchResults}
                            loadingText="Loading assets"
                            sortingDisabled
                            empty={
                                <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                                    <SpaceBetween size="m">
                                        <b>No assets found</b>
                                        <p>Try adjusting your search terms</p>
                                    </SpaceBetween>
                                </Box>
                            }
                        />
                    ) : (
                        <Box textAlign="center" padding="m" color="text-status-inactive">
                            No assets found matching your search
                        </Box>
                    )}
                </FormField>
            )}
        </SpaceBetween>
    );
}
