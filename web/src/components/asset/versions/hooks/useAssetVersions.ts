/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useCallback } from "react";
import { fetchAllAssetVersions, fetchAssetVersion } from "../../../../services/AssetVersionService";
import { AssetVersion, AssetVersionDetails } from "../AssetVersionManager";

export const useAssetVersions = (databaseId: string, assetId: string) => {
    // State for versions list
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);
    const [allVersions, setAllVersions] = useState<AssetVersion[]>([]);

    // State for pagination (local)
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [pageSize, setPageSize] = useState<number>(5);

    // State for filtering
    const [filterText, setFilterText] = useState<string>("");

    // State for sorting
    const [sortingColumn, setSortingColumn] = useState<{
        sortingField: string;
        isDescending: boolean;
    }>({
        sortingField: "DateModified",
        isDescending: true, // Default to descending (newest first)
    });

    // State for selected version
    const [selectedVersion, setSelectedVersion] = useState<AssetVersion | null>(null);
    const [selectedVersionDetails, setSelectedVersionDetails] =
        useState<AssetVersionDetails | null>(null);

    // Track the last loaded version to prevent duplicate API calls
    const [lastLoadedVersionId, setLastLoadedVersionId] = useState<string | null>(null);

    // Load all versions from API
    const loadVersions = useCallback(async () => {
        if (!databaseId || !assetId) {
            setError("Database ID and Asset ID are required");
            setLoading(false);
            return;
        }

        setLoading(true);
        setError(null);

        try {
            const [success, response] = await fetchAllAssetVersions({
                databaseId,
                assetId,
                pageSize: 100, // Fetch 100 at a time from backend
            });

            console.log("Fetched all asset versions:", response);

            if (success && response && response.versions) {
                setAllVersions(response.versions);
            } else {
                console.error("Failed to load asset versions:", response);
                setError("Failed to load asset versions");
            }
        } catch (err) {
            setError("An error occurred while loading versions");
            console.error("Error loading versions:", err);
        } finally {
            setLoading(false);
        }
    }, [databaseId, assetId]);

    // Compute filtered versions
    const filteredVersions = allVersions.filter((version) => {
        if (filterText.trim() === "") return true;

        const lowerFilter = filterText.toLowerCase();
        return (
            version.Version.toLowerCase().includes(lowerFilter) ||
            (version.Comment && version.Comment.toLowerCase().includes(lowerFilter)) ||
            (version.createdBy && version.createdBy.toLowerCase().includes(lowerFilter)) ||
            (version.DateModified && version.DateModified.toLowerCase().includes(lowerFilter))
        );
    });

    // Apply sorting to filtered versions
    const sortedVersions = [...filteredVersions].sort((a, b) => {
        const field = sortingColumn.sortingField;
        let aValue: any;
        let bValue: any;

        switch (field) {
            case "Version":
                aValue = parseInt(a.Version);
                bValue = parseInt(b.Version);
                break;
            case "DateModified":
                aValue = new Date(a.DateModified).getTime();
                bValue = new Date(b.DateModified).getTime();
                break;
            case "createdBy":
                aValue = (a.createdBy || "").toLowerCase();
                bValue = (b.createdBy || "").toLowerCase();
                break;
            case "Comment":
                aValue = (a.Comment || "").toLowerCase();
                bValue = (b.Comment || "").toLowerCase();
                break;
            default:
                return 0;
        }

        if (aValue < bValue) return sortingColumn.isDescending ? 1 : -1;
        if (aValue > bValue) return sortingColumn.isDescending ? -1 : 1;
        return 0;
    });

    // Compute paginated versions (local pagination)
    const paginatedVersions = sortedVersions.slice(
        (currentPage - 1) * pageSize,
        currentPage * pageSize
    );

    // Handle sorting change
    const handleSortingChange = useCallback(
        (detail: { sortingColumn: { sortingField?: string }; isDescending?: boolean }) => {
            setSortingColumn({
                sortingField: detail.sortingColumn.sortingField || "DateModified",
                isDescending: detail.isDescending !== undefined ? detail.isDescending : true,
            });
            setCurrentPage(1); // Reset to first page when sorting changes
        },
        []
    );

    // Handle page change (local pagination)
    const handlePageChange = useCallback((newPage: number) => {
        setCurrentPage(newPage);
    }, []);

    // Handle page size change (local pagination)
    const handlePageSizeChange = useCallback((newPageSize: number) => {
        setPageSize(newPageSize);
        setCurrentPage(1); // Reset to first page
    }, []);

    // Handle filter change
    const handleFilterChange = useCallback((newFilter: string) => {
        console.log("handleFilterChange called with:", newFilter);
        setFilterText(newFilter);
        setCurrentPage(1); // Reset to first page when filtering
    }, []);

    // Refresh versions
    const refreshVersions = useCallback(() => {
        setCurrentPage(1);
        setFilterText("");
        loadVersions();
    }, [loadVersions]);

    // Load version details
    const loadVersionDetails = useCallback(
        async (version: AssetVersion) => {
            if (!databaseId || !assetId) {
                return;
            }

            try {
                console.log("useAssetVersions - Loading version details for version:", version);

                const versionToLoad = version;

                const [success, response] = await fetchAssetVersion({
                    databaseId,
                    assetId,
                    assetVersionId: `${versionToLoad.Version}`,
                });

                console.log("useAssetVersions - fetchAssetVersion response:", success, response);

                if (success && response) {
                    console.log(
                        "useAssetVersions - Setting selectedVersionDetails with files:",
                        response.files
                    );

                    setSelectedVersionDetails((currentDetails) => {
                        if (
                            currentDetails &&
                            currentDetails.assetVersionId === `${versionToLoad.Version}`
                        ) {
                            return {
                                ...currentDetails,
                                files: response.files,
                            };
                        }
                        return response;
                    });
                } else {
                    console.error("Failed to load version details");
                }
            } catch (err) {
                console.error("Error loading version details:", err);
            }
        },
        [databaseId, assetId]
    );

    // Initial load
    useEffect(() => {
        console.log("useAssetVersions - Initial load");
        loadVersions();
    }, [loadVersions]);

    // Load details when selected version changes
    useEffect(() => {
        if (selectedVersion) {
            console.log("useAssetVersions - Selected version changed to:", selectedVersion.Version);

            if (lastLoadedVersionId !== selectedVersion.Version) {
                console.log(
                    "useAssetVersions - Loading details for version:",
                    selectedVersion.Version
                );
                loadVersionDetails(selectedVersion);
                setLastLoadedVersionId(selectedVersion.Version);
            } else {
                console.log(
                    "useAssetVersions - Skipping load, already have details for version:",
                    selectedVersion.Version
                );
            }
        } else {
            setSelectedVersionDetails(null);
            setLastLoadedVersionId(null);
        }
    }, [selectedVersion, loadVersionDetails, lastLoadedVersionId]);

    return {
        loading,
        error,
        versions: paginatedVersions,
        selectedVersion,
        selectedVersionDetails,
        totalVersions: sortedVersions.length,
        currentPage,
        pageSize,
        setCurrentPage: handlePageChange,
        setPageSize: handlePageSizeChange,
        setSelectedVersion,
        refreshVersions,
        loadVersionDetails,
        filterText,
        setFilterText: handleFilterChange,
        sortingColumn,
        setSortingColumn: handleSortingChange,
    };
};
