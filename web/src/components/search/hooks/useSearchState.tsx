/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useCallback, useReducer, useEffect } from "react";
import { SearchFilters, SearchQuery, SearchResponse, SearchResult, MetadataFilter } from "../types";

interface SearchState {
    query: string;
    filters: SearchFilters;
    metadataFilters: MetadataFilter[];
    sort: any[];
    tableSort: {
        sortingField?: string;
        sortingDescending?: boolean;
    };
    pagination: {
        from: number;
        size: number;
    };
    loading: boolean;
    error: string | null;
    result: SearchResponse | null;
    selectedItems: SearchResult[];
    columnNames: string[];
    initialResult: boolean;
}

type SearchAction =
    | { type: "SET_QUERY"; payload: string }
    | { type: "SET_FILTERS"; payload: SearchFilters }
    | { type: "SET_METADATA_FILTERS"; payload: MetadataFilter[] }
    | { type: "SET_SORT"; payload: any[] }
    | { type: "SET_TABLE_SORT"; payload: { sortingField?: string; sortingDescending?: boolean } }
    | { type: "SET_PAGINATION"; payload: { from: number; size?: number } }
    | { type: "SET_LOADING"; payload: boolean }
    | { type: "SET_ERROR"; payload: string | null }
    | { type: "SET_RESULT"; payload: SearchResponse }
    | { type: "SET_SELECTED_ITEMS"; payload: SearchResult[] }
    | { type: "CLEAR_SEARCH" }
    | { type: "RESET_PAGINATION" };

const initialState: SearchState = {
    query: "",
    filters: {
        _rectype: {
            label: "Assets",
            value: "asset",
        },
    },
    metadataFilters: [],
    sort: [],
    tableSort: {},
    pagination: {
        from: 0,
        size: 50, // Match default page size
    },
    loading: false,
    error: null,
    result: null,
    selectedItems: [],
    columnNames: [],
    initialResult: false,
};

function searchReducer(state: SearchState, action: SearchAction): SearchState {
    switch (action.type) {
        case "SET_QUERY":
            return {
                ...state,
                query: action.payload,
                pagination: { ...state.pagination, from: 0 }, // Reset pagination on query change
            };

        case "SET_FILTERS":
            return {
                ...state,
                filters: action.payload,
                pagination: { ...state.pagination, from: 0 }, // Reset pagination on filter change
                // Preserve sort state when filters change
            };

        case "SET_METADATA_FILTERS":
            return {
                ...state,
                metadataFilters: action.payload,
                pagination: { ...state.pagination, from: 0 }, // Reset pagination on metadata filter change
            };

        case "SET_SORT":
            return {
                ...state,
                sort: action.payload,
                pagination: { ...state.pagination, from: 0 }, // Reset pagination on sort change
            };

        case "SET_TABLE_SORT":
            return {
                ...state,
                tableSort: action.payload,
            };

        case "SET_PAGINATION":
            return {
                ...state,
                pagination: {
                    ...state.pagination,
                    ...action.payload,
                },
            };

        case "SET_LOADING":
            return {
                ...state,
                loading: action.payload,
                error: action.payload ? null : state.error, // Clear error when starting new request
            };

        case "SET_ERROR":
            return {
                ...state,
                error: action.payload,
                loading: false,
            };

        case "SET_RESULT":
            // Extract column names from the first few results
            const columnNames =
                action.payload?.hits?.hits?.length > 0
                    ? Array.from(
                          new Set(
                              action.payload.hits.hits
                                  .slice(0, 10) // Sample first 10 results
                                  .flatMap((hit) => Object.keys(hit._source))
                                  .filter((key) => key.indexOf("_") > 0) // Filter out internal fields
                          )
                      )
                    : [];

            return {
                ...state,
                result: action.payload,
                loading: false,
                error: null,
                columnNames,
                selectedItems: [], // Clear selection on new results
                initialResult: true,
            };

        case "SET_SELECTED_ITEMS":
            return {
                ...state,
                selectedItems: action.payload,
            };

        case "CLEAR_SEARCH":
            return {
                ...initialState,
                filters: {
                    _rectype: state.filters._rectype, // Only preserve record type filter
                },
            };

        case "RESET_PAGINATION":
            return {
                ...state,
                pagination: { ...state.pagination, from: 0 },
            };

        default:
            return state;
    }
}

/**
 * Custom hook for managing search state
 */
