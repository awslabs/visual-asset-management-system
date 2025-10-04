/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Cache } from "aws-amplify";
import { useNavigate } from "react-router-dom";
import {
    SpaceBetween,
    Container,
    Header,
    Tabs,
    Button,
    Box,
    Alert,
    SegmentedControl,
} from "@cloudscape-design/components";
import { featuresEnabled } from "../../common/constants/featuresEnabled";
import { SearchContainerProps } from "./types";
import { useSearchState } from "./hooks/useSearchState";
import { useSearchAPI } from "./hooks/useSearchAPI";
import { usePreferences } from "./hooks/usePreferences";
import { useToasts } from "./hooks/useToasts";
import BasicFilters from "./SearchFilters/BasicFilters";
import MetadataFilters from "./SearchFilters/MetadataFilters";
import CardView from "./SearchResults/CardView";
import ToastManager from "./SearchNotifications/ToastManager";
import SearchPageListView from "../../pages/search/SearchPageListView";
import SearchPageMapView from "../../pages/search/SearchPageMapView";
import ListPage from "../../pages/ListPage";
import { AssetListDefinition } from "../list/list-definitions/AssetListDefinition.js";
import { fetchAllAssets, fetchDatabaseAssets } from "../../services/APIService";
import Synonyms from "../../synonyms";

