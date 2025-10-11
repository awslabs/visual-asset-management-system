/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Box,
    Button,
    Input,
    Link,
    SpaceBetween,
    Toggle,
    Badge,
    Pagination,
    Spinner,
} from "@cloudscape-design/components";
import { API, Cache } from "aws-amplify";
import { fetchAllAssets } from "../../services/APIService";
import CustomTable from "../table/CustomTable";
import { featuresEnabled } from "../../common/constants/featuresEnabled";

export interface AssetSearchItem {
    assetId: string;
    assetName: string;
    databaseId: string;
    databaseName?: string;
    description: string;
    tags?: any[];
}

export interface AssetSearchTableProps {
    // Selection mode
    selectionMode: "single" | "multi";

    // Filtering
    currentAssetId?: string;
    currentDatabaseId?: string;

    // Callbacks
    onAssetSelect?: (asset: AssetSearchItem) => void; // For single mode
    onAssetsSelect?: (assets: AssetSearchItem[]) => void; // For multi mode

    // Optional features
    showDatabaseColumn?: boolean;
    showTagsColumn?: boolean;
    showSelectedAssets?: boolean; // For multi mode

    // Tag formatting (for CreateAssetLinkModal)
    tagTypes?: any[];

    // Optional file loading (for AssetSelector)
    onAssetFilesLoad?: (assetId: string, files: any[]) => void;

    // Optional no-OpenSearch mode
    noOpenSearch?: boolean;
}

