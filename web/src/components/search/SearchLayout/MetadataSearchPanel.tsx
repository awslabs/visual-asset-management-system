/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    ExpandableSection,
    SpaceBetween,
    FormField,
    Input,
    Select,
    Button,
    Box,
    Grid,
} from "@cloudscape-design/components";
import { MetadataFilter } from "../types";
import { searchRunningReason } from "./searchRunning";

/**
 * The name of a filter row's icon-only remove button: the filter's field name, else its value, else
 * the row's position, so each row's button says which filter it removes.
 */
const removeFilterLabel = (filter: MetadataFilter, index: number) =>
    filter.key
        ? `Remove metadata filter ${filter.key}`
        : filter.value
        ? `Remove metadata filter with value ${filter.value}`
        : `Remove metadata filter ${index + 1}`;

interface MetadataSearchPanelProps {
    metadataFilters: MetadataFilter[];
    onAddFilter: () => void;
    onRemoveFilter: (index: number) => void;
    onUpdateFilter: (index: number, filter: MetadataFilter) => void;
    /** True while a search runs: the controls keep focus and ignore input (see searchRunning). */
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
    metadataSearchMode = "both",
    onSearchModeChange,
    metadataOperator = "OR",
    onOperatorChange,
    isMapView = false,
}) => {
    // Helper function to check if a filter is a location filter (added by map view)
    const isLocationFilter = (filter: MetadataFilter) => {
        const keyLower = filter.key.toLowerCase();
        return keyLower === "location" || keyLower === "latitude" || keyLower === "longitude";
    };
    const searchModeOptions = [
        { label: "Search Both (Field Names & Values)", value: "both" },
        { label: "Search Field Names Only", value: "key" },
        { label: "Search Field Values Only", value: "value" },
    ];

    const operatorOptions = [
        { label: "AND (All must match)", value: "AND" },
        { label: "OR (Any can match)", value: "OR" },
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
                        description={
                            isMapView
                                ? "Locked to 'Search Both' for map view"
                                : "Choose where to search for your metadata terms"
                        }
                    >
                        <Select
                            selectedOption={
                                searchModeOptions.find((opt) => opt.value === metadataSearchMode) ||
                                searchModeOptions[0]
                            }
                            onChange={({ detail }) => {
                                if (onSearchModeChange && !isMapView) {
                                    onSearchModeChange(detail.selectedOption.value || "both");
                                }
                            }}
                            options={searchModeOptions}
                            disabled={isMapView}
                            readOnly={disabled}
                        />
                    </FormField>
                )}

                {/* Combine Filters Operator - Only show when multiple filters exist */}
                {metadataFilters.length > 1 && (
                    <FormField
                        label="Combine Filters"
                        description={
                            isMapView
                                ? "Locked to 'OR' for map view"
                                : "Choose how to combine multiple metadata filters"
                        }
                    >
                        <Select
                            selectedOption={
                                operatorOptions.find((opt) => opt.value === metadataOperator) ||
                                operatorOptions[0]
                            }
                            onChange={({ detail }) => {
                                if (onOperatorChange && !isMapView) {
                                    onOperatorChange(detail.selectedOption.value || "AND");
                                }
                            }}
                            options={operatorOptions}
                            disabled={isMapView}
                            readOnly={disabled}
                        />
                    </FormField>
                )}

                {/* Metadata Filter Rows */}
                {metadataFilters.map((filter, index) => {
                    const isLocFilter = isLocationFilter(filter);
                    return (
                        <Box key={index} padding={{ bottom: "s" }}>
                            <Grid gridDefinition={[{ colspan: 5 }, { colspan: 5 }, { colspan: 2 }]}>
                                <FormField label="Field Name">
                                    <Input
                                        value={filter.key}
                                        onChange={({ detail }) =>
                                            onUpdateFilter(index, { ...filter, key: detail.value })
                                        }
                                        placeholder="e.g., product"
                                        disabled={metadataSearchMode === "value" || isLocFilter}
                                        readOnly={disabled}
                                    />
                                </FormField>

                                <FormField label="Value">
                                    <Input
                                        value={filter.value}
                                        onChange={({ detail }) =>
                                            onUpdateFilter(index, {
                                                ...filter,
                                                value: detail.value,
                                            })
                                        }
                                        placeholder="Enter value (use * for wildcard)"
                                        disabled={metadataSearchMode === "key" || isLocFilter}
                                        readOnly={disabled}
                                    />
                                </FormField>

                                <FormField label="Action">
                                    <Button
                                        ariaLabel={removeFilterLabel(filter, index)}
                                        onClick={() => onRemoveFilter(index)}
                                        disabled={disabled || isLocFilter}
                                        disabledReason={searchRunningReason(
                                            disabled && !isLocFilter
                                        )}
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
                    disabledReason={searchRunningReason(disabled)}
                    iconName="add-plus"
                >
                    Add Metadata Filter
                </Button>
            </SpaceBetween>
        </ExpandableSection>
    );
};

export default MetadataSearchPanel;
