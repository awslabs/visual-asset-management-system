/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Box from "@cloudscape-design/components/box";
import LiveRegion from "@cloudscape-design/components/live-region";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import { appCache } from "../../services/appCache";
import Alert from "@cloudscape-design/components/alert";
import { featuresEnabled } from "../../common/constants/featuresEnabled";
import {
    SearchContainerProps,
    NlpSearchResponse,
    SearchMode,
    SearchResponse,
    SearchQuery,
    getTotalResultCount,
    getPageCount,
    getPagedResultCount,
    SEARCH_RESULT_WINDOW,
    formatTotalResultCount,
    isTotalLowerBound,
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
import { AssetActionDisabledReasons, SearchTitleSlots } from "./SearchPageTypes";
import SearchPageMapView from "./SearchPageMapView";
import { ResizableSplitter } from "../filemanager/components/ResizableSplitter";
import "./SearchLayout/searchSplitter.css";
import { initializePluginRegistry } from "../../visualizerPlugin";
import { resolveSearchModeCase, effectiveSearchMode } from "./utils/searchMode";
import { EMPTY_NLP_RESPONSE, pageNlpResult, withNlpResultTotal } from "./utils/nlpResultPaging";
import {
    nlpSearchedNoDatabase,
    rareNlpWarningText,
    rareNlpWarningTexts,
} from "./utils/nlpWarnings";
import { NLP_QUERY_MAX_LENGTH, nlpQueryLength } from "./hooks/searchRequestBuilders";
import Synonyms from "../../synonyms";

/**
 * Columns shown when OpenSearch is off: the fields every natural-language hit carries without
 * OpenSearch enrichment. Per record type because asset-mode hits have no file fields.
 */
// The note under an empty natural-language answer whose candidate window may hold more (nlp.truncated):
// the search found no match among the closest results it checked, and the answer is still a minimum.
export const NLP_EMPTY_TRUNCATED_NOTE =
    "No match among the closest results searched; more may exist.";

// The note under a natural-language answer of `count` (1 or more) matches whose candidate window may
// hold more: singular for one match.
export const nlpTopMatchesNote = (count: number): string =>
    `Showing the top ${count} semantic ${count === 1 ? "match" : "matches"}; more may exist.`;

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

/**
 * "Found 1 result" / "Found N results", with "+" when N is a minimum ("Found 1,200+ results"). A
 * minimum of one reads "Found 1+ results", as a natural-language answer with one hit does: the API
 * reports every natural-language total that is not zero as a minimum.
 */
const resultCountMessage = (result: SearchResponse) => {
    const exactOne = getTotalResultCount(result) === 1 && !isTotalLowerBound(result);
    return `Found ${formatTotalResultCount(result)} ${exactOne ? "result" : "results"}`;
};

/**
 * What a finished search announces to screen readers: its count, then, for a natural-language answer,
 * the text of the Search notice shown above the results ("Found 0 results. You don't have access to
 * any database this search covers. Ask your administrator for access."). The count alone when the
 * answer has no rare warning, and always for a keyword answer.
 */
const searchAnnouncement = (result: SearchResponse, naturalLanguage: boolean) => {
    const count = resultCountMessage(result);
    const notice = naturalLanguage
        ? rareNlpWarningText((result as Partial<NlpSearchResponse>).warnings)
        : "";
    return notice ? `${count}. ${notice}` : count;
};

const ModernSearchContainer: React.FC<
    SearchContainerProps & {
        assetActionDisabledReasons?: AssetActionDisabledReasons;
        /** The search page's title row; the top bar then renders no heading (see SearchTopBar). */
        titleSlots?: SearchTitleSlots;
    }
> = ({
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
    assetActionDisabledReasons,
    titleSlots,
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
    const { toasts, showSuccess, showError, removeToast } = useToasts();

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
    // The Map view shows only records with a location: a keyword search on it adds that one filter
    // to the request and leaves the user's own filters, metadata mode and operator as set.
    const mapScoped = useMapView && currentView === "map";
    const [autoRefreshing, setAutoRefreshing] = useState(false);
    const [hasInitialLoad, setHasInitialLoad] = useState(false);
    const [sidebarWidth, setSidebarWidth] = useState(preferences.sidebarWidth || 400);
    // The full natural-language result (at most 100 hits); the table pages within it client-side.
    const [nlpResult, setNlpResult] = useState<NlpSearchResponse | null>(null);
    // The engine whose answer the page holds: null before the first search and while natural-
    // language mode idles on an empty query. The count badge and the "No matches" state describe a
    // search only in the mode that ran it, so a keyword listing is never presented as a natural-
    // language answer.
    const [answeredMode, setAnsweredMode] = useState<SearchMode | null>(null);
    // The count a finished search announces to screen readers through the polite live region below:
    // "Found N results", followed by the Search notice's text when the answer has one. Cleared when
    // the next search starts, so the same count is announced again.
    const [countAnnouncement, setCountAnnouncement] = useState("");
    // The natural-language answer whose Search notice the user dismissed. The notice is hidden only
    // while that answer is on screen; the next answer (a new search, Clear all filters, a mode or view
    // switch) shows its own notice again.
    const [dismissedNoticeAnswer, setDismissedNoticeAnswer] = useState<NlpSearchResponse | null>(
        null
    );
    // The number of the newest search. Each search takes the next one, and only the newest applies its
    // answer, its error and the end of the loading state: an older search that answers later (a page
    // change, a refresh or an auto-refresh still running) never replaces a newer one. Clear all
    // filters takes a number too, so no answer from before it is applied.
    const latestSearchRef = useRef(0);
    const startSearch = () => {
        latestSearchRef.current += 1;
        return latestSearchRef.current;
    };
    const isLatestSearch = (searchNumber: number) => searchNumber === latestSearchRef.current;

    // Initialize search query if provided
    useEffect(() => {
        if (initialQuery) {
            searchState.setQuery(initialQuery);
        }
    }, [initialQuery]);

    // Eagerly initialize the visualizer plugin registry so isViewableSearchRow()
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
     * One search against the engine the current mode selects, as search number `searchNumber`
     * (startSearch). Natural-language mode with nothing typed issues no request: there is nothing to
     * embed, so the table shows its empty state and `null` is returned so callers do not announce a
     * search that never ran. An answer that arrives after a newer search started is dropped, and
     * `null` is returned for it too.
     */
    const runSearch = async (searchQuery: SearchQuery, searchNumber: number) => {
        if (searchMode === "nlp") {
            if (!searchQuery.query.trim()) {
                setNlpResult(null);
                setAnsweredMode(null);
                searchState.setResult(EMPTY_NLP_RESPONSE);
                return null;
            }
            // Past the route's limit nothing is sent (an auto-refresh included); the query band
            // shows the limit and how much to shorten the query.
            if (nlpQueryLength(searchQuery.query) > NLP_QUERY_MAX_LENGTH) {
                return null;
            }
            const result = await searchAPI.executeNlpSearch(searchQuery, {
                databaseId,
                metadataSearchMode,
                metadataOperator,
                includeOpenSearchConstraints: !useNoOpenSearch,
            });
            if (!isLatestSearch(searchNumber)) return null;
            setNlpResult(result);
            setAnsweredMode("nlp");
            searchState.setResult(result);
            // The answer's rare warnings are shown in the Search notice above the results, which
            // reads them from the answer held here; nothing is toasted, so no toast covers the title
            // row's controls. A filled window is stated under the results instead.
            // The announcement reads the count the badge and the table header show.
            return withNlpResultTotal(result);
        }
        const result = await searchAPI.executeSearch(
            searchQuery,
            databaseId,
            metadataSearchMode,
            metadataOperator,
            { requireLocation: mapScoped }
        );
        if (!isLatestSearch(searchNumber)) return null;
        setNlpResult(null);
        setAnsweredMode("keyword");
        searchState.setResult(result);
        return result;
    };

    /**
     * A failed search, reported as "Search failed" with the API's message (a 503 included: the
     * search ran and failed, the feature is not off). An older search's failure is dropped like its
     * answer would be.
     */
    const reportSearchError = (error: any, searchNumber: number) => {
        if (!isLatestSearch(searchNumber)) return;
        console.error("Search error:", error);
        searchState.setError(error.message || "Search failed");
        showError("Search failed", error.message || "An error occurred while searching");
    };

    const handleSearch = async () => {
        const searchNumber = startSearch();
        try {
            searchState.setLoading(true);
            setCountAnnouncement("");
            const result = await runSearch(searchState.buildSearchQuery(), searchNumber);

            // `null` means no request was issued (empty natural-language query) or a newer search
            // started: nothing to announce. An auto-refresh is not announced either. No toast: the
            // badge in the title row shows the count, and a toast would cover the title row's controls.
            if (result && !autoRefreshing) {
                setCountAnnouncement(searchAnnouncement(result, searchMode === "nlp"));
            }
        } catch (error: any) {
            reportSearchError(error, searchNumber);
        } finally {
            // The loading state lasts until the newest search ends.
            if (isLatestSearch(searchNumber)) {
                searchState.setLoading(false);
                setAutoRefreshing(false);
            }
        }
    };

    // A search whose query differs from state (a sort or page change applied before the reducer
    // re-renders), reported like handleSearch but without the count announcement.
    const runOverride = async (searchQuery: SearchQuery) => {
        const searchNumber = startSearch();
        try {
            searchState.setLoading(true);
            await runSearch(searchQuery, searchNumber);
        } catch (error: any) {
            reportSearchError(error, searchNumber);
        } finally {
            if (isLatestSearch(searchNumber)) {
                searchState.setLoading(false);
            }
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
            const searchNumber = startSearch();
            try {
                searchState.setLoading(true);
                setCountAnnouncement("");
                const result = await runSearch(
                    searchState.buildSearchQuery(newPagination),
                    searchNumber
                );
                if (result) {
                    setCountAnnouncement(searchAnnouncement(result, false));
                }
            } catch (error: any) {
                reportSearchError(error, searchNumber);
            } finally {
                if (isLatestSearch(searchNumber)) {
                    searchState.setLoading(false);
                }
            }
        }
    };

    // A search still running when the mode changes is not dropped: its answer records the mode it ran
    // in (answeredMode), so the other mode never presents it, and the search in the new mode that the
    // auto-refresh starts is newer and wins.
    const handleSearchModeChange = (nextMode: SearchMode) => {
        updatePreferences({ searchMode: nextMode });
        setNlpResult(null);
        searchState.setPagination({ from: 0 });
    };

    const handleClearSearch = () => {
        // Drop any search still running, and show no count until the reloaded answer arrives.
        startSearch();
        setAnsweredMode(null);
        // A database route's locked database is the page's scope, not a filter: it stays.
        searchState.clearSearch(databaseLocked ? ["str_databaseid"] : []);
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

        // Switching to files on the Map view returns to the table; the user's metadata filters stay.
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

    // Entering or leaving the Map view changes only the view. The location scope it needs is added
    // to the request (mapScoped), never to the user's filters, metadata mode or operator, so the
    // sidebar shows and the other views search exactly what the user set.
    const handleViewChange = (view: "table" | "card" | "map") => {
        if (view === currentView) return;
        setCurrentView(view);
        if ((view === "map") !== (currentView === "map")) {
            // A keyword search on the map covers other records than the table's: it runs again,
            // with or without the location scope, from page 1. In natural-language mode the map
            // pages through the answer already held, as the table does, so no search runs and the
            // query is not embedded again.
            searchState.setPagination({ from: 0 });
            if (hasInitialLoad && searchMode !== "nlp") {
                debouncedAutoRefresh(searchState.query);
            }
        }
    };

    const handleSidebarWidthChange = (width: number) => {
        setSidebarWidth(width);
        updatePreferences({ sidebarWidth: width });
    };

    // What the table renders: in natural-language mode the page slice of its own answer, or the
    // empty set when none is held (a keyword listing from before a switch is never shown there);
    // in keyword mode the server page as returned.
    const displayedResult = useMemo(() => {
        if (searchMode === "nlp") {
            if (!nlpResult) return EMPTY_NLP_RESPONSE;
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

    // Whether the page holds an answer to show a count for: one from the mode on screen, and not the
    // empty result Clear all filters leaves while the reloaded list is on its way ("0 results" would
    // describe a search that has not answered).
    const hasAnswer =
        answeredMode === searchMode && (searchMode === "nlp" ? !!nlpResult : !!searchState.result);

    // Calculate pagination values: the count shown, and the pages over the records that can be shown
    // (fewer in natural-language mode, which counts every match but returns at most 100).
    const totalResults = getTotalResultCount(displayedResult);
    const currentPage = 1 + Math.floor(searchState.pagination.from / preferences.pageSize);
    const pageCount = getPageCount(getPagedResultCount(displayedResult), preferences.pageSize);

    // The Search notice above the results: the rare warnings of the natural-language answer on screen,
    // in the page's own words. Derived from that answer, so it can't outlive it: a new search, Clear all
    // filters or a mode switch replaces the answer, and a keyword answer never has one. Dismissing it
    // hides it for that answer only.
    const searchNotices = useMemo(
        () =>
            searchMode === "nlp" && answeredMode === "nlp" && nlpResult
                ? rareNlpWarningTexts(nlpResult.warnings)
                : [],
        [searchMode, answeredMode, nlpResult]
    );
    const showSearchNotice = searchNotices.length > 0 && dismissedNoticeAnswer !== nlpResult;

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
            case "refresh":
                // An archive, delete or unarchive in the table re-runs the current search.
                setAutoRefreshing(true);
                void handleSearch();
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
        // Natural-language mode without its own answer (no query yet, or only a keyword listing
        // from before a switch): the table explains what to type rather than reporting no matches.
        nlpIdle: searchMode === "nlp" && answeredMode !== "nlp",
        // The natural-language answer on screen searched no database (none it covers is one the user
        // can access): the empty table says so, as the Search notice does, instead of "No matches"
        // with a Clear filter action that suggests the filters are the cause.
        nlpNoDatabaseSearched:
            searchMode === "nlp" &&
            answeredMode === "nlp" &&
            nlpSearchedNoDatabase(nlpResult?.warnings),
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
                        totalItemsIsLowerBound={isTotalLowerBound(displayedResult)}
                    />
                );

            case "map":
                if (useMapView) {
                    // The map shows what the table would: in natural-language mode the page of the
                    // answer held, paged in the browser through the same dispatch as the table, so a
                    // page change never runs a keyword search; in keyword mode the server page.
                    return (
                        <SearchPageMapView
                            state={{
                                ...searchState,
                                result: displayedResult,
                                tablePreferences: {
                                    pageSize: preferences.pageSize,
                                },
                                showMapThumbnails: preferences.showMapThumbnails,
                                useMapView: useMapView,
                            }}
                            dispatch={handleListDispatch}
                        />
                    );
                }
                // Fall through to table view if map not available
                return (
                    <SearchPageListView
                        state={listViewState}
                        onShowToast={showSuccess}
                        onCreateAsset={handleCreateAsset}
                        assetActionDisabledReasons={assetActionDisabledReasons}
                        dispatch={handleListDispatch}
                    />
                );

            case "table":
            default:
                return (
                    <SearchPageListView
                        state={listViewState}
                        onShowToast={showSuccess}
                        onCreateAsset={handleCreateAsset}
                        assetActionDisabledReasons={assetActionDisabledReasons}
                        dispatch={handleListDispatch}
                    />
                );
        }
    };

    const containerStyle = maxHeight ? { maxHeight, overflow: "auto" } : {};

    return (
        <div className="search-page" style={containerStyle}>
            {/* Toast notifications at the very top */}
            <ToastManager toasts={toasts} onDismiss={removeToast} />

            {/* Visually hidden; announces a finished search's count politely to screen readers. */}
            <LiveRegion hidden data-testid="search-count-announcement">
                {countAnnouncement}
            </LiveRegion>

            {/* Top Bar */}
            <SearchTopBar
                query={searchState.query}
                onQueryChange={searchState.setQuery}
                onSearch={handleExplicitSearch}
                onClearAll={handleClearSearch}
                loading={searchState.loading}
                resultCount={hasAnswer ? totalResults : undefined}
                resultCountIsLowerBound={hasAnswer && isTotalLowerBound(displayedResult)}
                hasActiveFilters={searchState.hasActiveFilters({ databaseLocked })}
                title={
                    embedded?.title ||
                    (databaseId
                        ? `${Synonyms.Assets} for ${databaseId}`
                        : `${Synonyms.Assets} and Files - Search`)
                }
                searchMode={searchMode}
                searchModeControl={searchModeControl}
                titleSlots={titleSlots}
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
                        reduced={useNoOpenSearch}
                        searchMode={searchMode}
                    />
                }
                rightPanel={
                    <Box padding={{ horizontal: "l", bottom: "l" }}>
                        {/* View Selector */}
                        <Box padding={{ bottom: "m" }}>{renderViewSelector()}</Box>

                        {totalResults > SEARCH_RESULT_WINDOW && (
                            <Box padding={{ bottom: "m" }}>
                                <Alert type="info" statusIconAriaLabel="Info">
                                    Showing the first {SEARCH_RESULT_WINDOW.toLocaleString()}{" "}
                                    results; refine your search
                                </Alert>
                            </Box>
                        )}

                        {/* The rare natural-language warnings, in the page rather than a toast: the
                            toast stack sits over the title row's view switch and Clear all filters. */}
                        {showSearchNotice && (
                            <Box padding={{ bottom: "m" }}>
                                <Alert
                                    type="info"
                                    header="Search notice"
                                    statusIconAriaLabel="Info"
                                    dismissible
                                    dismissAriaLabel="Dismiss search notice"
                                    onDismiss={() => setDismissedNoticeAnswer(nlpResult)}
                                    data-testid="search-notice"
                                >
                                    {searchNotices.map((text) => (
                                        <div key={text}>{text}</div>
                                    ))}
                                </Alert>
                            </Box>
                        )}

                        {/* Results */}
                        {renderContent()}

                        {/* A natural-language call that filled its candidate window found at least
                            this many matches; there is no cursor to fetch the rest. An empty answer
                            names no number: nothing matched among the closest results searched. */}
                        {searchMode === "nlp" && nlpResult?.nlp?.truncated && (
                            <Box
                                padding={{ top: "s" }}
                                color="text-body-secondary"
                                fontSize="body-s"
                            >
                                {nlpResult.hits.hits.length > 0
                                    ? nlpTopMatchesNote(nlpResult.hits.hits.length)
                                    : NLP_EMPTY_TRUNCATED_NOTE}
                            </Box>
                        )}
                        {/* Only a segment window filled: no whole-file window did, but some files
                            may match in more scenes or passages than were checked. */}
                        {searchMode === "nlp" &&
                            nlpResult &&
                            !nlpResult.nlp?.truncated &&
                            (nlpResult.warnings ?? []).some(
                                (w) => w.code === "segments:window_full"
                            ) && (
                                <Box
                                    padding={{ top: "s" }}
                                    color="text-body-secondary"
                                    fontSize="body-s"
                                >
                                    Some files have more matching scenes or passages than were
                                    checked; more may exist.
                                </Box>
                            )}
                    </Box>
                }
                initialLeftWidth={sidebarWidth}
                minLeftWidth={300}
                maxLeftWidth={600}
                onWidthChange={handleSidebarWidthChange}
                className="search-splitter"
            />
        </div>
    );
};

export default ModernSearchContainer;
