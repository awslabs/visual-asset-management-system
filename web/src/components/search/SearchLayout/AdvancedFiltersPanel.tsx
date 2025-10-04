/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
    ExpandableSection,
    FormField,
    Toggle,
    SpaceBetween,
    Checkbox,
    Box,
    Grid,
    Select,
} from "@cloudscape-design/components";
import { SearchFilters } from "../types";

interface AdvancedFiltersPanelProps {
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;
    loading?: boolean;
    recordType: "asset" | "file";
}

const AdvancedFiltersPanel: React.FC<AdvancedFiltersPanelProps> = ({
    filters,
    onFilterChange,
    loading = false,
    recordType,
}) => {
    return (
        <ExpandableSection
            headerText="Advanced Filters"
            variant="footer"
            defaultExpanded={false}
            headingTagOverride="h5"
        >
            <SpaceBetween direction="vertical" size="m">
                {/* Include Archived Toggle */}
                <FormField label="Archived Items">
                    <Toggle
                        onChange={({ detail }) =>
                            onFilterChange("bool_archived", detail.checked ? { value: true } : null)
                        }
                        checked={!!filters.bool_archived}
                        disabled={loading}
                    >
                        Include archived items
                    </Toggle>
                </FormField>

                {/* Include Metadata in Keyword Search Toggle */}
                <FormField label="Keyword Search Scope">
                    <Toggle
                        onChange={({ detail }) =>
                            onFilterChange("includeMetadataInKeywordSearch", detail.checked)
                        }
                        checked={filters.includeMetadataInKeywordSearch !== false}
                        disabled={loading}
                    >
                        Include metadata field data in keyword search
                    </Toggle>
                </FormField>

                {/* Asset Relationship Filters - Only show for assets */}
                {recordType === "asset" && (
                    <>
                        <FormField
                            label="Asset Relationships"
                            description="Filter assets based on their relationships with other assets"
                        >
                            <SpaceBetween direction="vertical" size="s">
                                {/* Has Child Assets */}
                                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                                    <Checkbox
                                        onChange={({ detail }) => {
                                            if (!detail.checked) {
                                                onFilterChange("bool_has_asset_children", null);
                                            } else if (!filters.bool_has_asset_children) {
                                                onFilterChange("bool_has_asset_children", {
                                                    value: true,
                                                });
                                            }
                                        }}
                                        checked={!!filters.bool_has_asset_children}
                                        disabled={loading}
                                    >
                                        Has child assets
                                    </Checkbox>
                                    {filters.bool_has_asset_children && (
                                        <Select
                                            selectedOption={
                                                filters.bool_has_asset_children.value === false
                                                    ? { label: "False", value: "false" }
                                                    : { label: "True", value: "true" }
                                            }
                                            onChange={({ detail }) =>
                                                onFilterChange("bool_has_asset_children", {
                                                    value: detail.selectedOption.value === "true",
                                                })
                                            }
                                            options={[
                                                { label: "True", value: "true" },
                                                { label: "False", value: "false" },
                                            ]}
                                            disabled={loading}
                                        />
                                    )}
                                </Grid>

                                {/* Has Parent Assets */}
                                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                                    <Checkbox
                                        onChange={({ detail }) => {
                                            if (!detail.checked) {
                                                onFilterChange("bool_has_asset_parents", null);
                                            } else if (!filters.bool_has_asset_parents) {
                                                onFilterChange("bool_has_asset_parents", {
                                                    value: true,
                                                });
                                            }
                                        }}
                                        checked={!!filters.bool_has_asset_parents}
                                        disabled={loading}
                                    >
                                        Has parent assets
                                    </Checkbox>
                                    {filters.bool_has_asset_parents && (
                                        <Select
                                            selectedOption={
                                                filters.bool_has_asset_parents.value === false
                                                    ? { label: "False", value: "false" }
                                                    : { label: "True", value: "true" }
                                            }
                                            onChange={({ detail }) =>
                                                onFilterChange("bool_has_asset_parents", {
                                                    value: detail.selectedOption.value === "true",
                                                })
                                            }
                                            options={[
                                                { label: "True", value: "true" },
                                                { label: "False", value: "false" },
                                            ]}
                                            disabled={loading}
                                        />
                                    )}
                                </Grid>

                                {/* Has Related Assets */}
                                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                                    <Checkbox
                                        onChange={({ detail }) => {
                                            if (!detail.checked) {
                                                onFilterChange("bool_has_assets_related", null);
                                            } else if (!filters.bool_has_assets_related) {
                                                onFilterChange("bool_has_assets_related", {
                                                    value: true,
                                                });
                                            }
                                        }}
                                        checked={!!filters.bool_has_assets_related}
                                        disabled={loading}
                                    >
                                        Has related assets
                                    </Checkbox>
                                    {filters.bool_has_assets_related && (
                                        <Select
                                            selectedOption={
                                                filters.bool_has_assets_related.value === false
                                                    ? { label: "False", value: "false" }
                                                    : { label: "True", value: "true" }
                                            }
                                            onChange={({ detail }) =>
                                                onFilterChange("bool_has_assets_related", {
                                                    value: detail.selectedOption.value === "true",
                                                })
                                            }
                                            options={[
                                                { label: "True", value: "true" },
                                                { label: "False", value: "false" },
                                            ]}
                                            disabled={loading}
                                        />
                                    )}
                                </Grid>
                            </SpaceBetween>
                        </FormField>
                    </>
                )}
            </SpaceBetween>
        </ExpandableSection>
    );
};

export default AdvancedFiltersPanel;
