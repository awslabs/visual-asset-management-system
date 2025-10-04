/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from 'react';
import {
    Container,
    Header,
    FormField,
    Select,
    Toggle,
    SpaceBetween,
    ExpandableSection,
    Box,
} from '@cloudscape-design/components';
import { SearchFilters } from '../types';
import { fetchAllDatabases, fetchTags } from '../../../services/APIService';

interface BasicFiltersPanelProps {
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;
    loading?: boolean;
    searchResult?: any;
    databaseLocked?: boolean;
    recordType: 'asset' | 'file';
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

    // Build database options with result counts
    const databaseOptions = [
        { label: 'All Databases', value: 'all' },
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
                label: `${db.databaseId} (${count} results)`,
                value: db.databaseId,
            };
        }),
    ];

    // Build asset type options from aggregations
    const assetTypeOptions = [
        { label: 'All Types', value: 'all' },
        ...(searchResult?.aggregations?.str_assettype?.buckets?.map((bucket: any) => ({
            label: `${bucket.key} (${bucket.doc_count})`,
            value: bucket.key,
        })) || []),
    ];

    // Build tag options from aggregations
    const tagOptions = [
        { label: 'All Tags', value: 'all' },
        ...(searchResult?.aggregations?.list_tags?.buckets?.flatMap((tag: any) =>
            tag.key.split(',').map((value: string) => ({
                label: `${value.trim()} (${tag.doc_count})`,
                value: value.trim(),
            }))
        ) || []),
    ];

    return (
        <ExpandableSection
            headerText="Basic Filters"
            variant="footer"
            defaultExpanded={true}
            headingTagOverride="h5"
        >
            <SpaceBetween direction="vertical" size="m">
                {/* Database Filter */}
                <FormField
                    label="Database"
                    description={databaseLocked ? 'Locked by URL parameter' : undefined}
                >
                    <Select
                        selectedOption={filters.str_databaseid || { label: 'All Databases', value: 'all' }}
                        placeholder="Select database"
                        options={databaseOptions}
                        onChange={({ detail }) =>
                            onFilterChange('str_databaseid', detail.selectedOption.value === 'all' ? null : detail.selectedOption)
                        }
                        disabled={loading || databaseLocked}
                        loadingText="Loading databases..."
                    />
                </FormField>

                {/* Asset Type Filter - Only show for assets */}
                {recordType === 'asset' && (
                    <FormField label="Asset Type">
                        <Select
                            selectedOption={filters.str_assettype || { label: 'All Types', value: 'all' }}
                            placeholder="Select type"
                            options={assetTypeOptions}
                            onChange={({ detail }) =>
                                onFilterChange('str_assettype', detail.selectedOption.value === 'all' ? null : detail.selectedOption)
                            }
                            disabled={loading}
                        />
                    </FormField>
                )}

                {/* File Type Filter - Only show for files */}
                {recordType === 'file' && (
                    <FormField label="File Type">
                        <Select
                            selectedOption={filters.str_fileext || { label: 'All Types', value: 'all' }}
                            placeholder="Select file type"
                            options={[
                                { label: 'All Types', value: 'all' },
                                ...(searchResult?.aggregations?.str_fileext?.buckets?.map((bucket: any) => ({
                                    label: `${bucket.key} (${bucket.doc_count})`,
                                    value: bucket.key,
                                })) || []),
                            ]}
                            onChange={({ detail }) =>
                                onFilterChange('str_fileext', detail.selectedOption.value === 'all' ? null : detail.selectedOption)
                            }
                            disabled={loading}
                        />
                    </FormField>
                )}

                {/* Tags Filter */}
                <FormField label="Tags">
                    <Select
                        selectedOption={filters.list_tags || { label: 'All Tags', value: 'all' }}
                        placeholder="Select tag"
                        options={tagOptions}
                        onChange={({ detail }) =>
                            onFilterChange('list_tags', detail.selectedOption.value === 'all' ? null : detail.selectedOption)
                        }
                        disabled={loading}
                    />
                </FormField>

            </SpaceBetween>
        </ExpandableSection>
    );
};

export default BasicFiltersPanel;
