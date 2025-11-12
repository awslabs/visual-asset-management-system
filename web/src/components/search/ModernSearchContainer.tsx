/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import { useParams } from "react-router-dom";
import { Cache } from "aws-amplify";
import { useNavigate } from "react-router-dom";
import { Grid, Alert, SegmentedControl, Box } from "@cloudscape-design/components";
import { featuresEnabled } from "../../common/constants/featuresEnabled";
import { SearchContainerProps, MetadataFilter } from "./types";
import { useSearchState } from "./hooks/useSearchState";
import { useSearchAPI } from "./hooks/useSearchAPI";
import { usePreferences } from "./hooks/usePreferences";
import { useToasts } from "./hooks/useToasts";
import { useDebounce } from "./hooks/useDebounce";
import { SearchTopBar, SearchSidebar } from "./SearchLayout";
import CardView from "./SearchResults/CardView";
import ToastManager from "./SearchNotifications/ToastManager";
import SearchPageListView from "./SearchPageListView";
import SearchPageMapView from "./SearchPageMapView";
import ListPage from "../../pages/ListPage";
import { AssetListDefinition } from "../list/list-definitions/AssetListDefinition.js";
import { fetchAllAssets, fetchDatabaseAssets } from "../../services/APIService";
import { ResizableSplitter } from "../filemanager/components/ResizableSplitter";
import Synonyms from "../../synonyms";

