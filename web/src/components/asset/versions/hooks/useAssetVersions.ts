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
    const [pageSize] = useState<number>(10);
    const [nextToken, setNextToken] = useState<string | null>(null);
    
    // State for selected version
    const [selectedVersion, setSelectedVersion] = useState<AssetVersion | null>(null);
    const [selectedVersionDetails, setSelectedVersionDetails] = useState<AssetVersionDetails | null>(null);
    
    // Load versions
    const loadVersions = useCallback(async (startingToken?: string | undefined) => {
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
                startingToken: startingToken ? startingToken : ""
            });
            
            console.log('Asset versions API response:', JSON.stringify(response, null, 2));
            
            if (success && response) {
                try {
                    // Simplified response handling - focus on finding versions array
                    let versionsArray = null;
                    let nextTokenValue = null;
                    
                    // Case 1: Direct array
                    if (Array.isArray(response)) {
                        console.log('Response is a direct array');
                        versionsArray = response;
                    }
                    // Case 2: Object with versions property
                    else if (response.versions && Array.isArray(response.versions)) {
                        console.log('Response has versions array property');
                        versionsArray = response.versions;
                        nextTokenValue = response.nextToken || null;
                    }
                    // Case 3: Object with message property containing versions
                    else if (response.message) {
                        if (Array.isArray(response.message)) {
                            console.log('Response.message is an array');
                            versionsArray = response.message;
                        } else if (response.message.versions && Array.isArray(response.message.versions)) {
                            console.log('Response.message has versions array property');
                            versionsArray = response.message.versions;
                            nextTokenValue = response.message.nextToken || null;
                        }
                    }
                    
                    // If we found versions, update state
                    if (versionsArray) {
                        console.log('Found versions array with length:', versionsArray.length);
                        setVersions(versionsArray);
                        setTotalVersions(versionsArray.length);
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
            setLoading(false);
        }
    }, [databaseId, assetId, pageSize]);
    
    // Load version details
    const loadVersionDetails = useCallback(async (version: AssetVersion) => {
        if (!databaseId || !assetId) {
            return;
        }
        
        try {
            const [success, response] = await fetchAssetVersion({
                databaseId,
                assetId,
                assetVersionId: `v${version.Version}`
            });
            
            if (success && response) {
                setSelectedVersionDetails(response);
            } else {
                console.error('Failed to load version details');
            }
        } catch (err) {
            console.error('Error loading version details:', err);
        }
    }, [databaseId, assetId]);
    
    // Handle page change
    const handlePageChange = useCallback((newPage: number) => {
        setCurrentPage(newPage);
        
        // Calculate starting token based on page number
        if (newPage === 1) {
            loadVersions();
        } else {
            // This is a simplified approach - in a real implementation,
            // you would need to track tokens for each page or use a different pagination strategy
            loadVersions(nextToken || undefined);
        }
    }, [loadVersions, nextToken]);
    
    // Refresh versions
    const refreshVersions = useCallback(() => {
        setCurrentPage(1);
        loadVersions();
    }, [loadVersions]);
    
    // Initial load
    useEffect(() => {
        loadVersions();
    }, [loadVersions]);
    
    // Load details when selected version changes
    useEffect(() => {
        if (selectedVersion) {
            loadVersionDetails(selectedVersion);
        } else {
            setSelectedVersionDetails(null);
        }
    }, [selectedVersion, loadVersionDetails]);
    
    return {
        loading,
        error,
        versions,
        selectedVersion,
        selectedVersionDetails,
        totalVersions,
        currentPage,
        pageSize,
        setCurrentPage: handlePageChange,
        setSelectedVersion,
        refreshVersions,
        loadVersionDetails
    };
};
