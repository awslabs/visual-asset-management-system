/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useCallback } from 'react';
import { 
    fetchAssetVersions, 
    fetchAssetVersion
} from '../../../../services/AssetVersionService';
import { AssetVersion, AssetVersionDetails } from '../AssetVersionManager';

export const useAssetVersions = (databaseId: string, assetId: string) => {
    // State for versions list
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);
    const [versions, setVersions] = useState<AssetVersion[]>([]);
    const [totalVersions, setTotalVersions] = useState<number>(0);
    
    // State for pagination
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [pageSize, setPageSize] = useState<number>(5);
    const [nextToken, setNextToken] = useState<string | null>(null);
    const [pageTokens, setPageTokens] = useState<{[key: number]: string | null | undefined}>({1: null});
    const [allVersions, setAllVersions] = useState<AssetVersion[]>([]);
    
    // State for filtering
    const [filterText, setFilterText] = useState<string>('');
    const [filteredVersions, setFilteredVersions] = useState<AssetVersion[]>([]);
    const [filteredTotalVersions, setFilteredTotalVersions] = useState<number>(0);
    
    // State for selected version
    const [selectedVersion, setSelectedVersion] = useState<AssetVersion | null>(null);
    const [selectedVersionDetails, setSelectedVersionDetails] = useState<AssetVersionDetails | null>(null);
    
    // Load versions
    const loadVersions = useCallback(async (startingToken?: string | undefined | null, page: number = 1, loadAll: boolean = false) => {
        if (!databaseId || !assetId) {
            setError('Database ID and Asset ID are required');
            setLoading(false);
            return;
        }
        
        setLoading(true);
        setError(null);
        
        try {
            const [success, response] = await fetchAssetVersions({
                databaseId,
                assetId,
                maxItems: pageSize,
                startingToken: startingToken || ""
            });
            
            console.log('Asset versions API response:', JSON.stringify(response, null, 2));
            
            if (success && response) {
                try {
                    // Simplified response handling - focus on finding versions array
                    let versionsArray: AssetVersion[] | null = null;
                    let nextTokenValue: string | null = null;
                    let totalCount = 0;
                    
                    // Case 1: Direct array
                    if (Array.isArray(response)) {
                        console.log('Response is a direct array');
                        versionsArray = response;
                        totalCount = response.length;
                    }
                    // Case 2: Object with versions property
                    else if (response.versions && Array.isArray(response.versions)) {
                        console.log('Response has versions array property');
                        versionsArray = response.versions;
                        nextTokenValue = response.nextToken || null;
                        totalCount = response.totalCount || response.versions.length;
                    }
                    // Case 3: Object with message property containing versions
                    else if (response.message) {
                        if (Array.isArray(response.message)) {
                            console.log('Response.message is an array');
                            versionsArray = response.message;
                            totalCount = response.message.length;
                        } else if (response.message.versions && Array.isArray(response.message.versions)) {
                            console.log('Response.message has versions array property');
                            versionsArray = response.message.versions;
                            nextTokenValue = response.message.nextToken || null;
                            totalCount = response.message.totalCount || response.message.versions.length;
                        }
                    }
                    
                    // If we found versions, update state
                    if (versionsArray) {
                        console.log('Found versions array with length:', versionsArray.length);
                        
                        // Update page tokens map
                        const newPageTokens = {...pageTokens};
                        newPageTokens[page] = startingToken;
                        if (nextTokenValue) {
                            newPageTokens[page + 1] = nextTokenValue;
                        }
                        setPageTokens(newPageTokens);
                        
                        // If we're loading all versions (for filtering)
                        if (loadAll) {
                            setAllVersions(prev => [...prev, ...(versionsArray || [])]);
                            
                            // If there's a next token, load the next page
                            if (nextTokenValue) {
                                loadVersions(nextTokenValue, page + 1, true);
                                return;
                            }
                        } else {
                            // Just update the current page
                            setVersions(versionsArray);
                        }
                        
                        // Update total count
                        if (totalCount > 0) {
                            setTotalVersions(totalCount);
                        } else if (nextTokenValue) {
                            // If we have a next token but no total count, we need to estimate
                            setTotalVersions((page * pageSize) + pageSize);
                        } else {
                            // If no next token, we're on the last page
                            setTotalVersions((page - 1) * pageSize + (versionsArray ? versionsArray.length : 0));
                        }
                        
                        setNextToken(nextTokenValue);
                    } else {
                        console.error('Could not find versions array in response:', response);
                        setError('Invalid response format - no versions found');
                    }
                } catch (parseError) {
                    console.error('Error parsing versions response:', parseError);
                    setError('Error parsing response data');
                }
            } else {
                console.error('Failed to load asset versions:', response);
                setError('Failed to load asset versions');
            }
        } catch (err) {
            setError('An error occurred while loading versions');
            console.error('Error loading versions:', err);
            console.error('Error details:', JSON.stringify(err, null, 2));
        } finally {
            if (!loadAll) {
                setLoading(false);
            }
        }
    }, [databaseId, assetId, pageSize, pageTokens]);
    
    // Load version details
    const loadVersionDetails = useCallback(async (version: AssetVersion) => {
        if (!databaseId || !assetId) {
            return;
        }
        
        try {
            console.log('useAssetVersions - Loading version details for version:', version);
            
            // Store the current version to ensure it's not lost during async operations
            const versionToLoad = version;
            
            const [success, response] = await fetchAssetVersion({
                databaseId,
                assetId,
                assetVersionId: `${versionToLoad.Version}`
            });
            
            console.log('useAssetVersions - fetchAssetVersion response:', success, response);
            
            if (success && response) {
                console.log('useAssetVersions - Setting selectedVersionDetails with files:', response.files);
                
                // Make sure we're still on the same version before updating details
                // This prevents race conditions if the user quickly switches between versions
                setSelectedVersionDetails((currentDetails) => {
                    if (currentDetails && currentDetails.assetVersionId === `${versionToLoad.Version}`) {
                        // If we already have details for this version, just update the files
                        return {
                            ...currentDetails,
                            files: response.files
                        };
                    }
                    return response;
                });
            } else {
                console.error('Failed to load version details');
            }
        } catch (err) {
            console.error('Error loading version details:', err);
        }
    }, [databaseId, assetId]);
    
    // Apply filtering
    useEffect(() => {
        if (filterText.trim() === '') {
            setFilteredVersions(versions);
            setFilteredTotalVersions(totalVersions);
            return;
        }
        
        const lowerFilter = filterText.toLowerCase();
        let filtered;
        
        // If we have all versions loaded, filter from there
        if (allVersions.length > 0) {
            filtered = allVersions.filter(version => 
                version.Version.toLowerCase().includes(lowerFilter) ||
                (version.Comment && version.Comment.toLowerCase().includes(lowerFilter)) ||
                (version.createdBy && version.createdBy.toLowerCase().includes(lowerFilter)) ||
                (version.DateModified && version.DateModified.toLowerCase().includes(lowerFilter))
            );
            setFilteredVersions(filtered);
            setFilteredTotalVersions(filtered.length);
        } else {
            // Otherwise just filter the current page
            filtered = versions.filter(version => 
                version.Version.toLowerCase().includes(lowerFilter) ||
                (version.Comment && version.Comment.toLowerCase().includes(lowerFilter)) ||
                (version.createdBy && version.createdBy.toLowerCase().includes(lowerFilter)) ||
                (version.DateModified && version.DateModified.toLowerCase().includes(lowerFilter))
            );
            setFilteredVersions(filtered);
            // We can't know the total filtered count without loading all pages
            setFilteredTotalVersions(filtered.length);
        }
    }, [filterText, versions, allVersions, totalVersions]);
    
    // Handle page change
    const handlePageChange = useCallback((newPage: number) => {
        setCurrentPage(newPage);
        
        // If we're filtering and have all versions loaded, we don't need to fetch from API
        if (filterText.trim() !== '' && allVersions.length > 0) {
            return;
        }
        
        // Calculate starting token based on page number
        if (newPage === 1) {
            loadVersions(null, 1);
        } else if (pageTokens[newPage]) {
            // If we have a token for this page, use it
            loadVersions(pageTokens[newPage], newPage);
        } else if (nextToken) {
            // If we only have the next token, use it
            loadVersions(nextToken, currentPage + 1);
        } else {
            // Fallback - reload from page 1 and navigate forward
            console.warn('Missing token for page navigation, reloading from page 1');
            loadVersions(null, 1);
        }
    }, [loadVersions, nextToken, currentPage, pageTokens, filterText, allVersions.length]);
    
    // Handle page size change
    const handlePageSizeChange = useCallback((newPageSize: number) => {
        setPageSize(newPageSize);
        setCurrentPage(1);
        setPageTokens({1: null});
        loadVersions(null, 1);
    }, [loadVersions]);
    
    // Handle filter change
    const handleFilterChange = useCallback((newFilter: string) => {
        console.log('handleFilterChange called with:', newFilter);
        setFilterText(newFilter);
        
        // Just set the filter text and let the useEffect handle the filtering
        // Don't reload or change pages - this makes it a purely local filter
        
        // REMOVED: Loading all versions when filtering as it causes infinite loading
        // if (newFilter.trim() !== '' && allVersions.length === 0) {
        //     setAllVersions([]);
        //     loadVersions(null, 1, true);
        // }
    }, []);
    
    // Refresh versions
    const refreshVersions = useCallback(() => {
        setCurrentPage(1);
        setPageTokens({1: null});
        setAllVersions([]);
        setFilterText('');
        loadVersions(null, 1);
    }, [loadVersions]);
    
    // Initial load - with a flag to prevent multiple calls
    const [initialLoadDone, setInitialLoadDone] = useState(false);
    
    useEffect(() => {
        if (!initialLoadDone) {
            console.log('useAssetVersions - Initial load');
            loadVersions();
            setInitialLoadDone(true);
        }
    }, [loadVersions, initialLoadDone]);
    
    // Track the last loaded version to prevent duplicate API calls
    const [lastLoadedVersionId, setLastLoadedVersionId] = useState<string | null>(null);
    
    // Load details when selected version changes
    useEffect(() => {
        if (selectedVersion) {
            console.log('useAssetVersions - Selected version changed to:', selectedVersion.Version);
            
            // Only load details if we haven't already loaded this version
            // or if we're explicitly forcing a refresh
            if (lastLoadedVersionId !== selectedVersion.Version) {
                console.log('useAssetVersions - Loading details for version:', selectedVersion.Version);
                loadVersionDetails(selectedVersion);
                setLastLoadedVersionId(selectedVersion.Version);
            } else {
                console.log('useAssetVersions - Skipping load, already have details for version:', selectedVersion.Version);
            }
        } else {
            setSelectedVersionDetails(null);
            setLastLoadedVersionId(null);
        }
    }, [selectedVersion, loadVersionDetails, lastLoadedVersionId]);
    
    // Debug effect to track re-renders and potential loops
    useEffect(() => {
        console.log('useAssetVersions - Hook re-rendered');
        
        // Return cleanup function to track component unmounts
        return () => {
            console.log('useAssetVersions - Hook cleanup');
        };
    }, []);
    
    return {
        loading,
        error,
        versions: filterText.trim() !== '' ? filteredVersions : versions,
        selectedVersion,
        selectedVersionDetails,
        totalVersions: filterText.trim() !== '' ? filteredTotalVersions : totalVersions,
        currentPage,
        pageSize,
        setCurrentPage: handlePageChange,
        setPageSize: handlePageSizeChange,
        setSelectedVersion,
        refreshVersions,
        loadVersionDetails,
        filterText,
        setFilterText: handleFilterChange
    };
};
