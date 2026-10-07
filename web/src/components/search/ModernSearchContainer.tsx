/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Box from "@cloudscape-design/components/box";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import { appCache } from "../../services/appCache";
import { featuresEnabled } from "../../common/constants/featuresEnabled";
import {
    SearchContainerProps,
    MetadataFilter,
    NlpSearchResponse,
    SearchMode,
    SearchQuery,
    getTotalResultCount,
} from "./types";
import { useSearchState } from "./hooks/useSearchState";
import { useSearchAPI } from "./hooks/useSearchAPI";
import { usePreferences } from "./hooks/usePreferences";
import { useToasts } from "./hooks/useToasts";
import { useDebounce } from "./hooks/useDebounce";
import { SearchTopBar, SearchSidebar } from "./SearchLayout";
import type { SearchModeControl } from "./SearchLayout/SearchTopBar";
import CardView from "./SearchResults/CardView";
import ToastManager from "./SearchNotifications/ToastManager";
import SearchPageListView from "./SearchPageListView";
import SearchPageMapView from "./SearchPageMapView";
import { ResizableSplitter } from "../filemanager/components/ResizableSplitter";
import { initializePluginRegistry } from "../../visualizerPlugin";
import { resolveSearchModeCase, effectiveSearchMode } from "./utils/searchMode";
import { EMPTY_NLP_RESPONSE, pageNlpResult } from "./utils/nlpResultPaging";
import Synonyms from "../../synonyms";

/**
 * Columns shown when OpenSearch is off: the fields every natural-language hit carries without
 * OpenSearch enrichment. Per record type because asset-mode hits have no file fields.
 */
