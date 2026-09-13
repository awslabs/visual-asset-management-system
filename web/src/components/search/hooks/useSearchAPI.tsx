/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback } from "react";
import {
    searchAssets,
    searchAssetsSimple,
    fetchSearchMappings,
    searchNlp,
} from "../../../services/APIService";
import { SearchQuery, SearchResponse, NlpSearchResponse } from "../types";
import {
    buildKeywordSearchRequest,
    buildNlpSearchRequest,
    NlpRequestOptions,
} from "./searchRequestBuilders";

/**
 * Custom hook for search API operations
 */
export const useSearchAPI = () => {
    const executeSearch = useCallback(
        async (
            searchQuery: SearchQuery,
            databaseId?: string,
            metadataSearchMode?: string,
            metadataOperator?: string
        ): Promise<SearchResponse> => {
            try {
                const body = buildKeywordSearchRequest(
                    searchQuery,
                    databaseId,
                    metadataSearchMode,
                    metadataOperator
                );

                console.log("Search API request body:", body);

                const [success, result] = (await searchAssets(body)) as [boolean, any];
                if (!success) {
                    throw new Error(result || "Search failed");
                }

                return result;
            } catch (error) {
                console.error("Search API error:", error);
                throw error;
            }
        },
        []
    );

    /**
     * Natural-language search over file embeddings (`POST /search/nlp`). Returns the same envelope
     * as `executeSearch` plus the `nlp` summary; paging over its hits is the caller's.
     */
    const executeNlpSearch = useCallback(
        async (
            searchQuery: SearchQuery,
            options: NlpRequestOptions
        ): Promise<NlpSearchResponse> => {
            const body = buildNlpSearchRequest(searchQuery, options);
            console.log("NLP search API request body:", body);
            const [success, result] = await searchNlp(body);
            if (!success) {
                throw new Error(
                    typeof result === "string" ? result : "Natural-language search failed"
                );
            }
            return result as NlpSearchResponse;
        },
        []
    );

    const executeSimpleSearch = useCallback(
        async (params: {
            query?: string;
            assetName?: string;
            assetId?: string;
            assetType?: string;
            fileKey?: string;
            fileExtension?: string;
            databaseId?: string;
            tags?: string[];
            metadataKey?: string;
            metadataValue?: string;
            includeArchived?: boolean;
            from?: number;
            size?: number;
            entityTypes?: string[];
        }): Promise<SearchResponse> => {
            try {
                // Build request body matching SimpleSearchRequestModel
                const body = {
                    query: params.query,
                    assetName: params.assetName,
                    assetId: params.assetId,
                    assetType: params.assetType,
                    fileKey: params.fileKey,
                    fileExtension: params.fileExtension,
                    databaseId: params.databaseId,
                    tags: params.tags,
                    metadataKey: params.metadataKey,
                    metadataValue: params.metadataValue,
                    includeArchived: params.includeArchived || false,
                    from: params.from || 0,
                    size: params.size || 100,
                    entityTypes: params.entityTypes,
                };

                const [success, result] = (await searchAssetsSimple(body)) as [boolean, any];
                if (!success) {
                    throw new Error(result || "Simple search failed");
                }

                return result;
            } catch (error) {
                console.error("Simple search API error:", error);
                throw error;
            }
        },
        []
    );

    const getSearchMappings = useCallback(async (): Promise<any> => {
        try {
            const response = await fetchSearchMappings();
            return response;
        } catch (error) {
            console.error("Search mappings API error:", error);
            throw error;
        }
    }, []);

    const buildSortQuery = useCallback((sortField: string, isDescending: boolean) => {
        // Send field name as-is without .keyword suffix
        return [
            {
                field: sortField,
                order: isDescending ? "desc" : "asc",
            },
        ];
    }, []);

    return {
        executeSearch,
        executeNlpSearch,
        executeSimpleSearch,
        getSearchMappings,
        buildSortQuery,
    };
};

export default useSearchAPI;
