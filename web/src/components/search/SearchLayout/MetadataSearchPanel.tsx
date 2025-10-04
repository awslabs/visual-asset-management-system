/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
import {
    ExpandableSection,
    SpaceBetween,
    FormField,
    Input,
    Select,
    Button,
    Box,
    Grid,
} from '@cloudscape-design/components';
import { MetadataFilter } from '../types';

interface MetadataSearchPanelProps {
    metadataFilters: MetadataFilter[];
    onAddFilter: () => void;
    onRemoveFilter: (index: number) => void;
    onUpdateFilter: (index: number, filter: MetadataFilter) => void;
    disabled?: boolean;
    metadataSearchMode?: string;
    onSearchModeChange?: (mode: string) => void;
    metadataOperator?: string;
    onOperatorChange?: (operator: string) => void;
    isMapView?: boolean;
}

const MetadataSearchPanel: React.FC<MetadataSearchPanelProps> = ({
    metadataFilters,
    onAddFilter,
    onRemoveFilter,
    onUpdateFilter,
    disabled = false,
    metadataSearchMode = 'both',
    onSearchModeChange,
    metadataOperator = 'OR',
    onOperatorChange,
    isMapView = false,
}) => {
    // Helper function to check if a filter is a location filter (added by map view)
    const isLocationFilter = (filter: MetadataFilter) => {
        return (
            (filter.key === 'location' && (filter.fieldType === 'gp' || filter.fieldType === 'gs')) ||
            (filter.key === 'latitude' && filter.fieldType === 'str') ||
            (filter.key === 'longitude' && filter.fieldType === 'str')
        );
    };
    const searchModeOptions = [
        { label: 'Search Both (Field Names & Values)', value: 'both' },
        { label: 'Search Field Names Only', value: 'key' },
        { label: 'Search Field Values Only', value: 'value' },
    ];

    const operatorOptions = [
        { label: 'AND (All must match)', value: 'AND' },
        { label: 'OR (Any can match)', value: 'OR' },
    ];

    return (
        <ExpandableSection
            headerText="Metadata Search"
            variant="footer"
            defaultExpanded={false}
            headingTagOverride="h5"
        >
            <SpaceBetween direction="vertical" size="m">
                <Box variant="p" color="text-body-secondary">
                    Search by custom metadata fields. Values support * wildcard characters.
                </Box>

                {/* Metadata Mode Selector - Only show when filters exist */}
                {metadataFilters.length > 0 && (
                    <FormField
                        label="Metadata Mode"
                        description={isMapView ? "Locked to 'Search Both' for map view" : "Choose where to search for your metadata terms"}
                    >
                        <Select
                            selectedOption={
                                searchModeOptions.find((opt) => opt.value === metadataSearchMode) ||
                                searchModeOptions[0]
                            }
                            onChange={({ detail }) => {
                                if (onSearchModeChange && !isMapView) {
                                    onSearchModeChange(detail.selectedOption.value || 'both');
                                }
                            }}
                            options={searchModeOptions}
                            disabled={disabled || isMapView}
                        />
                    </FormField>
                )}

                {/* Combine Filters Operator - Only show when multiple filters exist */}
                {metadataFilters.length > 1 && (
                    <FormField
                        label="Combine Filters"
                        description={isMapView ? "Locked to 'OR' for map view" : "Choose how to combine multiple metadata filters"}
                    >
                        <Select
                            selectedOption={
                                operatorOptions.find((opt) => opt.value === metadataOperator) ||
                                operatorOptions[0]
                            }
                            onChange={({ detail }) => {
                                if (onOperatorChange && !isMapView) {
                                    onOperatorChange(detail.selectedOption.value || 'AND');
                                }
                            }}
                            options={operatorOptions}
                            disabled={disabled || isMapView}
                        />
                    </FormField>
                )}

                {/* Metadata Filter Rows */}
                {metadataFilters.map((filter, index) => {
                    const isLocFilter = isLocationFilter(filter);
                    return (
                        <Box key={index} padding={{ bottom: 's' }}>
                            <Grid
                                gridDefinition={[
                                    { colspan: 3 },
                                    { colspan: 2 },
                                    { colspan: 5 },
                                    { colspan: 2 },
                                ]}
                            >
                                <FormField label="Field Name">
                                    <Input
                                        value={filter.key}
                                        onChange={({ detail }) =>
                                            onUpdateFilter(index, { ...filter, key: detail.value })
                                        }
                                        placeholder="e.g., product"
                                        disabled={disabled || metadataSearchMode === 'value' || isLocFilter}
                                    />
                                </FormField>

                                <FormField label="Type">
                                    <Select
                                        selectedOption={{
                                            label: filter.fieldType || 'str',
                                            value: filter.fieldType || 'str',
                                        }}
                                        onChange={({ detail }) =>
                                            onUpdateFilter(index, { ...filter, fieldType: detail.selectedOption.value as any })
                                        }
                                        options={[
                                            { label: 'String', value: 'str' },
                                            { label: 'Number', value: 'num' },
                                            { label: 'Boolean', value: 'bool' },
                                            { label: 'Date', value: 'date' },
                                            { label: 'List', value: 'list' },
                                            { label: 'Geo Point', value: 'gp' },
                                            { label: 'Geo Shape', value: 'gs' },
                                        ]}
                                        disabled={disabled || metadataSearchMode === 'value' || isLocFilter}
                                    />
                                </FormField>

                                <FormField label="Value">
                                    <Input
                                        value={filter.value}
                                        onChange={({ detail }) =>
                                            onUpdateFilter(index, { ...filter, value: detail.value })
                                        }
                                        placeholder="Enter value (use * for wildcard)"
                                        disabled={disabled || metadataSearchMode === 'key' || isLocFilter}
                                    />
                                </FormField>

                                <FormField label="Action">
                                    <Button
                                        onClick={() => onRemoveFilter(index)}
                                        disabled={disabled || isLocFilter}
                                        iconName="remove"
                                    />
                                </FormField>
                            </Grid>
                        </Box>
                    );
                })}

                <Button
                    onClick={onAddFilter}
                    disabled={disabled}
                    iconName="add-plus"
                >
                    Add Metadata Filter
                </Button>
            </SpaceBetween>
        </ExpandableSection>
    );
};

export default MetadataSearchPanel;