export const REDUCED_FILE_COLUMNS = [
    "relevance",
    "str_assetname",
    "str_databaseid",
    "str_key",
    "str_fileext",
    "num_filesize",
    "bool_archived",
];
export const REDUCED_ASSET_COLUMNS = [
    "relevance",
    "str_assetname",
    "str_databaseid",
    "str_assettype",
    "list_tags",
    "bool_archived",
];

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
    const config = appCache.getItem("config");
    const navigate = useNavigate();
    const { databaseId: urlDatabaseId } = useParams<{ databaseId?: string }>();

    // Determine which databaseId to use (URL param takes precedence)
    const databaseId = urlDatabaseId || propDatabaseId;
    const databaseLocked = !!urlDatabaseId;

    // Feature flags, read once per mount
    const [useNoOpenSearch] = useState(
        !!config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
    );
    const [useVectorSearch] = useState(
        !!config?.featuresEnabled?.includes(featuresEnabled.VECTORSEARCH)
    );
    const [useMapView] = useState(
        !!config?.featuresEnabled?.includes(featuresEnabled.LOCATIONSERVICES) &&
            !useNoOpenSearch &&
            allowedViews.includes("map")
    );
    const modeCase = resolveSearchModeCase(useNoOpenSearch, useVectorSearch);
    const searchAvailable = modeCase !== "none";

    // Hooks
    const searchState = useSearchState(initialFilters, databaseId);
    const searchAPI = useSearchAPI();
    const { preferences, updatePreferences, isLoaded: preferencesLoaded } = usePreferences();
    const { toasts, showSuccess, showError, showWarning, removeToast } = useToasts();

    // Which engine answers a search. Derived from the preference so it persists with the cookie and
    // is settled by the time the mount search runs (which waits for the preferences to load).
    const searchMode: SearchMode = effectiveSearchMode(modeCase, preferences.searchMode);

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
    // The full natural-language result (at most 100 hits); the table pages within it client-side.
    const [nlpResult, setNlpResult] = useState<NlpSearchResponse | null>(null);
    // Whether the last submit reached an engine. False before the first search and while natural-
    // language mode idles on an empty query, so the count badge and the "No matches" state are not
    // shown for a search that never ran.
    const [searchIssued, setSearchIssued] = useState(false);

    // Initialize search query if provided
    useEffect(() => {
        if (initialQuery) {
            searchState.setQuery(initialQuery);
        }
    }, [initialQuery]);

    // Eagerly initialize the visualizer plugin registry so isViewableExtension()
    // works on the file-search list before any viewer modal mounts. Without this,
    // the registry stays uninitialized on the search page and every row is treated
    // as non-viewable, so the multi-select viewer counter never increments.
    useEffect(() => {
        initializePluginRegistry().catch((err) =>
            console.log("Failed to initialize visualizer plugin registry for search:", err)
        );
    }, []);

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
            searchState.setPagination({
                from: 0, // Reset to first page when size changes
                size: preferences.pageSize,
            });
        }
    }, [preferencesLoaded, preferences.pageSize]); // React to preferences loading and changes

    /**
     * One search against the engine the current mode selects. Natural-language mode with nothing
     * typed issues no request: there is nothing to embed, so the table shows its empty state and
     * `null` is returned so callers do not announce a search that never ran.
     */
    const runSearch = async (searchQuery: SearchQuery) => {
        if (searchMode === "nlp") {
            if (!searchQuery.query.trim()) {
                setNlpResult(null);
                setSearchIssued(false);
                searchState.setResult(EMPTY_NLP_RESPONSE);
                return null;
            }
            setSearchIssued(true);
            const result = await searchAPI.executeNlpSearch(searchQuery, {
                databaseId,
                metadataSearchMode,
                metadataOperator,
                includeOpenSearchConstraints: !useNoOpenSearch,
            });
            setNlpResult(result);
            searchState.setResult(result);
            if (result.warnings && result.warnings.length > 0) {
                // Each entry is `{ code, message }`; the user reads the messages.
                showWarning("Search notice", result.warnings.map((w) => w.message).join(" "));
            }
            return result;
        }
        setNlpResult(null);
        setSearchIssued(true);
        const result = await searchAPI.executeSearch(
            searchQuery,
            databaseId,
            metadataSearchMode,
            metadataOperator
        );
        searchState.setResult(result);
        return result;
    };

    const handleSearch = async () => {
        try {
            searchState.setLoading(true);
            const result = await runSearch(searchState.buildSearchQuery());

            // `null` means no request was issued (empty natural-language query): nothing to announce.
            if (result && !autoRefreshing) {
                showSuccess("Search completed", `Found ${getTotalResultCount(result)} results`);
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

    // A search whose query differs from state (a sort or page change applied before the reducer
    // re-renders), reported like handleSearch but without the completion toast.
    const runOverride = async (searchQuery: SearchQuery) => {
        try {
            searchState.setLoading(true);
            await runSearch(searchQuery);
        } catch (error: any) {
            console.error("Search error:", error);
            searchState.setError(error.message || "Search failed");
            showError("Search failed", error.message || "An error occurred while searching");
        } finally {
            searchState.setLoading(false);
        }
    };

    // Auto-search on mount when a search engine is enabled - wait for preferences to load AND sync first
    useEffect(() => {
        if (
            !searchState.initialResult &&
            searchAvailable &&
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

    // Debounced auto-refresh: re-runs the query as it stood when a filter or mode change armed it.
    // If the text has changed since, the user is mid-edit and will submit; searching what they had
    // typed 500 ms in would embed a fragment and flash results for it.
    const debouncedAutoRefresh = useDebounce((armedQuery: string) => {
        if (hasInitialLoad && armedQuery === searchState.query) {
            setAutoRefreshing(true);
            handleSearch();
        }
    }, 500);

    // A search the user asks for (Enter or the Search button) supersedes an auto-refresh still
    // waiting on its debounce — otherwise a mode or filter change followed by a quick submit runs
    // the same query twice, which in natural-language mode embeds it twice.
    const handleExplicitSearch = async () => {
        debouncedAutoRefresh.cancel();
        await handleSearch();
    };

    // Auto-refresh when filters or the search mode change
    useEffect(() => {
        if (hasInitialLoad) {
            debouncedAutoRefresh(searchState.query);
        }
    }, [
        searchState.filters,
        searchState.metadataFilters,
        recordType,
        metadataSearchMode,
        metadataOperator,
        searchMode,
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

    const handlePagination = async (from: number, size?: number) => {
        try {
            searchState.setPagination({ from, size: size || preferences.pageSize });
            // Natural-language hits are paged client-side from the result already held.
            if (searchMode !== "nlp") {
                await handleSearch();
            }
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

    const handleMapThumbnailToggle = () => {
        const newValue = !preferences.showMapThumbnails;
        updatePreferences({ showMapThumbnails: newValue });
    };

    const handlePreferencesChange = async (newPreferences: any) => {
        updatePreferences(newPreferences);
        if (newPreferences.pageSize !== preferences.pageSize) {
            // Update pagination state with new page size and reset to page 1
            const newPagination = { from: 0, size: newPreferences.pageSize };
            searchState.setPagination(newPagination);
            if (searchMode === "nlp") return;

            // Trigger search with the new page size - pass pagination directly to avoid closure
            try {
                searchState.setLoading(true);
                const result = await runSearch(searchState.buildSearchQuery(newPagination));
                if (result) {
                    showSuccess("Search completed", `Found ${getTotalResultCount(result)} results`);
                }
            } catch (error: any) {
                console.error("Search error:", error);
                searchState.setError(error.message || "Search failed");
                showError("Search failed", error.message || "An error occurred while searching");
            } finally {
                searchState.setLoading(false);
            }
        }
    };

    const handleSearchModeChange = (nextMode: SearchMode) => {
        updatePreferences({ searchMode: nextMode });
        setNlpResult(null);
        searchState.setPagination({ from: 0 });
    };

    const handleClearSearch = () => {
        searchState.clearSearch();
        setNlpResult(null);
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

        // Map view only available for assets — switch to table and clean up map filters
        if (type === "file" && currentView === "map") {
            setCurrentView("table");
            // Remove location metadata filters that map view added
            const filteredMetadata = searchState.metadataFilters.filter((filter) => {
                const keyLower = filter.key.toLowerCase();
                return (
                    keyLower !== "location" && keyLower !== "latitude" && keyLower !== "longitude"
                );
            });
            searchState.setMetadataFilters(filteredMetadata);
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
        if (view === "map" && previousView !== "map") {
            // Add location metadata filters (disabled from editing)
            // Include both lowercase and capitalized versions to handle different capitalizations
            const locationFilters: MetadataFilter[] = [
                { key: "location", value: "*", operator: "=", type: "string" },
                { key: "Location", value: "*", operator: "=", type: "string" },
                { key: "latitude", value: "*", operator: "=", type: "string" },
                { key: "Latitude", value: "*", operator: "=", type: "string" },
                { key: "longitude", value: "*", operator: "=", type: "string" },
                { key: "Longitude", value: "*", operator: "=", type: "string" },
            ];

            // Add these filters to existing metadata filters
            searchState.setMetadataFilters([...searchState.metadataFilters, ...locationFilters]);

            // Set metadata search mode to "both" and operator to "OR"
            setMetadataSearchMode("both");
            setMetadataOperator("OR");
        }

        // When switching FROM map view, remove location metadata filters
        if (previousView === "map" && view !== "map") {
            // Remove the location filters we added (both lowercase and capitalized versions)
            const filteredMetadata = searchState.metadataFilters.filter((filter) => {
                // Remove filters that match our location filter pattern (case-insensitive)
                const keyLower = filter.key.toLowerCase();
                const isLocationFilter =
                    keyLower === "location" || keyLower === "latitude" || keyLower === "longitude";
                return !isLocationFilter;
            });
            searchState.setMetadataFilters(filteredMetadata);
        }
    };

    const handleSidebarWidthChange = (width: number) => {
        setSidebarWidth(width);
        updatePreferences({ sidebarWidth: width });
    };

    // What the table renders: the natural-language page slice, or the server page as returned.
    const displayedResult = useMemo(() => {
        if (searchMode === "nlp" && nlpResult) {
            return pageNlpResult(
                nlpResult,
                { from: searchState.pagination.from, size: preferences.pageSize },
                searchState.sort
            );
        }
        return searchState.result;
    }, [
        searchMode,
        nlpResult,
        searchState.result,
        searchState.pagination.from,
        preferences.pageSize,
        searchState.sort,
    ]);

    // Calculate pagination values
    const totalResults = getTotalResultCount(displayedResult);
    const currentPage = 1 + Math.floor(searchState.pagination.from / preferences.pageSize);
    const pageCount = Math.ceil(totalResults / preferences.pageSize);

    // The columns the list renders: the fallback set without OpenSearch (only the fields a
    // natural-language hit carries), the user's set otherwise, with relevance first in NLP mode.
    const visibleColumns = useMemo(() => {
        if (useNoOpenSearch) {
            return recordType === "asset" ? REDUCED_ASSET_COLUMNS : REDUCED_FILE_COLUMNS;
        }
        const preferred =
            recordType === "asset" ? preferences.assetTableColumns : preferences.fileTableColumns;
        if (searchMode === "nlp" && !preferred.includes("relevance")) {
            return ["relevance", ...preferred];
        }
        return preferred;
    }, [
        useNoOpenSearch,
        recordType,
        searchMode,
        preferences.assetTableColumns,
        preferences.fileTableColumns,
    ]);

    // Keyword / natural-language switch: offered only when both engines are on. A single-engine
    // deployment has nothing to choose, so the top bar shows the query box alone.
    const searchModeControl: SearchModeControl | undefined =
        modeCase === "both" ? { mode: searchMode, onChange: handleSearchModeChange } : undefined;

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
        if (allowedViews.includes("map") && useMapView) {
            viewOptions.push({ text: "Map", id: "map" });
        }

        // If only one option, don't show selector
        if (viewOptions.length <= 1) return null;

        return (
            <SegmentedControl
                label="Result view"
                selectedId={currentView}
                onChange={({ detail }) => handleViewChange(detail.selectedId as any)}
                options={viewOptions}
            />
        );
    };

    // Actions the list view raises. Natural-language sort and paging change only the slice the
    // table shows; keyword mode re-queries the server.
    const handleListDispatch = (action: any) => {
        switch (action.type) {
            case "set-selected-items":
                searchState.setSelectedItems(action.selectedItems);
                break;
            case "query-sort":
                if (action.sort && action.tableSort) {
                    searchState.setSort(action.sort);
                    searchState.setTableSort(action.tableSort);
                    if (searchMode !== "nlp") {
                        // Build the query with the new sort from the action, not the (lagging) state
                        void runOverride({ ...searchState.buildSearchQuery(), sort: action.sort });
                    }
                }
                break;
            case "query-paginate":
                if (action.pagination) {
                    searchState.setPagination(action.pagination);
                    if (searchMode !== "nlp") {
                        // Pass pagination directly to buildSearchQuery to avoid stale closure
                        void runOverride(searchState.buildSearchQuery(action.pagination));
                    }
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
    };

    // The visible column list below is chosen by recordType, so the view has to judge file mode by
    // the same value rather than by the _rectype filter, which trails it by a render (see isFileMode
    // there). The column list is copied because the view rewrites it for the thumbnail columns.
    const listViewState = {
        ...searchState,
        result: displayedResult,
        recordType,
        tablePreferences: {
            pageSize: preferences.pageSize,
            visibleContent: [...visibleColumns],
        },
        showPreviewThumbnails: preferences.showThumbnails,
        showMapThumbnails: preferences.showMapThumbnails,
        useMapView: useMapView,
        // Natural-language mode with no query: nothing was searched, so the table explains what to
        // type rather than reporting no matches.
        nlpIdle: searchMode === "nlp" && !searchIssued,
    };

    // Render main content
    const renderContent = () => {
        switch (currentView) {
            case "card":
                return (
                    <CardView
                        items={displayedResult?.hits?.hits || []}
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
                        totalItems={totalResults}
                    />
                );

            case "map":
                if (useMapView) {
                    return <SearchPageMapView state={searchState} dispatch={() => {}} />;
                }
                // Fall through to table view if map not available
                return (
                    <SearchPageListView
                        state={listViewState}
                        onShowToast={showSuccess}
                        dispatch={handleListDispatch}
                    />
                );

            case "table":
            default:
                return (
                    <SearchPageListView
                        state={listViewState}
                        onShowToast={showSuccess}
                        dispatch={handleListDispatch}
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
                onSearch={handleExplicitSearch}
                onClearAll={handleClearSearch}
                loading={searchState.loading}
                resultCount={searchIssued ? totalResults : undefined}
                hasActiveFilters={searchState.hasActiveFilters()}
                title={
                    embedded?.title ||
                    (databaseId
                        ? `${Synonyms.Assets} for ${databaseId}`
                        : `${Synonyms.Assets} and Files - Search`)
                }
                searchMode={searchMode}
                searchModeControl={searchModeControl}
            />

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
                        // The file-type facet reflects the whole natural-language result set, not one page
                        searchResult={
                            searchMode === "nlp" && nlpResult ? nlpResult : displayedResult
                        }
                        databaseLocked={databaseLocked}
                        showThumbnails={preferences.showThumbnails}
                        onThumbnailToggle={handleThumbnailToggle}
                        showMapThumbnails={preferences.showMapThumbnails}
                        onMapThumbnailToggle={handleMapThumbnailToggle}
                        useMapView={useMapView}
                        isMapView={currentView === "map"}
                        reduced={useNoOpenSearch}
                        searchMode={searchMode}
                    />
                }
                rightPanel={
                    <Box padding={{ horizontal: "l", bottom: "l" }}>
                        {/* View Selector */}
                        <Box padding={{ bottom: "m" }}>{renderViewSelector()}</Box>

                        {/* Results */}
                        {renderContent()}

                        {/* A natural-language call that filled its candidate window found at least
                            this many matches; there is no cursor to fetch the rest. */}
                        {searchMode === "nlp" && nlpResult?.nlp?.truncated && (
                            <Box
                                padding={{ top: "s" }}
                                color="text-body-secondary"
                                fontSize="body-s"
                            >
                                Showing the top {nlpResult.hits.hits.length} semantic matches; more
                                may exist.
                            </Box>
                        )}
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
