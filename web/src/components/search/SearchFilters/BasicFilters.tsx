/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    FormField,
    Input,
    Select,
    Grid,
    Button,
    Toggle,
    SpaceBetween,
    Box,
} from "@cloudscape-design/components";
import { SearchFilters } from "../types";
import { fetchAllDatabases, fetchTags } from "../../../services/APIService";
import Synonyms from "../../../synonyms";

interface BasicFiltersProps {
    query: string;
    filters: SearchFilters;
    showThumbnails: boolean;
    onQueryChange: (query: string) => void;
    onFilterChange: (key: string, value: any) => void;
    onThumbnailToggle: () => void;
    onSearch: () => void;
    loading?: boolean;
    searchResult?: any;
}

const BasicFilters: React.FC<BasicFiltersProps> = ({
    query,
    filters,
    showThumbnails,
    onQueryChange,
    onFilterChange,
    onThumbnailToggle,
    onSearch,
    loading = false,
    searchResult,
}) => {
    const [databases, setDatabases] = useState<any[]>([]);
    const [tags, setTags] = useState<any[]>([]);

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
                const tagOptions = res.map((tag: any) => ({
                    label: `${tag.tagName} (${tag.tagTypeName})`,
                    value: tag.tagName,
                }));
                setTags(tagOptions);
            }
        });
    }, []);

    const handleKeyDown = (event: any) => {
        if (event.detail.key === "Enter") {
            onSearch();
        }
    };

    // Build database options with result counts
    const databaseOptions = [
        { label: "All", value: "all" },
        ...databases.map((db: any) => {
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
                label: `${db.databaseId} (Results: ${count} / Total: ${db.assetCount || 0})`,
                value: db.databaseId,
            };
        }),
    ];

    // Build asset type options from aggregations
    const assetTypeOptions = [
        { label: "All", value: "all" },
        ...(searchResult?.aggregations?.str_assettype?.buckets?.map((bucket: any) => ({
            label: `${bucket.key} (${bucket.doc_count})`,
            value: bucket.key,
        })) || []),
    ];

    // Build tag options from aggregations
    const tagOptions = [
        { label: "All", value: "all" },
        ...(searchResult?.aggregations?.list_tags?.buckets?.flatMap((tag: any) =>
            tag.key.split(",").map((value: string) => ({
                label: `${value.trim()} (${tag.doc_count})`,
                value: value.trim(),
            }))
        ) || []),
    ];

    return (
        <SpaceBetween direction="vertical" size="l">
            <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
                {/* Keywords Section */}
                <FormField label="Keywords">
                    <Grid
                        gridDefinition={[{ colspan: { default: 10 } }, { colspan: { default: 2 } }]}
                    >
                        <Input
                            placeholder="Search assets and files..."
                            type="search"
                            value={query}
                            onChange={(e) => onQueryChange(e.detail.value)}
                            onKeyDown={handleKeyDown}
                            disabled={loading}
                        />
                        <Button variant="primary" onClick={onSearch} loading={loading}>
                            Search
                        </Button>
                    </Grid>
                </FormField>

                {/* Filter Types Section */}
                <Box>
                    <Grid
                        gridDefinition={[{ colspan: { default: 9 } }, { colspan: { default: 3 } }]}
                    >
                        <FormField label="Filter Types">
                            <Grid
                                gridDefinition={[
                                    { colspan: { default: 3 } },
                                    { colspan: { default: 3 } },
                                    { colspan: { default: 3 } },
                                    { colspan: { default: 3 } },
                                ]}
                            >
                                {/* Record Type Filter */}
                                <Select
                                    selectedOption={
                                        filters._rectype || {
                                            label: Synonyms.Assets,
                                            value: "asset",
                                        }
                                    }
                                    onChange={({ detail }) =>
                                        onFilterChange("_rectype", detail.selectedOption)
                                    }
                                    options={[
                                        { label: Synonyms.Assets, value: "asset" },
                                        { label: "Files", value: "file" },
                                    ]}
                                    placeholder="Record Type"
                                    disabled={loading}
                                />

                                {/* Database Filter */}
                                <Select
                                    selectedOption={filters.str_databaseid || null}
                                    placeholder="Database"
                                    options={databaseOptions}
                                    onChange={({ detail }) =>
                                        onFilterChange("str_databaseid", detail.selectedOption)
                                    }
                                    disabled={loading}
                                />

                                {/* Asset Type Filter */}
                                <Select
                                    selectedOption={filters.str_assettype || null}
                                    placeholder="Type"
                                    options={assetTypeOptions}
                                    onChange={({ detail }) =>
                                        onFilterChange("str_assettype", detail.selectedOption)
                                    }
                                    disabled={loading}
                                />

                                {/* Tags Filter */}
                                <Select
                                    selectedOption={filters.list_tags || null}
                                    placeholder="Tags"
                                    options={tagOptions}
                                    onChange={({ detail }) =>
                                        onFilterChange("list_tags", detail.selectedOption)
                                    }
                                    disabled={loading}
                                />
                            </Grid>
                        </FormField>

                        {/* Preview Toggle */}
                        <FormField label="Display Options">
                            <Toggle
                                onChange={onThumbnailToggle}
                                checked={showThumbnails}
                                disabled={loading}
                            >
                                Show Preview Thumbnails
                            </Toggle>
                        </FormField>
                    </Grid>
                </Box>
            </Grid>
        </SpaceBetween>
    );
};

export default BasicFilters;
