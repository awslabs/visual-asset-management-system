/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Grid from "@cloudscape-design/components/grid";
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
}) => {
    // Every row is the user's own, a location field included: the Map view adds its location scope
    // to the request, not as rows here, and changes neither the mode nor the operator.
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
                        description="Choose where to search for your metadata terms"
                    >
                        <Select
                            selectedOption={
                                searchModeOptions.find((opt) => opt.value === metadataSearchMode) ||
                                searchModeOptions[0]
                            }
                            onChange={({ detail }) => {
                                if (onSearchModeChange) {
                                    onSearchModeChange(detail.selectedOption.value || "both");
                                }
                            }}
                            options={searchModeOptions}
                            readOnly={disabled}
                        />
                    </FormField>
                )}

                {/* Combine Filters Operator - Only show when multiple filters exist */}
                {metadataFilters.length > 1 && (
                    <FormField
                        label="Combine Filters"
                        description="Choose how to combine multiple metadata filters"
                    >
                        <Select
                            selectedOption={
                                operatorOptions.find((opt) => opt.value === metadataOperator) ||
                                operatorOptions[0]
                            }
                            onChange={({ detail }) => {
                                if (onOperatorChange) {
                                    onOperatorChange(detail.selectedOption.value || "AND");
                                }
                            }}
                            options={operatorOptions}
                            readOnly={disabled}
                        />
                    </FormField>
                )}

                {/* Metadata Filter Rows */}
                {metadataFilters.map((filter, index) => (
                    <Box key={index} padding={{ bottom: "s" }}>
                        <Grid gridDefinition={[{ colspan: 5 }, { colspan: 5 }, { colspan: 2 }]}>
                            <FormField label="Field Name">
                                <Input
                                    value={filter.key}
                                    onChange={({ detail }) =>
                                        onUpdateFilter(index, { ...filter, key: detail.value })
                                    }
                                    placeholder="e.g., product"
                                    disabled={metadataSearchMode === "value"}
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
                                    disabled={metadataSearchMode === "key"}
                                    readOnly={disabled}
                                />
                            </FormField>

                            <FormField label="Action">
                                <Button
                                    ariaLabel={removeFilterLabel(filter, index)}
                                    onClick={() => onRemoveFilter(index)}
                                    disabled={disabled}
                                    disabledReason={searchRunningReason(disabled)}
                                    iconName="remove"
                                />
                            </FormField>
                        </Grid>
                    </Box>
                ))}

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