const SearchContainer: React.FC<SearchContainerProps> = ({
    mode = "full",
    initialFilters,
    initialQuery = "",
    onSelectionChange,
    allowedViews = ["table", "card", "map"],
    showPreferences = true,
    showBulkActions = true,
    maxHeight,
    databaseId,
    embedded,
}) => {
    const config = Cache.getItem("config");
    const navigate = useNavigate();

    // Feature flags
    const [useNoOpenSearch] = useState(
        config.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
    );
    const [useMapView] = useState(
        config.featuresEnabled?.includes(featuresEnabled.LOCATIONSERVICES) &&
            !useNoOpenSearch &&
            allowedViews.includes("map")
    );

    // Hooks
    const searchState = useSearchState(initialFilters, databaseId);
    const searchAPI = useSearchAPI();
    const { preferences, updatePreferences, savePreferences, hasUnsavedChanges } = usePreferences();
    const { toasts, showSuccess, showError, showWarning, removeToast } = useToasts();

    // Local state
    const [currentView, setCurrentView] = useState<"table" | "card" | "map">(() => {
        // Filter out map view if not supported by preferences
        const supportedViews = allowedViews.filter(
            (view) => view === "table" || view === "card" || (view === "map" && useMapView)
        );

        if (supportedViews.includes(preferences.viewMode as any)) {
            return preferences.viewMode as "table" | "card" | "map";
        }
        return supportedViews[0] as "table" | "card" | "map";
    });
    const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);
    const [showPreviewModal, setShowPreviewModal] = useState(false);
    const [previewAsset, setPreviewAsset] = useState<any>({});

    // Initialize search query if provided
    useEffect(() => {
        if (initialQuery) {
            searchState.setQuery(initialQuery);
        }
    }, [initialQuery, searchState]);

    // Auto-search on mount if not using NoOpenSearch
    useEffect(() => {
        if (!searchState.initialResult && !useNoOpenSearch) {
            handleSearch();
        }
    }, [searchState.initialResult, useNoOpenSearch]);

    // Update preferences when view changes
    useEffect(() => {
        if (currentView !== preferences.viewMode) {
            updatePreferences({ viewMode: currentView });
        }
    }, [currentView, preferences.viewMode, updatePreferences]);

    // Notify parent of selection changes
    useEffect(() => {
        if (onSelectionChange) {
            onSelectionChange(searchState.selectedItems);
        }
    }, [searchState.selectedItems, onSelectionChange]);

    const handleSearch = async () => {
        try {
            searchState.setLoading(true);
            const searchQuery = searchState.buildSearchQuery();
            const result = await searchAPI.executeSearch(searchQuery, databaseId);
            searchState.setResult(result);
            showSuccess("Search completed", `Found ${result.hits?.total?.value || 0} results`);
        } catch (error: any) {
            console.error("Search error:", error);
            searchState.setError(error.message || "Search failed");
            showError("Search failed", error.message || "An error occurred while searching");
        }
    };

    const handleSort = async (sortField: string, isDescending: boolean) => {
        try {
            const sortQuery = searchAPI.buildSortQuery(sortField, isDescending);
            searchState.setSort(sortQuery);
            await handleSearch();
        } catch (error: any) {
            showError("Sort failed", error.message);
        }
    };

    const handlePagination = async (from: number, size?: number) => {
        try {
            searchState.setPagination({ from, size: size || preferences.pageSize });
            await handleSearch();
        } catch (error: any) {
            showError("Pagination failed", error.message);
        }
    };

    const handleFilterChange = (key: string, value: any) => {
        searchState.updateFilter(key, value);
    };

    const handleThumbnailToggle = () => {
        const newValue = !preferences.showThumbnails;
        updatePreferences({ showThumbnails: newValue });
    };

    const handlePreferencesChange = (newPreferences: any) => {
        updatePreferences(newPreferences);
        if (newPreferences.pageSize !== preferences.pageSize) {
            handlePagination(0, newPreferences.pageSize);
        }
    };

    const handleOpenPreview = (url: string, name: string, previewKey: string, item?: any) => {
        setPreviewAsset({
            url,
            assetId: item?.str_assetid,
            databaseId: item?.str_databaseid,
            previewKey,
            assetName: name,
        });
        setShowPreviewModal(true);
    };

    const handleClearSearch = () => {
        searchState.clearSearch();
        showSuccess("Search cleared", "All filters and search terms have been cleared");
    };

    const handleCreateAsset = () => {
        if (databaseId) {
            navigate(`/upload/${databaseId}`);
        } else {
            navigate("/upload");
        }
    };

    // Calculate pagination values
    const currentPage = 1 + Math.ceil(searchState.pagination.from / preferences.pageSize);
    const pageCount = Math.ceil(
        (searchState.result?.hits?.total?.value || 0) / preferences.pageSize
    );

    // Render fallback for NoOpenSearch mode
    if (useNoOpenSearch) {
        return (
            <Container>
                <Alert type="info" header="Limited Search Mode">
                    OpenSearch is disabled. Using basic asset listing instead.
                </Alert>
                <ListPage
                    singularName={Synonyms.Asset}
                    singularNameTitleCase={Synonyms.Asset}
                    pluralName={Synonyms.assets}
                    pluralNameTitleCase={Synonyms.Assets}
                    onCreateCallback={handleCreateAsset}
                    listDefinition={AssetListDefinition}
                    fetchAllElements={fetchAllAssets}
                    fetchElements={fetchDatabaseAssets}
                />
            </Container>
        );
    }

    // Render view selector
    const renderViewSelector = () => {
        if (allowedViews.length <= 1) return null;

        const viewOptions = [];
        if (allowedViews.includes("table")) {
            viewOptions.push({ text: "Table", id: "table" });
        }
        if (allowedViews.includes("card")) {
            viewOptions.push({ text: "Cards", id: "card" });
        }
        if (allowedViews.includes("map") && useMapView) {
            viewOptions.push({ text: "Map", id: "map" });
        }

        return (
            <SegmentedControl
                selectedId={currentView}
                onChange={({ detail }) => setCurrentView(detail.selectedId as any)}
                options={viewOptions}
            />
        );
    };

    // Render main content
    const renderContent = () => {
        const items =
            searchState.result?.hits?.hits?.map((hit: any) => ({
                ...hit._source,
                _id: hit._id,
            })) || [];

        switch (currentView) {
            case "card":
                return (
                    <CardView
                        items={searchState.result?.hits?.hits || []}
                        selectedItems={searchState.selectedItems}
                        onSelectionChange={searchState.setSelectedItems}
                        loading={searchState.loading}
                        cardSize={preferences.cardSize}
                        showThumbnails={preferences.showThumbnails}
                        recordType={
                            (searchState.filters._rectype?.value as "asset" | "file") || "asset"
                        }
                        onOpenPreview={handleOpenPreview}
                        currentPageIndex={currentPage}
                        pagesCount={pageCount}
                        onPageChange={(pageIndex) =>
                            handlePagination((pageIndex - 1) * preferences.pageSize)
                        }
                        onPreferencesChange={showPreferences ? handlePreferencesChange : undefined}
                        preferences={preferences}
                        onCreateAsset={showBulkActions ? handleCreateAsset : undefined}
                        onDeleteSelected={showBulkActions ? () => {} : undefined}
                        totalItems={searchState.result?.hits?.total?.value}
                    />
                );

            case "map":
                if (useMapView) {
                    return (
                        <SearchPageMapView
                            state={searchState}
                            dispatch={() => {}} // Legacy compatibility
                        />
                    );
                }
                // If map view is not available, fall through to table view
                return (
                    <SearchPageListView
                        state={{
                            ...searchState,
                            tablePreferences: {
                                pageSize: preferences.pageSize,
                                visibleContent:
                                    searchState.filters._rectype?.value === "asset"
                                        ? preferences.assetTableColumns
                                        : preferences.fileTableColumns,
                            },
                            showPreviewThumbnails: preferences.showThumbnails,
                        }}
                        dispatch={() => {}} // Legacy compatibility
                    />
                );

            case "table":
            default:
                return (
                    <SearchPageListView
                        state={{
                            ...searchState,
                            tablePreferences: {
                                pageSize: preferences.pageSize,
                                visibleContent:
                                    searchState.filters._rectype?.value === "asset"
                                        ? preferences.assetTableColumns
                                        : preferences.fileTableColumns,
                            },
                            showPreviewThumbnails: preferences.showThumbnails,
                        }}
                        dispatch={() => {}} // Legacy compatibility
                    />
                );
        }
    };

    const containerStyle = maxHeight ? { maxHeight, overflow: "auto" } : {};

    return (
        <div style={containerStyle}>
            <ToastManager toasts={toasts} onDismiss={removeToast} />

            <SpaceBetween direction="vertical" size="l">
                {/* Header */}
                {(!embedded || embedded.showHeader !== false) && (
                    <Header
                        variant={mode === "full" ? "h1" : "h2"}
                        description={
                            mode === "full" ? "Search and filter assets and files" : undefined
                        }
                        actions={
                            showPreferences && (
                                <SpaceBetween direction="horizontal" size="s">
                                    {hasUnsavedChanges && (
                                        <Button onClick={() => savePreferences()}>
                                            Save Preferences
                                        </Button>
                                    )}
                                    {renderViewSelector()}
                                </SpaceBetween>
                            )
                        }
                    >
                        {embedded?.title ||
                            (databaseId ? `${Synonyms.Assets} for ${databaseId}` : Synonyms.Assets)}
                    </Header>
                )}

                {/* Error Display */}
                {searchState.error && (
                    <Alert
                        type="error"
                        header="Search Error"
                        dismissible
                        onDismiss={() => searchState.setError(null)}
                    >
                        {searchState.error}
                    </Alert>
                )}

                {/* Filters */}
                <Container>
                    <SpaceBetween direction="vertical" size="m">
                        <BasicFilters
                            query={searchState.query}
                            filters={searchState.filters}
                            showThumbnails={preferences.showThumbnails}
                            onQueryChange={searchState.setQuery}
                            onFilterChange={handleFilterChange}
                            onThumbnailToggle={handleThumbnailToggle}
                            onSearch={handleSearch}
                            loading={searchState.loading}
                            searchResult={searchState.result}
                        />

                        {/* Advanced Filters Toggle */}
                        <Box textAlign="center">
                            <Button
                                variant="link"
                                onClick={() => setShowAdvancedFilters(!showAdvancedFilters)}
                            >
                                {showAdvancedFilters ? "Hide" : "Show"} Advanced Filters
                            </Button>
                        </Box>

                        {/* Advanced Filters */}
                        {showAdvancedFilters && (
                            <MetadataFilters
                                metadataFilters={searchState.metadataFilters}
                                onAddFilter={searchState.addMetadataFilter}
                                onRemoveFilter={searchState.removeMetadataFilter}
                                onUpdateFilter={(index, filter) => {
                                    const updatedFilters = [...searchState.metadataFilters];
                                    updatedFilters[index] = filter;
                                    searchState.setMetadataFilters(updatedFilters);
                                }}
                                disabled={searchState.loading}
                            />
                        )}

                        {/* Clear Filters */}
                        {searchState.hasActiveFilters() && (
                            <Box textAlign="center">
                                <Button onClick={handleClearSearch}>Clear All Filters</Button>
                            </Box>
                        )}
                    </SpaceBetween>
                </Container>

                {/* Results */}
                {renderContent()}
            </SpaceBetween>
        </div>
    );
};

export default SearchContainer;
