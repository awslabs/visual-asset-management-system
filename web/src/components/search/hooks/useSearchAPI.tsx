/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback } from 'react';
import { API } from 'aws-amplify';
import { SearchQuery, SearchResponse, MetadataFilter } from '../types';

/**
 * Custom hook for search API operations
 */
export const useSearchAPI = () => {
    const executeSearch = useCallback(async (searchQuery: SearchQuery, databaseId?: string, metadataSearchMode?: string, metadataOperator?: string): Promise<SearchResponse> => {
        try {
            // Build filters array for the new API format
            const filters: object[] = [];

            // Determine if archived items should be included
            const includeArchived = !!searchQuery.filters.bool_archived;
            
            // Add regular filters using query_string format
            Object.keys(searchQuery.filters).forEach(key => {
                const filter = searchQuery.filters[key];
                
                // Skip non-filter fields, _rectype (handled by entityTypes), and bool_archived (handled by includeArchived)
                if (key === 'includeMetadataInKeywordSearch' || key === 'showResultExplanation' || key === '_rectype' || key === 'bool_archived') return;
                
                // Handle boolean filters (relationship filters, etc.)
                if (key.startsWith('bool_') && filter && typeof filter.value === 'boolean') {
                    filters.push({
                        query_string: {
                            query: `(${key}:${filter.value})`,
                        },
                    });
                }
                // Handle regular filters
                else if (filter && filter.value !== 'all' && filter.value !== '') {
                    filters.push({
                        query_string: {
                            query: `(${key}:("${filter.value}"))`,
                        },
                    });
                }
            });

            // Add database filter if specified
            if (databaseId) {
                filters.push({
                    query_string: { 
                        query: `(str_databaseid:("${databaseId}"))` 
                    },
                });
            }

            // Build metadata query for dedicated metadata search
            // Construct proper MD_<type>_<fieldname> format based on search mode
            // Filter out rows with empty field names
            let metadataQuery = '';
            if (searchQuery.metadataFilters.length > 0) {
                // For "value" mode, just send the value (no field name)
                // For "key" mode, just send the field name (no value)
                // For "both" mode, send field:value pairs
                
                if (metadataSearchMode === 'value') {
                    // Value-only mode: just send the values, no field names
                    const metadataValues = searchQuery.metadataFilters
                        .filter(filter => filter.value && filter.value.trim() !== '')
                        .map(filter => filter.value.trim());
                    const operator = metadataOperator || 'AND';
                    metadataQuery = metadataValues.join(` ${operator} `);
                } else if (metadataSearchMode === 'key') {
                    // Key-only mode: just send the field names, no values
                    const metadataKeys = searchQuery.metadataFilters
                        .filter(filter => filter.key && filter.key.trim() !== '')
                        .map(filter => {
                            const fieldType = filter.fieldType || 'str';
                            let fieldName = filter.key;
                            
                            // Remove MD_ prefix if user added it
                            if (fieldName.startsWith('MD_')) {
                                fieldName = fieldName.substring(3);
                            }
                            
                            // Remove type prefix if user added it
                            const typePrefixes = ['str_', 'num_', 'bool_', 'date_', 'list_', 'gp_', 'gs_'];
                            for (const prefix of typePrefixes) {
                                if (fieldName.startsWith(prefix)) {
                                    fieldName = fieldName.substring(prefix.length);
                                    break;
                                }
                            }
                            
                            // Remove wildcards from field names
                            fieldName = fieldName.replace(/[*?]/g, '');
                            
                            return `MD_${fieldType}_${fieldName}`;
                        });
                    const operator = metadataOperator || 'AND';
                    metadataQuery = metadataKeys.join(` ${operator} `);
                } else {
                    // Both mode: send field:value pairs
                    const metadataTerms = searchQuery.metadataFilters
                        .filter(filter => filter.key && filter.key.trim() !== '') // Ignore empty field names
                        .map(filter => {
                            // Construct full field name: MD_<type>_<fieldname>
                            const fieldType = filter.fieldType || 'str';
                            let fieldName = filter.key;
                            
                            // Remove MD_ prefix if user added it
                            if (fieldName.startsWith('MD_')) {
                                fieldName = fieldName.substring(3);
                            }
                            
                            // Remove type prefix if user added it (e.g., str_product -> product)
                            const typePrefixes = ['str_', 'num_', 'bool_', 'date_', 'list_', 'gp_', 'gs_'];
                            for (const prefix of typePrefixes) {
                                if (fieldName.startsWith(prefix)) {
                                    fieldName = fieldName.substring(prefix.length);
                                    break;
                                }
                            }
                            
                            // Remove wildcards from field names
                            fieldName = fieldName.replace(/[*?]/g, '');
                            
                            // Construct final field name
                            const fullFieldName = `MD_${fieldType}_${fieldName}`;
                            return `${fullFieldName}:${filter.value}`;
                        });
                    const operator = metadataOperator || 'AND';
                    metadataQuery = metadataTerms.join(` ${operator} `);
                }
            }

            // Determine entity types based on filters
            const entityTypes: string[] = [];
            const rectypeFilter = searchQuery.filters._rectype;
            if (rectypeFilter) {
                if (rectypeFilter.value === 'asset') {
                    entityTypes.push('asset');
                } else if (rectypeFilter.value === 'file') {
                    entityTypes.push('file');
                }
            } else {
                // Default to both if no filter specified
                entityTypes.push('asset', 'file');
            }

            // Build the request body using the new SearchRequestModel format
            const body = {
                query: searchQuery.query || undefined,
                filters: filters.length > 0 ? filters : undefined,
                sort: searchQuery.sort,
                from: searchQuery.pagination.from,
                size: searchQuery.pagination.size,
                entityTypes: entityTypes.length > 0 ? entityTypes : undefined,
                metadataQuery: metadataQuery || undefined,
                metadataSearchMode: metadataSearchMode || 'both',
                includeMetadataInSearch: searchQuery.filters.includeMetadataInKeywordSearch !== false,
                aggregations: true,
                includeHighlights: true,
                explainResults: searchQuery.filters.showResultExplanation || false,
                includeArchived: includeArchived, // Include archived items if bool_archived filter is set
            };

            console.log('Search API request body:', body);

            // Execute the search
            const response = await API.post('api', 'search', {
                'Content-type': 'application/json',
                body: body,
            });

            return response;
        } catch (error) {
            console.error('Search API error:', error);
            throw error;
        }
    }, []);

    const executeSimpleSearch = useCallback(async (params: {
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

            const response = await API.post('api', 'search/simple', {
                'Content-type': 'application/json',
                body: body,
            });

            return response;
        } catch (error) {
            console.error('Simple search API error:', error);
            throw error;
        }
    }, []);

    const getSearchMappings = useCallback(async (): Promise<any> => {
        try {
            const response = await API.get('api', 'search', {});
            return response;
        } catch (error) {
            console.error('Search mappings API error:', error);
            throw error;
        }
    }, []);

    const buildSortQuery = useCallback((sortField: string, isDescending: boolean) => {
        let sortingFieldIndex = sortField;
        
        // Handle string fields that need .keyword suffix for sorting (backend expects this)
        if (sortField.indexOf('str_') === 0) {
            sortingFieldIndex = sortField + '.keyword';
        }

        return [
            {
                field: sortingFieldIndex,
                order: isDescending ? 'desc' : 'asc',
            },
            '_score',
        ];
    }, []);

    return {
        executeSearch,
        executeSimpleSearch,
        getSearchMappings,
        buildSortQuery,
    };
};

export default useSearchAPI;