export function AssetSearchTable({
    selectionMode,
    currentAssetId,
    currentDatabaseId,
    onAssetSelect,
    onAssetsSelect,
    showDatabaseColumn = false,
    showTagsColumn = false,
    showSelectedAssets = false,
    tagTypes = [],
    onAssetFilesLoad,
    noOpenSearch = false,
}: AssetSearchTableProps) {
    const [searchTerm, setSearchTerm] = useState("");
    const [searchResults, setSearchResults] = useState<AssetSearchItem[]>([]);
    const [showResults, setShowResults] = useState(false);
    const [isSearching, setIsSearching] = useState(false);
    const [searchError, setSearchError] = useState("");

    // Pagination state
    const [includeMetadata, setIncludeMetadata] = useState(true);
    const [currentPage, setCurrentPage] = useState(1); // 1-indexed for UI
    const [totalResults, setTotalResults] = useState(0);
    const pageSize = 10;

    // Multi-select state
    const [selectedItems, setSelectedItems] = useState<AssetSearchItem[]>([]);
    const [selectedAssets, setSelectedAssets] = useState<AssetSearchItem[]>([]);

    // Check if OpenSearch is disabled
    const config = Cache.getItem("config");
    const useNoOpenSearch =
        noOpenSearch || config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH);

    // Calculate total pages
    const totalPages = Math.ceil(totalResults / pageSize);

    // Format tags with tag types
    const formatAssetTags = (tags: any[]) => {
        if (!Array.isArray(tags) || tags.length === 0) {
            return "";
        }

        try {
            const tagsWithType = tags.map((tag) => {
                if (tagTypes && tagTypes.length > 0) {
                    for (const tagType of tagTypes) {
                        let tagTypeName = tagType.tagTypeName;

                        if (tagType && tagType.required === "True") {
                            tagTypeName += " [R]";
                        }

                        if (
                            tagType.tags &&
                            Array.isArray(tagType.tags) &&
                            tagType.tags.includes(tag)
                        ) {
                            return `${tag} [${tagTypeName}]`;
                        }
                    }
                }
                return tag;
            });

            return tagsWithType.join(", ");
        } catch (error) {
            console.error("Error formatting tags:", error);
            return tags.join(", ");
        }
    };

    // Handle search
    const handleSearch = async (page: number = 1) => {
        if (!searchTerm.trim()) {
            setSearchResults([]);
            setShowResults(false);
            setTotalResults(0);
            setCurrentPage(1);
            return;
        }

        setIsSearching(true);
        setSearchError("");

        try {
            let results: AssetSearchItem[] = [];
            let total = 0;

            if (!useNoOpenSearch) {
                // Build filters array
                const filters: object[] = [];

                // Add database filter if specified (for both single and multi select modes)
                if (currentDatabaseId) {
                    filters.push({
                        query_string: {
                            query: `(str_databaseid:("${currentDatabaseId}"))`,
                        },
                    });
                }

                // Use new OpenSearch API format
                const body = {
                    query: searchTerm,
                    from: (page - 1) * pageSize,
                    size: pageSize,
                    entityTypes: ["asset"],
                    includeMetadataInSearch: includeMetadata,
                    filters: filters.length > 0 ? filters : undefined,
                    aggregations: false,
                    includeHighlights: false,
                    explainResults: false,
                    includeArchived: false,
                };

                const response = await API.post("api", "search", {
                    "Content-type": "application/json",
                    body: body,
                });

                if (response?.hits?.hits) {
                    results = response.hits.hits.map((result: any) => {
                        // Extract tags
                        let tags: any[] = [];
                        if (result._source?.list_tags) {
                            if (typeof result._source.list_tags === "string") {
                                tags = result._source.list_tags
                                    .split(",")
                                    .map((tag: string) => tag.trim());
                            } else if (Array.isArray(result._source.list_tags)) {
                                tags = result._source.list_tags;
                            }
                        } else if (result._source?.tags) {
                            tags = Array.isArray(result._source.tags)
                                ? result._source.tags
                                : [result._source.tags];
                        }

                        return {
                            assetId: result._source.str_assetid || "",
                            assetName: result._source.str_assetname || "",
                            databaseId: result._source.str_databaseid || "",
                            databaseName: result._source.str_databaseid || "",
                            description: result._source.str_description || "",
                            tags: tags,
                        };
                    });

                    total = response.hits.total?.value || 0;
                }
            } else {
                // Use assets API with client-side pagination
                const allAssets = await fetchAllAssets();
                if (Array.isArray(allAssets)) {
                    const filtered = allAssets
                        .filter((asset: any) => asset.databaseId.indexOf("#deleted") === -1)
                        .filter((asset: any) =>
                            asset.assetName.toLowerCase().includes(searchTerm.toLowerCase())
                        );

                    total = filtered.length;
                    const startIndex = (page - 1) * pageSize;

                    results = filtered
                        .slice(startIndex, startIndex + pageSize)
                        .map((asset: any) => ({
                            assetId: asset.assetId,
                            assetName: asset.assetName,
                            databaseId: asset.databaseId,
                            databaseName: asset.databaseId,
                            description: asset.description,
                            tags: asset.tags || [],
                        }));
                }
            }

            // Filter out current asset if specified (client-side only for this case)
            let filteredResults = results;
            if (currentAssetId) {
                filteredResults = results.filter((asset) => asset.assetId !== currentAssetId);
            }

            setSearchResults(filteredResults);
            setTotalResults(total);
            setCurrentPage(page);
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

    // Handle page change
    const handlePageChange = (page: number) => {
        handleSearch(page);
    };

    // Handle single selection
    const handleSingleSelect = async (asset: AssetSearchItem) => {
        if (onAssetSelect) {
            onAssetSelect(asset);
        }

        // Load asset files if callback provided
        if (onAssetFilesLoad) {
            try {
                const { fetchAssetS3Files } = await import("../../services/AssetVersionService");
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

    // Handle multi-select add
    const handleAddSelected = () => {
        if (selectedItems.length === 0) {
            return;
        }

        // Add selected items to the selected assets list, avoiding duplicates
        const newSelectedAssets = [...selectedAssets];
        selectedItems.forEach((item) => {
            const isDuplicate = newSelectedAssets.some((asset) => asset.assetId === item.assetId);
            if (!isDuplicate) {
                newSelectedAssets.push(item);
            }
        });

        setSelectedAssets(newSelectedAssets);
        setSelectedItems([]);

        // Notify parent
        if (onAssetsSelect) {
            onAssetsSelect(newSelectedAssets);
        }
    };

    // Handle remove from selected assets
    const handleRemoveSelected = (assetId: string) => {
        const newSelectedAssets = selectedAssets.filter((asset) => asset.assetId !== assetId);
        setSelectedAssets(newSelectedAssets);

        // Notify parent
        if (onAssetsSelect) {
            onAssetsSelect(newSelectedAssets);
        }
    };

    // Build column definitions
    const buildColumns = () => {
        const columns: any[] = [
            {
                id: "assetName",
                header: "Asset Name",
                cell: (item: AssetSearchItem) => (
                    <Link
                        href={`#/databases/${item.databaseName || item.databaseId}/assets/${
                            item.assetId
                        }`}
                        external
                        target="_blank"
                    >
                        {item.assetName}
                    </Link>
                ),
                sortingField: "assetName",
                isRowHeader: true,
            },
        ];

        if (showDatabaseColumn) {
            columns.push({
                id: "databaseId",
                header: "Database Name",
                cell: (item: AssetSearchItem) => item.databaseName || item.databaseId,
                sortingField: "databaseName",
            });
        }

        columns.push({
            id: "description",
            header: "Description",
            cell: (item: AssetSearchItem) => item.description || "-",
            sortingField: "description",
        });

        if (showTagsColumn) {
            columns.push({
                id: "tags",
                header: "Tags",
                cell: (item: AssetSearchItem) => formatAssetTags(item.tags || []),
                sortingField: "tags",
            });
        }

        return columns;
    };

    const columns = buildColumns();

    // Build columns for selected assets table (multi mode)
    const selectedAssetsColumns = [
        ...columns,
        {
            id: "actions",
            header: "Actions",
            cell: (item: AssetSearchItem) => (
                <Button variant="link" onClick={() => handleRemoveSelected(item.assetId)}>
                    Remove
                </Button>
            ),
        },
    ];

    return (
        <SpaceBetween direction="vertical" size="l">
            {/* Selected Assets Table (Multi-select mode only) */}
            {selectionMode === "multi" && showSelectedAssets && selectedAssets.length > 0 && (
                <div style={{ width: "100%", maxWidth: "none", minWidth: 0 }}>
                    <div style={{ marginBottom: "8px" }}>
                        <label
                            style={{
                                display: "block",
                                fontWeight: "600",
                                fontSize: "14px",
                                color: "var(--color-text-label)",
                            }}
                        >
                            Selected Assets ({selectedAssets.length})
                        </label>
                    </div>
                    <CustomTable
                        columns={selectedAssetsColumns}
                        items={selectedAssets}
                        selectedItems={[]}
                        setSelectedItems={() => {}}
                        trackBy="assetId"
                        enablePagination={true}
                        pageSize={5}
                    />
                </div>
            )}

            {/* Search Input */}
            <div style={{ width: "100%", maxWidth: "none" }}>
                <div style={{ marginBottom: "8px" }}>
                    <label
                        style={{
                            display: "block",
                            fontWeight: "600",
                            fontSize: "14px",
                            marginBottom: "4px",
                            color: "var(--color-text-label)",
                        }}
                    >
                        Search Assets
                    </label>
                    <div
                        style={{
                            fontSize: "12px",
                            color: "var(--color-text-body-secondary)",
                            marginBottom: "8px",
                        }}
                    >
                        Input asset search keywords. Press Enter to search.
                    </div>
                </div>
                <SpaceBetween direction="vertical" size="xs">
                    <Toggle
                        checked={includeMetadata}
                        onChange={({ detail }) => {
                            setIncludeMetadata(detail.checked);
                            // Reset search when toggling metadata search
                            if (showResults) {
                                setShowResults(false);
                                setSearchResults([]);
                                setCurrentPage(1);
                            }
                        }}
                    >
                        Search in metadata
                    </Toggle>
                    <Input
                        placeholder="Search for assets"
                        type="search"
                        value={searchTerm}
                        onChange={({ detail }) => {
                            setSearchTerm(detail.value);
                            if (!detail.value.trim()) {
                                setShowResults(false);
                                setSearchResults([]);
                                setCurrentPage(1);
                                setTotalResults(0);
                                setSearchError("");
                            }
                        }}
                        onKeyDown={({ detail }) => {
                            if (detail.key === "Enter") {
                                handleSearch(1);
                            }
                        }}
                    />
                </SpaceBetween>
                {searchError && (
                    <Box color="text-status-error" margin={{ top: "xs" }}>
                        {searchError}
                    </Box>
                )}
            </div>

            {/* Loading State */}
            {isSearching && (
                <Box textAlign="center" padding="m">
                    <Spinner size="normal" />
                    <div>Searching assets...</div>
                </Box>
            )}

            {/* Search Results */}
            {showResults && !isSearching && (
                <div style={{ width: "100%", maxWidth: "none", minWidth: 0 }}>
                    <div
                        style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            marginBottom: "8px",
                        }}
                    >
                        <SpaceBetween direction="horizontal" size="xs">
                            <label
                                style={{
                                    display: "block",
                                    fontWeight: "600",
                                    fontSize: "14px",
                                    color: "var(--color-text-label)",
                                }}
                            >
                                Search Results
                            </label>
                            {totalResults > 0 && (
                                <Badge color="blue">
                                    Showing {(currentPage - 1) * pageSize + 1}-
                                    {Math.min(currentPage * pageSize, totalResults)} of{" "}
                                    {totalResults} results
                                </Badge>
                            )}
                            {currentDatabaseId && <Badge>Database: {currentDatabaseId}</Badge>}
                        </SpaceBetween>
                        {selectionMode === "multi" && (
                            <Button
                                onClick={handleAddSelected}
                                disabled={selectedItems.length === 0}
                            >
                                Add Selected ({selectedItems.length})
                            </Button>
                        )}
                    </div>

                    <SpaceBetween direction="vertical" size="m">
                        {searchResults.length > 0 ? (
                            <>
                                <CustomTable
                                    columns={columns}
                                    items={searchResults}
                                    selectedItems={selectedItems}
                                    setSelectedItems={(items: AssetSearchItem[]) => {
                                        setSelectedItems(items);
                                        if (selectionMode === "single" && items.length > 0) {
                                            handleSingleSelect(items[0]);
                                        }
                                    }}
                                    trackBy="assetId"
                                    enablePagination={false}
                                    selectionType={selectionMode === "multi" ? "multi" : "single"}
                                />
                                {totalPages > 1 && (
                                    <Pagination
                                        currentPageIndex={currentPage}
                                        pagesCount={totalPages}
                                        onChange={({ detail }) =>
                                            handlePageChange(detail.currentPageIndex)
                                        }
                                    />
                                )}
                            </>
                        ) : (
                            <Box textAlign="center" padding="m" color="text-status-inactive">
                                No assets found matching your search
                            </Box>
                        )}
                    </SpaceBetween>
                </div>
            )}
        </SpaceBetween>
    );
}
