/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Box, SpaceBetween, Container } from "@cloudscape-design/components";
import ModeSelector from "./ModeSelector";
import BasicFiltersPanel from "./BasicFiltersPanel";
import AdvancedFiltersPanel from "./AdvancedFiltersPanel";
import MetadataSearchPanel from "./MetadataSearchPanel";
import PreferencesPanel from "./PreferencesPanel";
import { SearchFilters, MetadataFilter, SearchPreferences } from "../types";

interface SearchSidebarProps {
    // Mode
    recordType: "asset" | "file";
    onRecordTypeChange: (type: "asset" | "file") => void;

    // Filters
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;

    // Metadata
    metadataFilters: MetadataFilter[];
    onAddMetadataFilter: () => void;
    onRemoveMetadataFilter: (index: number) => void;
    onUpdateMetadataFilter: (index: number, filter: MetadataFilter) => void;
    metadataSearchMode?: string;
    onMetadataSearchModeChange?: (mode: string) => void;
    metadataOperator?: string;
    onMetadataOperatorChange?: (operator: string) => void;

    // Preferences
    preferences: SearchPreferences;
    onPreferencesChange: (prefs: Partial<SearchPreferences>) => void;

    // State
    loading?: boolean;
    searchResult?: any;
    databaseLocked?: boolean;

    // Display
    showThumbnails: boolean;
    onThumbnailToggle: () => void;
    isMapView?: boolean;
}

const SearchSidebar: React.FC<SearchSidebarProps> = ({
    recordType,
    onRecordTypeChange,
    filters,
    onFilterChange,
    metadataFilters,
    onAddMetadataFilter,
    onRemoveMetadataFilter,
    onUpdateMetadataFilter,
    metadataSearchMode = "both",
    onMetadataSearchModeChange,
    metadataOperator = "AND",
    onMetadataOperatorChange,
    preferences,
    onPreferencesChange,
    loading = false,
    searchResult,
    databaseLocked = false,
    showThumbnails,
    onThumbnailToggle,
    isMapView = false,
}) => {
    return (
        <Box padding={{ vertical: "s", horizontal: "s" }}>
            <SpaceBetween direction="vertical" size="m">
                {/* Mode Selector - Prominent at top */}
                <ModeSelector
                    recordType={recordType}
                    onRecordTypeChange={onRecordTypeChange}
                    showThumbnails={showThumbnails}
                    onThumbnailToggle={onThumbnailToggle}
                    disabled={loading}
                />

                {/* Basic Filters */}
                <BasicFiltersPanel
                    filters={filters}
                    onFilterChange={onFilterChange}
                    loading={loading}
                    searchResult={searchResult}
                    databaseLocked={databaseLocked}
                    recordType={recordType}
                />

                {/* Advanced Filters */}
                <AdvancedFiltersPanel
                    filters={filters}
                    onFilterChange={onFilterChange}
                    loading={loading}
                    recordType={recordType}
                />

                {/* Metadata Search */}
                <MetadataSearchPanel
                    metadataFilters={metadataFilters}
                    onAddFilter={onAddMetadataFilter}
                    onRemoveFilter={onRemoveMetadataFilter}
                    onUpdateFilter={onUpdateMetadataFilter}
                    metadataSearchMode={metadataSearchMode}
                    onSearchModeChange={onMetadataSearchModeChange}
                    metadataOperator={metadataOperator}
                    onOperatorChange={onMetadataOperatorChange}
                    disabled={loading}
                    isMapView={isMapView}
                />

                {/* Display & Preferences (combined) */}
                <PreferencesPanel
                    preferences={preferences}
                    onPreferencesChange={onPreferencesChange}
                    recordType={recordType}
                    disabled={loading}
                    filters={filters}
                    onFilterChange={onFilterChange}
                />
            </SpaceBetween>
        </Box>
    );
};

export default SearchSidebar;