export const useSearchState = (initialFilters?: SearchFilters, databaseId?: string) => {
    const [state, dispatch] = useReducer(searchReducer, {
        ...initialState,
        filters: {
            ...initialState.filters,
            ...initialFilters,
            ...(databaseId && {
                str_databaseid: {
                    label: databaseId,
                    value: databaseId,
                },
            }),
        },
    });

    // Action creators
    const setQuery = useCallback((query: string) => {
        dispatch({ type: "SET_QUERY", payload: query });
    }, []);

    const setFilters = useCallback((filters: SearchFilters) => {
        dispatch({ type: "SET_FILTERS", payload: filters });
    }, []);

    const updateFilter = useCallback(
        (key: string, value: any) => {
            const updatedFilters = {
                ...state.filters,
                [key]: value,
            };
            dispatch({ type: "SET_FILTERS", payload: updatedFilters });
        },
        [state.filters]
    );

    const removeFilter = useCallback(
        (key: string) => {
            const updatedFilters = { ...state.filters };
            delete updatedFilters[key];
            dispatch({ type: "SET_FILTERS", payload: updatedFilters });
        },
        [state.filters]
    );

    const setMetadataFilters = useCallback((metadataFilters: MetadataFilter[]) => {
        dispatch({ type: "SET_METADATA_FILTERS", payload: metadataFilters });
    }, []);

    const addMetadataFilter = useCallback(
        (filter: MetadataFilter) => {
            const updatedFilters = [...state.metadataFilters, filter];
            dispatch({ type: "SET_METADATA_FILTERS", payload: updatedFilters });
        },
        [state.metadataFilters]
    );

    const removeMetadataFilter = useCallback(
        (index: number) => {
            const updatedFilters = state.metadataFilters.filter((_, i) => i !== index);
            dispatch({ type: "SET_METADATA_FILTERS", payload: updatedFilters });
        },
        [state.metadataFilters]
    );

    const setSort = useCallback((sort: any[]) => {
        dispatch({ type: "SET_SORT", payload: sort });
    }, []);

    const setTableSort = useCallback(
        (tableSort: { sortingField?: string; sortingDescending?: boolean }) => {
            dispatch({ type: "SET_TABLE_SORT", payload: tableSort });
        },
        []
    );

    const setPagination = useCallback((pagination: { from: number; size?: number }) => {
        dispatch({ type: "SET_PAGINATION", payload: pagination });
    }, []);

    const setLoading = useCallback((loading: boolean) => {
        dispatch({ type: "SET_LOADING", payload: loading });
    }, []);

    const setError = useCallback((error: string | null) => {
        dispatch({ type: "SET_ERROR", payload: error });
    }, []);

    const setResult = useCallback((result: SearchResponse) => {
        dispatch({ type: "SET_RESULT", payload: result });
    }, []);

    const setSelectedItems = useCallback((items: SearchResult[]) => {
        dispatch({ type: "SET_SELECTED_ITEMS", payload: items });
    }, []);

    const clearSearch = useCallback(() => {
        dispatch({ type: "CLEAR_SEARCH" });
    }, []);

    const resetPagination = useCallback(() => {
        dispatch({ type: "RESET_PAGINATION" });
    }, []);

    // Build search query object
    const buildSearchQuery = useCallback(
        (overridePagination?: { from: number; size: number }): SearchQuery => {
            return {
                query: state.query,
                filters: state.filters,
                metadataFilters: state.metadataFilters,
                sort: state.sort,
                pagination: overridePagination || state.pagination,
            };
        },
        [state.query, state.filters, state.metadataFilters, state.sort, state.pagination]
    );

    // Check if search has active filters
    const hasActiveFilters = useCallback(() => {
        const hasQuery = state.query.trim().length > 0;
        const hasFilters = Object.keys(state.filters).some((key) => {
            const filter = state.filters[key];
            return filter && filter.value !== "all" && filter.value !== "";
        });
        const hasMetadataFilters = state.metadataFilters.length > 0;

        return hasQuery || hasFilters || hasMetadataFilters;
    }, [state.query, state.filters, state.metadataFilters]);

    return {
        // State
        ...state,

        // Actions
        setQuery,
        setFilters,
        updateFilter,
        removeFilter,
        setMetadataFilters,
        addMetadataFilter,
        removeMetadataFilter,
        setSort,
        setTableSort,
        setPagination,
        setLoading,
        setError,
        setResult,
        setSelectedItems,
        clearSearch,
        resetPagination,

        // Computed values
        buildSearchQuery,
        hasActiveFilters,
    };
};

export default useSearchState;
