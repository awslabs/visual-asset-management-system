/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Container,
    Header,
    FormField,
    Multiselect,
    Toggle,
    SpaceBetween,
    ExpandableSection,
    Box,
    Input,
} from "@cloudscape-design/components";
import { SearchFilters } from "../types";
import { fetchAllDatabases, fetchTags } from "../../../services/APIService";

interface BasicFiltersPanelProps {
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;
    loading?: boolean;
    searchResult?: any;
    databaseLocked?: boolean;
    recordType: "asset" | "file";
}

const BasicFiltersPanel: React.FC<BasicFiltersPanelProps> = ({
    filters,
    onFilterChange,
    loading = false,
    searchResult,
    databaseLocked = false,
    recordType,
}) => {
    const [databases, setDatabases] = useState<any[]>([]);
    const [tags, setTags] = useState<any[]>([]);

    // Cache for aggregation results from non-filtered searches
    const [cachedAssetTypes, setCachedAssetTypes] = useState<
        Array<{ label: string; value: string }>
    >([]);
    const [cachedFileTypes, setCachedFileTypes] = useState<Array<{ label: string; value: string }>>(
        []
    );
    const [cachedTags, setCachedTags] = useState<Array<{ label: string; value: string }>>([]);

    useEffect(() => {
        // Load databases
        fetchAllDatabases().then((res) => {
            if (res && Array.isArray(res)) {
                setDatabases(res);
            }
        });

        // Load tags
        fetchTags().then((res) => {
            if (res && Array.isArray(res)) {
                setTags(res);
            }
        });
    }, []);

    // Check if this is a non-filtered search (no query, no basic filters except rectype)
    const isNonFilteredSearch = () => {
        const hasQuery = filters._rectype; // Always has rectype
        const filterKeys = Object.keys(filters).filter(
            (key) =>
                key !== "_rectype" &&
                key !== "includeMetadataInKeywordSearch" &&
                key !== "showResultExplanation" &&
                filters[key] !== null &&
                filters[key] !== undefined
        );
        return filterKeys.length === 0;
    };

    // Update cache when we get results from a non-filtered search
    useEffect(() => {
        if (searchResult?.aggregations && isNonFilteredSearch()) {
            // Cache asset types
            if (recordType === "asset" && searchResult.aggregations.str_assettype?.buckets) {
                const assetTypes = searchResult.aggregations.str_assettype.buckets.map(
                    (bucket: any) => ({
                        label: `${bucket.key} (${bucket.doc_count})`,
                        value: bucket.key,
                    })
                );
                setCachedAssetTypes(assetTypes);
            }

            // Cache file types
            if (recordType === "file" && searchResult.aggregations.str_fileext?.buckets) {
                const fileTypes = searchResult.aggregations.str_fileext.buckets.map(
                    (bucket: any) => ({
                        label: `${bucket.key} (${bucket.doc_count})`,
                        value: bucket.key,
                    })
                );
                setCachedFileTypes(fileTypes);
            }

            // Cache tags
            if (searchResult.aggregations.list_tags?.buckets) {
                const tagList = searchResult.aggregations.list_tags.buckets.flatMap((tag: any) =>
                    tag.key.split(",").map((value: string) => ({
                        label: `${value.trim()} (${tag.doc_count})`,
                        value: value.trim(),
                    }))
                );
                setCachedTags(tagList);
            }
        }
    }, [searchResult, recordType, filters]);

    // Build database options with result counts
    const databaseOptions = databases.map((db: any) => {
        let count = 0;
        // Map through result aggregation to find doc_count for each database
        if (searchResult?.aggregations?.str_databaseid?.buckets) {
            const bucket = searchResult.aggregations.str_databaseid.buckets.find(
                (b: any) => b.key === db.databaseId
            );
            if (bucket) {
                count = bucket.doc_count;
            }
        }

        return {
            label: `${db.databaseId} (${count} results)`,
            value: db.databaseId,
        };
    });

    // Build asset type options - use cache if available and we're in a filtered search
    const assetTypeOptions = (() => {
        const currentOptions =
            searchResult?.aggregations?.str_assettype?.buckets?.map((bucket: any) => ({
                label: `${bucket.key} (${bucket.doc_count})`,
                value: bucket.key,
            })) || [];

        // Use cache if we have it and current results are filtered
        if (cachedAssetTypes.length > 0 && !isNonFilteredSearch()) {
            return cachedAssetTypes;
        }
        return currentOptions;
    })();

    // Build file type options - use cache if available and we're in a filtered search
    const fileTypeOptions = (() => {
        const currentOptions =
            searchResult?.aggregations?.str_fileext?.buckets?.map((bucket: any) => ({
                label: `${bucket.key} (${bucket.doc_count})`,
                value: bucket.key,
            })) || [];

        // Use cache if we have it and current results are filtered
        if (cachedFileTypes.length > 0 && !isNonFilteredSearch()) {
            return cachedFileTypes;
        }
        return currentOptions;
    })();

    // Build tag options - use cache if available and we're in a filtered search
    const tagOptions = (() => {
        const currentOptions =
            searchResult?.aggregations?.list_tags?.buckets?.flatMap((tag: any) =>
                tag.key.split(",").map((value: string) => ({
                    label: `${value.trim()} (${tag.doc_count})`,
                    value: value.trim(),
                }))
            ) || [];

        // Use cache if we have it and current results are filtered
        if (cachedTags.length > 0 && !isNonFilteredSearch()) {
            return cachedTags;
        }
        return currentOptions;
    })();

    // Get selected options for each filter
    const selectedDatabases = filters.str_databaseid?.values
        ? filters.str_databaseid.values.map((val) => ({
              label: val,
              value: val,
          }))
        : [];

    const selectedAssetTypes = filters.str_assettype?.values
        ? filters.str_assettype.values.map((val) => ({
              label: val,
              value: val,
          }))
        : [];

    const selectedFileTypes = filters.str_fileext?.values
        ? filters.str_fileext.values.map((val) => ({
              label: val,
              value: val,
          }))
        : [];

    const selectedTags = filters.list_tags?.values
        ? filters.list_tags.values.map((val) => ({
              label: val,
              value: val,
          }))
        : [];

    return (
        <ExpandableSection
            headerText="Basic Filters"
            variant="footer"
            defaultExpanded={true}
            headingTagOverride="h5"
        >
            <Box variant="p" color="text-body-secondary" margin={{ bottom: "s" }}>
                Note: Type and tag values are cached from non-filtered searches for easier
                multi-selection
            </Box>
            <SpaceBetween direction="vertical" size="m">
                {/* Asset Name Filter */}
                <FormField label="Asset Name" description="Search by asset name (exact)">
                    <Input
                        value={filters.str_assetname?.value || ""}
                        onChange={({ detail }) => {
                            if (detail.value.trim() === "") {
                                // Clear filter if empty
                                onFilterChange("str_assetname", null);
                            } else {
                                onFilterChange("str_assetname", {
                                    value: detail.value,
                                });
                            }
                        }}
                        placeholder="Enter asset name (e.g., MyAsset or My*)"
                        disabled={loading}
                        type="text"
                    />
                </FormField>

                {/* Database Filter */}
                <FormField
                    label="Database"
                    description={
                        databaseLocked
                            ? "Locked by URL parameter"
                            : "Select one or more databases (empty = all)"
                    }
                >
                    <Multiselect
                        selectedOptions={selectedDatabases}
                        onChange={({ detail }) => {
                            const selectedValues = detail.selectedOptions
                                .map((opt) => opt.value)
                                .filter((val): val is string => val !== undefined);

                            if (selectedValues.length === 0) {
                                // No selection = All databases
                                onFilterChange("str_databaseid", null);
                            } else {
                                onFilterChange("str_databaseid", {
                                    label: selectedValues.join(", "),
                                    value: selectedValues[0], // Keep first for backward compatibility
                                    values: selectedValues,
                                });
                            }
                        }}
                        options={databaseOptions}
                        placeholder="All databases"
                        disabled={loading || databaseLocked}
                        filteringType="auto"
                    />
                </FormField>

                {/* Asset Type Filter - Only show for assets */}
                {recordType === "asset" && (
                    <FormField
                        label="Asset Type"
                        description="Select one or more types (empty = all)"
                    >
                        <Multiselect
                            selectedOptions={selectedAssetTypes}
                            onChange={({ detail }) => {
                                const selectedValues = detail.selectedOptions
                                    .map((opt) => opt.value)
                                    .filter((val): val is string => val !== undefined);

                                if (selectedValues.length === 0) {
                                    // No selection = All types
                                    onFilterChange("str_assettype", null);
                                } else {
                                    onFilterChange("str_assettype", {
                                        label: selectedValues.join(", "),
                                        value: selectedValues[0], // Keep first for backward compatibility
                                        values: selectedValues,
                                    });
                                }
                            }}
                            options={assetTypeOptions}
                            placeholder="All types"
                            disabled={loading}
                            filteringType="auto"
                        />
                    </FormField>
                )}

                {/* File Type Filter - Only show for files */}
                {recordType === "file" && (
                    <FormField
                        label="File Type"
                        description="Select one or more types (empty = all)"
                    >
                        <Multiselect
                            selectedOptions={selectedFileTypes}
                            onChange={({ detail }) => {
                                const selectedValues = detail.selectedOptions
                                    .map((opt) => opt.value)
                                    .filter((val): val is string => val !== undefined);

                                if (selectedValues.length === 0) {
                                    // No selection = All types
                                    onFilterChange("str_fileext", null);
                                } else {
                                    onFilterChange("str_fileext", {
                                        label: selectedValues.join(", "),
                                        value: selectedValues[0], // Keep first for backward compatibility
                                        values: selectedValues,
                                    });
                                }
                            }}
                            options={fileTypeOptions}
                            placeholder="All types"
                            disabled={loading}
                            filteringType="auto"
                        />
                    </FormField>
                )}

                {/* Tags Filter */}
                <FormField label="Tags" description="Select one or more tags (empty = all)">
                    <Multiselect
                        selectedOptions={selectedTags}
                        onChange={({ detail }) => {
                            const selectedValues = detail.selectedOptions
                                .map((opt) => opt.value)
                                .filter((val): val is string => val !== undefined);

                            if (selectedValues.length === 0) {
                                // No selection = All tags
                                onFilterChange("list_tags", null);
                            } else {
                                onFilterChange("list_tags", {
                                    label: selectedValues.join(", "),
                                    value: selectedValues[0], // Keep first for backward compatibility
                                    values: selectedValues,
                                });
                            }
                        }}
                        options={tagOptions}
                        placeholder="All tags"
                        disabled={loading}
                        filteringType="auto"
                    />
                </FormField>
            </SpaceBetween>
        </ExpandableSection>
    );
};

export default BasicFiltersPanel;
