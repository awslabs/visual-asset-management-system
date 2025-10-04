/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
    ExpandableSection,
    SpaceBetween,
    FormField,
    Select,
    Multiselect,
    Button,
    Box,
    Toggle,
} from "@cloudscape-design/components";
import { SearchPreferences, FIELD_MAPPINGS, SearchFilters } from "../types";

interface PreferencesPanelProps {
    preferences: SearchPreferences;
    onPreferencesChange: (prefs: Partial<SearchPreferences>) => void;
    recordType: "asset" | "file";
    disabled?: boolean;
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;
}

const PreferencesPanel: React.FC<PreferencesPanelProps> = ({
    preferences,
    onPreferencesChange,
    recordType,
    disabled = false,
    filters,
    onFilterChange,
}) => {
    const pageSizeOptions = [
        { label: "25 per page", value: "25" },
        { label: "50 per page", value: "50" },
        { label: "100 per page", value: "100" },
        { label: "200 per page", value: "200" },
    ];

    // Define asset-specific and file-specific columns (all available columns from API)
    const assetSpecificColumns = [
        "str_assetname",
        "str_assetid",
        "str_assettype",
        "str_description",
        "str_bucketid",
        "str_bucketname",
        "str_bucketprefix",
        "str_asset_version_id",
        "date_asset_version_createdate",
        "str_asset_version_comment",
        "bool_isdistributable",
        "bool_has_asset_children",
        "bool_has_asset_parents",
        "bool_has_assets_related",
    ];

    const fileSpecificColumns = [
        "str_key",
        "str_assetname",
        "str_bucketid",
        "str_bucketname",
        "str_bucketprefix",
        "str_fileext",
        "num_filesize",
        "date_lastmodified",
        "str_etag",
        "str_s3_version_id",
    ];

    const commonColumns = [
        "str_databaseid",
        "list_tags",
        "bool_archived",
        "date_created",
        "str_createdby",
        "metadata",
    ];

    // Build column options filtered by record type
    const columnOptions = Object.entries(FIELD_MAPPINGS)
        .filter(([key]) => {
            // Exclude MD_* pattern and rectype, but keep 'metadata' column
            if (key.startsWith("MD_") && key !== "metadata") return false;
            if (key === "_rectype") return false;

            // Filter by record type
            if (recordType === "asset") {
                return assetSpecificColumns.includes(key) || commonColumns.includes(key);
            } else {
                return fileSpecificColumns.includes(key) || commonColumns.includes(key);
            }
        })
        .map(([key, config]) => ({
            label: config.label,
            value: key,
        }));

    // Get the appropriate column list based on record type
    const currentColumns =
        recordType === "asset" ? preferences.assetTableColumns : preferences.fileTableColumns;

    const selectedColumns = (currentColumns || []).map((col) => {
        const mapping = FIELD_MAPPINGS[col];
        return {
            label: mapping?.label || col,
            value: col,
        };
    });

    const cardSizeOptions = [
        { label: "Small Cards", value: "small" },
        { label: "Medium Cards", value: "medium" },
        { label: "Large Cards", value: "large" },
    ];

    const handleResetPreferences = () => {
        // Use the same defaults as defined in types.ts DEFAULT_PREFERENCES
        const defaultAssetColumns = [
            "str_assetname",
            "str_databaseid",
            "str_assettype",
            "str_description",
            "str_asset_version_id",
            "list_tags",
            "metadata",
        ];
        const defaultFileColumns = [
            "str_key",
            "str_assetname",
            "str_databaseid",
            "str_fileext",
            "num_filesize",
            "date_lastmodified",
            "list_tags",
            "metadata",
        ];

        onPreferencesChange({
            pageSize: 50, // Match DEFAULT_PREFERENCES in types.ts
            assetTableColumns: defaultAssetColumns,
            fileTableColumns: defaultFileColumns,
            cardSize: "medium",
            sortField: "_score",
            sortDirection: "desc",
            viewMode: "table",
        });
    };

    return (
        <ExpandableSection
            headerText="Display & Preferences"
            variant="footer"
            defaultExpanded={false}
            headingTagOverride="h5"
        >
            <SpaceBetween direction="vertical" size="m">
                {/* Page Size */}
                <FormField label="Page Size" description="Number of results per page">
                    <Select
                        selectedOption={
                            pageSizeOptions.find(
                                (opt) => opt.value === String(preferences.pageSize)
                            ) || pageSizeOptions[2]
                        }
                        onChange={({ detail }) =>
                            onPreferencesChange({
                                pageSize: parseInt(detail.selectedOption.value || "100"),
                            })
                        }
                        options={pageSizeOptions}
                        disabled={disabled}
                    />
                </FormField>

                {/* Table Columns */}
                <FormField
                    label="Table Columns"
                    description="Select columns to display in table view"
                >
                    <Multiselect
                        selectedOptions={selectedColumns}
                        onChange={({ detail }) => {
                            const newColumns = detail.selectedOptions
                                .map((opt) => opt.value)
                                .filter((val): val is string => val !== undefined);

                            // Update the appropriate column list based on record type
                            if (recordType === "asset") {
                                onPreferencesChange({ assetTableColumns: newColumns });
                            } else {
                                onPreferencesChange({ fileTableColumns: newColumns });
                            }
                        }}
                        options={columnOptions}
                        placeholder="Select columns"
                        disabled={disabled}
                    />
                </FormField>

                {/* Show Result Explanation Toggle */}
                <FormField
                    label="Search Insights"
                    description="Show detailed information about why each result matched"
                >
                    <Toggle
                        onChange={({ detail }) =>
                            onFilterChange("showResultExplanation", detail.checked)
                        }
                        checked={filters.showResultExplanation || false}
                        disabled={disabled}
                    >
                        Show result explanation
                    </Toggle>
                </FormField>

                {/* Reset Button */}
                <Box textAlign="center">
                    <Button onClick={handleResetPreferences} disabled={disabled} iconName="refresh">
                        Reset to Defaults
                    </Button>
                </Box>
            </SpaceBetween>
        </ExpandableSection>
    );
};

export default PreferencesPanel;