const ModernSearchContainer: React.FC<SearchContainerProps> = ({
    mode = "full",
    initialFilters,
    initialQuery = "",
    onSelectionChange,
    allowedViews = ["table", "card", "map"],
    showPreferences = true,
    showBulkActions = true,
    maxHeight,
    databaseId: propDatabaseId,
    embedded,
}) => {
    const config = Cache.getItem("config");
    const navigate = useNavigate();
    const { databaseId: urlDatabaseId } = useParams<{ databaseId?: string }>();

    // Determine which databaseId to use (URL param takes precedence)
    const databaseId = urlDatabaseId || propDatabaseId;
    const databaseLocked = !!urlDatabaseId;

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
    const {
        preferences,
        updatePreferences,
        savePreferences,
        hasUnsavedChanges,
        isLoaded: preferencesLoaded,
    } = usePreferences();
    const { toasts, showSuccess, showError, showWarning, removeToast } = useToasts();

    // Local state
    const [recordType, setRecordType] = useState<"asset" | "file">("asset");
    const [metadataSearchMode, setMetadataSearchMode] = useState<string>("both");
    const [metadataOperator, setMetadataOperator] = useState<string>("OR");
    const [currentView, setCurrentView] = useState<"table" | "card" | "map">(() => {
        const supportedViews = allowedViews.filter(
            (view) => view === "table" || view === "card" || (view === "map" && useMapView)
        );

        if (supportedViews.includes(preferences.viewMode as any)) {
            return preferences.viewMode as "table" | "card" | "map";
        }
        return supportedViews[0] as "table" | "card" | "map";
    });
    const [autoRefreshing, setAutoRefreshing] = useState(false);
    const [hasInitialLoad, setHasInitialLoad] = useState(false);
    const [sidebarWidth, setSidebarWidth] = useState(preferences.sidebarWidth || 400);

    // Initialize search query if provided
    useEffect(() => {
        if (initialQuery) {
            searchState.setQuery(initialQuery);
        }
    }, [initialQuery]);

    // Initialize database filter from URL parameter
    useEffect(() => {
        if (databaseId && databaseLocked) {
            searchState.updateFilter("str_databaseid", {
                label: databaseId,
                value: databaseId,
            });
        } else if (!databaseLocked && searchState.filters.str_databaseid) {
            // Remove database filter when not locked (navigating away from database-specific URL)
            searchState.removeFilter("str_databaseid");
        }
    }, [databaseId, databaseLocked]);

    // Update record type filter when recordType changes
    useEffect(() => {
        searchState.updateFilter("_rectype", {
            label: recordType === "asset" ? Synonyms.Assets : "Files",
            value: recordType,
        });
    }, [recordType]);

    // Sync pagination size with preferences when preferences are loaded - MUST happen before initial search
    useEffect(() => {
        if (preferencesLoaded && searchState.pagination.size !== preferences.pageSize) {
            console.log("[Pagination] Syncing pagination size with preferences:", {
                currentSize: searchState.pagination.size,
                preferenceSize: preferences.pageSize,
            });
            searchState.setPagination({
                from: 0, // Reset to first page when size changes
                size: preferences.pageSize,
            });
        }
    }, [preferencesLoaded, preferences.pageSize]); // React to preferences loading and changes

    // Auto-search on mount if not using NoOpenSearch - wait for preferences to load AND sync first
    useEffect(() => {
        if (
            !searchState.initialResult &&
            !useNoOpenSearch &&
            preferencesLoaded &&
            searchState.pagination.size === preferences.pageSize
        ) {
            handleSearch().then(() => setHasInitialLoad(true));
        } else if (preferencesLoaded && searchState.pagination.size === preferences.pageSize) {
            setHasInitialLoad(true);
        }
    }, [preferencesLoaded, searchState.pagination.size, preferences.pageSize]); // Wait for preferences to load AND pagination to sync

    // Update preferences when view changes
    useEffect(() => {
        if (currentView !== preferences.viewMode) {
            updatePreferences({ viewMode: currentView });
        }
    }, [currentView, preferences.viewMode]);

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
            console.log(
                "[Search] Executing search with sort:",
                searchQuery.sort,
                "tableSort:",
                searchState.tableSort
            );
            const result = await searchAPI.executeSearch(
                searchQuery,
                databaseId,
                metadataSearchMode,
                metadataOperator
            );
            searchState.setResult(result);

            if (!autoRefreshing) {
                showSuccess("Search completed", `Found ${result.hits?.total?.value || 0} results`);
            }
        } catch (error: any) {
            console.error("Search error:", error);
            searchState.setError(error.message || "Search failed");
            showError("Search failed", error.message || "An error occurred while searching");
        } finally {
            searchState.setLoading(false);
            setAutoRefreshing(false);
        }
    };

    // Debounced auto-refresh function
    const debouncedAutoRefresh = useDebounce(() => {
        if (hasInitialLoad) {
            setAutoRefreshing(true);
            handleSearch();
        }
    }, 500);

    // Auto-refresh when filters change
    useEffect(() => {
        if (hasInitialLoad) {
            debouncedAutoRefresh();
        }
    }, [
        searchState.filters,
        searchState.metadataFilters,
        recordType,
        metadataSearchMode,
        metadataOperator,
    ]);

    // Add/remove Archived column when includeArchived filter changes
    useEffect(() => {
        const hasArchivedFilter = !!searchState.filters.bool_archived;
        const currentColumns =
            recordType === "asset" ? preferences.assetTableColumns : preferences.fileTableColumns;
        const hasArchivedColumn = currentColumns?.includes("bool_archived");

        if (hasArchivedFilter && !hasArchivedColumn && currentColumns) {
            // Add Archived column at the end
            if (recordType === "asset") {
                updatePreferences({ assetTableColumns: [...currentColumns, "bool_archived"] });
            } else {
                updatePreferences({ fileTableColumns: [...currentColumns, "bool_archived"] });
            }
        } else if (!hasArchivedFilter && hasArchivedColumn && currentColumns) {
            // Remove Archived column
            if (recordType === "asset") {
                updatePreferences({
                    assetTableColumns: currentColumns.filter((col) => col !== "bool_archived"),
                });
            } else {
                updatePreferences({
                    fileTableColumns: currentColumns.filter((col) => col !== "bool_archived"),
                });
            }
        }
    }, [searchState.filters.bool_archived, recordType]);

    const handleSort = async (sortField: string, isDescending: boolean) => {
        try {
            const sortQuery = searchAPI.buildSortQuery(sortField, isDescending);
            searchState.setSort(sortQuery);
            searchState.setTableSort({
                sortingField: sortField,
                sortingDescending: isDescending,
            });
            await handleSearch();
        } catch (error: any) {
            showError("Sort failed", error.message);
        }
    };

    const handlePagination = async (from: number, size?: number) => {
        try {
            console.log("[Pagination] handlePagination called with:", {
                from,
                size,
                pageSize: preferences.pageSize,
            });
            searchState.setPagination({ from, size: size || preferences.pageSize });
            console.log(
                "[Pagination] After setPagination, searchState.pagination:",
                searchState.pagination
            );
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

    const handlePreferencesChange = async (newPreferences: any) => {
        updatePreferences(newPreferences);
        if (newPreferences.pageSize !== preferences.pageSize) {
            // Update pagination state with new page size and reset to page 1
            const newPagination = { from: 0, size: newPreferences.pageSize };
            searchState.setPagination(newPagination);

            // Trigger search with the new page size - pass pagination directly to avoid closure
            try {
                searchState.setLoading(true);
                const searchQuery = searchState.buildSearchQuery(newPagination);
                const result = await searchAPI.executeSearch(
                    searchQuery,
                    databaseId,
                    metadataSearchMode,
                    metadataOperator
                );
                searchState.setResult(result);
                showSuccess("Search completed", `Found ${result.hits?.total?.value || 0} results`);
            } catch (error: any) {
                console.error("Search error:", error);
                searchState.setError(error.message || "Search failed");
                showError("Search failed", error.message || "An error occurred while searching");
            } finally {
                searchState.setLoading(false);
            }
        }
    };

    const handleClearSearch = () => {
        searchState.clearSearch();
        // Don't reset recordType - preserve the current search mode (asset/file)
        setMetadataSearchMode("both");
        setMetadataOperator("OR");
        showSuccess("Search cleared", "All filters and search terms have been cleared");
    };

    const handleCreateAsset = () => {
        if (databaseId) {
            navigate(`/upload/${databaseId}`);
        } else {
            navigate("/upload");
        }
    };

    const handleRecordTypeChange = (type: "asset" | "file") => {
        setRecordType(type);

        // Map view only available for assets
        if (type === "file" && currentView === "map") {
            setCurrentView("table");
        }

        // Remove mode-specific filters that don't apply to the new mode
        const updatedFilters = { ...searchState.filters };

        if (type === "asset") {
            // Switching to asset mode - remove file-specific filters
            delete updatedFilters.str_fileext;
            delete updatedFilters.num_filesize_filter;
            delete updatedFilters.date_lastmodified_filter;
        } else {
            // Switching to file mode - remove asset-specific filters
            delete updatedFilters.str_assettype;
            delete updatedFilters.bool_has_asset_children;
            delete updatedFilters.bool_has_asset_parents;
            delete updatedFilters.bool_has_assets_related;
        }

        searchState.setFilters(updatedFilters);

        // No need to update columns here - they're already stored separately per record type
        // The PreferencesPanel will automatically use the correct column list
    };

    // Handle view changes - add/remove location metadata filters for map view
    const handleViewChange = (view: "table" | "card" | "map") => {
        const previousView = currentView;
        setCurrentView(view);

        // When switching TO map view, add location metadata filters
        if (view === "map" && previousView !== "map" && recordType === "asset") {
            // Add location metadata filters (disabled from editing)
            const locationFilters: MetadataFilter[] = [
                { key: "location", value: "*", operator: "=", type: "string", fieldType: "gp" },
                { key: "location", value: "*", operator: "=", type: "string", fieldType: "gs" },
                { key: "latitude", value: "*", operator: "=", type: "string", fieldType: "str" },
                { key: "longitude", value: "*", operator: "=", type: "string", fieldType: "str" },
            ];

            // Add these filters to existing metadata filters
            searchState.setMetadataFilters([...searchState.metadataFilters, ...locationFilters]);

            // Set metadata search mode to "both" and operator to "OR"
            setMetadataSearchMode("both");
            setMetadataOperator("OR");
        }

        // When switching FROM map view, remove location metadata filters
        if (previousView === "map" && view !== "map") {
            // Remove the location filters we added
            const filteredMetadata = searchState.metadataFilters.filter((filter) => {
                // Remove filters that match our location filter pattern
                const isLocationFilter =
                    (filter.key === "location" &&
                        (filter.fieldType === "gp" || filter.fieldType === "gs")) ||
                    (filter.key === "latitude" && filter.fieldType === "str") ||
                    (filter.key === "longitude" && filter.fieldType === "str");
                return !isLocationFilter;
            });
            searchState.setMetadataFilters(filteredMetadata);
        }
    };

    const handleSidebarWidthChange = (width: number) => {
        setSidebarWidth(width);
        updatePreferences({ sidebarWidth: width });
    };

    // Calculate pagination values
    const currentPage = 1 + Math.floor(searchState.pagination.from / preferences.pageSize);
    const pageCount = Math.ceil(
        (searchState.result?.hits?.total?.value || 0) / preferences.pageSize
    );

    console.log("[Pagination] Current page calculation:", {
        from: searchState.pagination.from,
        pageSize: preferences.pageSize,
        currentPage,
        pageCount,
        totalResults: searchState.result?.hits?.total?.value,
    });

    // Render fallback for NoOpenSearch mode
    if (useNoOpenSearch) {
        return (
            <Box>
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
                    hideDeleteButton={true}
                />
            </Box>
        );
    }

    // Render view selector
    const renderViewSelector = () => {
        if (allowedViews.length <= 1) return null;

        const viewOptions: Array<{ text: string; id: string }> = [];
        if (allowedViews.includes("table")) {
            viewOptions.push({ text: "Table", id: "table" });
        }
        // Hide Grid view for now - not fully fleshed out
        // if (allowedViews.includes('card')) {
        //     viewOptions.push({ text: 'Grid', id: 'card' });
        // }
        if (allowedViews.includes("map") && useMapView && recordType === "asset") {
            viewOptions.push({ text: "Map", id: "map" });
        }

        // If only one option, don't show selector
        if (viewOptions.length <= 1) return null;

        return (
            <SegmentedControl
                selectedId={currentView}
                onChange={({ detail }) => handleViewChange(detail.selectedId as any)}
                options={viewOptions}
            />
        );
    };

    // Render main content
    const renderContent = () => {
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
                        recordType={recordType}
                        onOpenPreview={() => {}}
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
                if (useMapView && recordType === "asset") {
                    return <SearchPageMapView state={searchState} dispatch={() => {}} />;
                }
                // Fall through to table view if map not available
                return (
                    <SearchPageListView
                        state={{
                            ...searchState,
                            tablePreferences: {
                                pageSize: preferences.pageSize,
                                visibleContent:
                                    recordType === "asset"
                                        ? preferences.assetTableColumns
                                        : preferences.fileTableColumns,
                            },
                            showPreviewThumbnails: preferences.showThumbnails,
                        }}
                        dispatch={(action: any) => {
                            // Handle dispatch actions from SearchPageListView
                            switch (action.type) {
                                case "set-selected-items":
                                    searchState.setSelectedItems(action.selectedItems);
                                    break;
                                case "query-sort":
                                    if (action.sort && action.tableSort) {
                                        searchState.setSort(action.sort);
                                        searchState.setTableSort(action.tableSort);
                                        // Execute search with the new sort immediately
                                        (async () => {
                                            try {
                                                searchState.setLoading(true);
                                                // Build query with the new sort from action, not state
                                                const searchQuery = {
                                                    ...searchState.buildSearchQuery(),
                                                    sort: action.sort,
                                                };
                                                console.log(
                                                    "[Sort] Executing search with sort from action:",
                                                    action.sort
                                                );
                                                const result = await searchAPI.executeSearch(
                                                    searchQuery,
                                                    databaseId,
                                                    metadataSearchMode,
                                                    metadataOperator
                                                );
                                                searchState.setResult(result);
                                            } catch (error: any) {
                                                console.error("Sort search error:", error);
                                                searchState.setError(
                                                    error.message || "Search failed"
                                                );
                                                showError(
                                                    "Search failed",
                                                    error.message ||
                                                        "An error occurred while searching"
                                                );
                                            } finally {
                                                searchState.setLoading(false);
                                            }
                                        })();
                                    }
                                    break;
                                case "query-paginate":
                                    if (action.pagination) {
                                        console.log(
                                            "[Pagination] query-paginate action received:",
                                            action.pagination
                                        );
                                        // Update pagination state
                                        searchState.setPagination(action.pagination);

                                        // Execute search immediately with the new pagination values
                                        // Pass pagination directly to buildSearchQuery to avoid stale closure
                                        (async () => {
                                            try {
                                                searchState.setLoading(true);
                                                // Pass pagination directly to buildSearchQuery to avoid stale closure
                                                const searchQuery = searchState.buildSearchQuery(
                                                    action.pagination
                                                );
                                                console.log(
                                                    "[Pagination] Executing search with pagination:",
                                                    searchQuery.pagination
                                                );
                                                const result = await searchAPI.executeSearch(
                                                    searchQuery,
                                                    databaseId,
                                                    metadataSearchMode,
                                                    metadataOperator
                                                );
                                                searchState.setResult(result);
                                            } catch (error: any) {
                                                console.error("Pagination search error:", error);
                                                searchState.setError(
                                                    error.message || "Search failed"
                                                );
                                                showError(
                                                    "Search failed",
                                                    error.message ||
                                                        "An error occurred while searching"
                                                );
                                            } finally {
                                                searchState.setLoading(false);
                                            }
                                        })();
                                    }
                                    break;
                                case "set-search-table-preferences":
                                    if (action.payload) {
                                        handlePreferencesChange(action.payload);
                                    }
                                    break;
                                case "query-criteria-cleared":
                                    handleClearSearch();
                                    break;
                                default:
                                    console.log("Unhandled dispatch action:", action.type);
                            }
                        }}
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
                                    recordType === "asset"
                                        ? preferences.assetTableColumns
                                        : preferences.fileTableColumns,
                            },
                            showPreviewThumbnails: preferences.showThumbnails,
                        }}
                        dispatch={(action: any) => {
                            // Handle dispatch actions from SearchPageListView
                            switch (action.type) {
                                case "set-selected-items":
                                    searchState.setSelectedItems(action.selectedItems);
                                    break;
                                case "query-sort":
                                    if (action.sort && action.tableSort) {
                                        searchState.setSort(action.sort);
                                        searchState.setTableSort(action.tableSort);
                                        // Execute search with the new sort immediately
                                        (async () => {
                                            try {
                                                searchState.setLoading(true);
                                                // Build query with the new sort from action, not state
                                                const searchQuery = {
                                                    ...searchState.buildSearchQuery(),
                                                    sort: action.sort,
                                                };
                                                console.log(
                                                    "[Sort] Executing search with sort from action:",
                                                    action.sort
                                                );
                                                const result = await searchAPI.executeSearch(
                                                    searchQuery,
                                                    databaseId,
                                                    metadataSearchMode,
                                                    metadataOperator
                                                );
                                                searchState.setResult(result);
                                            } catch (error: any) {
                                                console.error("Sort search error:", error);
                                                searchState.setError(
                                                    error.message || "Search failed"
                                                );
                                                showError(
                                                    "Search failed",
                                                    error.message ||
                                                        "An error occurred while searching"
                                                );
                                            } finally {
                                                searchState.setLoading(false);
                                            }
                                        })();
                                    }
                                    break;
                                case "query-paginate":
                                    if (action.pagination) {
                                        console.log(
                                            "[Pagination] query-paginate action received:",
                                            action.pagination
                                        );
                                        // Update pagination state
                                        searchState.setPagination(action.pagination);

                                        // Execute search immediately with the new pagination values
                                        // Pass pagination directly to buildSearchQuery to avoid stale closure
                                        (async () => {
                                            try {
                                                searchState.setLoading(true);
                                                // Pass pagination directly to buildSearchQuery to avoid stale closure
                                                const searchQuery = searchState.buildSearchQuery(
                                                    action.pagination
                                                );
                                                console.log(
                                                    "[Pagination] Executing search with pagination:",
                                                    searchQuery.pagination
                                                );
                                                const result = await searchAPI.executeSearch(
                                                    searchQuery,
                                                    databaseId,
                                                    metadataSearchMode,
                                                    metadataOperator
                                                );
                                                searchState.setResult(result);
                                            } catch (error: any) {
                                                console.error("Pagination search error:", error);
                                                searchState.setError(
                                                    error.message || "Search failed"
                                                );
                                                showError(
                                                    "Search failed",
                                                    error.message ||
                                                        "An error occurred while searching"
                                                );
                                            } finally {
                                                searchState.setLoading(false);
                                            }
                                        })();
                                    }
                                    break;
                                case "set-search-table-preferences":
                                    if (action.payload) {
                                        handlePreferencesChange(action.payload);
                                    }
                                    break;
                                case "query-criteria-cleared":
                                    handleClearSearch();
                                    break;
                                default:
                                    console.log("Unhandled dispatch action:", action.type);
                            }
                        }}
                    />
                );
        }
    };

    const containerStyle = maxHeight ? { maxHeight, overflow: "auto" } : {};

    return (
        <div style={containerStyle}>
            {/* Toast notifications at the very top */}
            <ToastManager toasts={toasts} onDismiss={removeToast} />

            {/* Top Bar */}
            <SearchTopBar
                query={searchState.query}
                onQueryChange={searchState.setQuery}
                onSearch={handleSearch}
                onClearAll={handleClearSearch}
                loading={searchState.loading}
                resultCount={searchState.result?.hits?.total?.value}
                hasActiveFilters={searchState.hasActiveFilters()}
                title={
                    embedded?.title ||
                    (databaseId
                        ? `${Synonyms.Assets} for ${databaseId}`
                        : "Assets and Files - Search")
                }
                description={mode === "full" ? "Search and filter assets and files" : undefined}
            />

            {/* Error Display - removed, using toast notifications instead */}

            {/* Main Layout: Sidebar + Content with Resizable Splitter */}
            <ResizableSplitter
                leftPanel={
                    <SearchSidebar
                        recordType={recordType}
                        onRecordTypeChange={handleRecordTypeChange}
                        filters={searchState.filters}
                        onFilterChange={handleFilterChange}
                        metadataFilters={searchState.metadataFilters}
                        onAddMetadataFilter={() =>
                            searchState.addMetadataFilter({
                                key: "",
                                operator: "=",
                                value: "",
                                type: "string",
                                fieldType: "str",
                            })
                        }
                        metadataSearchMode={metadataSearchMode}
                        onMetadataSearchModeChange={setMetadataSearchMode}
                        metadataOperator={metadataOperator}
                        onMetadataOperatorChange={setMetadataOperator}
                        onRemoveMetadataFilter={searchState.removeMetadataFilter}
                        onUpdateMetadataFilter={(index, filter) => {
                            const updatedFilters = [...searchState.metadataFilters];
                            updatedFilters[index] = filter;
                            searchState.setMetadataFilters(updatedFilters);
                        }}
                        preferences={preferences}
                        onPreferencesChange={handlePreferencesChange}
                        loading={searchState.loading}
                        searchResult={searchState.result}
                        databaseLocked={databaseLocked}
                        showThumbnails={preferences.showThumbnails}
                        onThumbnailToggle={handleThumbnailToggle}
                        isMapView={currentView === "map"}
                    />
                }
                rightPanel={
                    <Box padding={{ horizontal: "l", bottom: "l" }}>
                        {/* View Selector */}
                        <Box padding={{ bottom: "m" }}>{renderViewSelector()}</Box>

                        {/* Results */}
                        {renderContent()}
                    </Box>
                }
                initialLeftWidth={sidebarWidth}
                minLeftWidth={300}
                maxLeftWidth={600}
                onWidthChange={handleSidebarWidthChange}
            />
        </div>
    );
};

export default ModernSearchContainer;
