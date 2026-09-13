/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Box, SpaceBetween, Checkbox } from "@cloudscape-design/components";
import ModeSelector from "./ModeSelector";
import BasicFiltersPanel from "./BasicFiltersPanel";
import AdvancedFiltersPanel from "./AdvancedFiltersPanel";
import MetadataSearchPanel from "./MetadataSearchPanel";
import PreferencesPanel from "./PreferencesPanel";
import GeoFilterPanel from "./GeoFilterPanel";
import ReducedFiltersPanel from "./ReducedFiltersPanel";
import { SearchFilters, MetadataFilter, SearchPreferences, SearchMode } from "../types";

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
    showMapThumbnails?: boolean;
    onMapThumbnailToggle?: () => void;
    useMapView?: boolean;
    isMapView?: boolean;

    /** True when OpenSearch is off: only the mode selector and the natural-language filters render. */
    reduced?: boolean;
    /** The effective search mode; the `Search inside files` checkbox renders only under `nlp`. */
    searchMode?: SearchMode;
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
    showMapThumbnails,
    onMapThumbnailToggle,
    useMapView,
    isMapView = false,
    reduced = false,
    searchMode = "keyword",
}) => {
    const modeSelector = (
        <ModeSelector
            recordType={recordType}
            onRecordTypeChange={onRecordTypeChange}
            showThumbnails={showThumbnails}
            onThumbnailToggle={onThumbnailToggle}
            showMapThumbnails={showMapThumbnails}
            onMapThumbnailToggle={onMapThumbnailToggle}
            useMapView={useMapView}
            disabled={loading}
        />
    );

    // Natural-language mode only: whether segment vectors (video windows, document chunks) join
    // the ranking. Cleared sends `includeSegments: false`; checked leaves the route's default.
    const segmentsCheckbox = searchMode === "nlp" && (
        <Checkbox
            onChange={({ detail }) => onFilterChange("includeSegments", detail.checked)}
            checked={filters.includeSegments !== false}
            disabled={loading}
            description="Also match individual video windows and document chunks"
        >
            Search inside files
        </Checkbox>
    );

    if (reduced) {
        return (
            <Box padding={{ top: "n", bottom: "s", horizontal: "s" }}>
                <SpaceBetween direction="vertical" size="m">
                    {modeSelector}
                    {segmentsCheckbox}
                    <ReducedFiltersPanel
                        filters={filters}
                        onFilterChange={onFilterChange}
                        loading={loading}
                        searchResult={searchResult}
                        databaseLocked={databaseLocked}
                        recordType={recordType}
                    />
                </SpaceBetween>
            </Box>
        );
    }

    return (
        <Box padding={{ top: "n", bottom: "s", horizontal: "s" }}>
            <SpaceBetween direction="vertical" size="m">
                {/* Mode Selector - Prominent at top */}
                {modeSelector}
                {segmentsCheckbox}

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

                {/* Geospatial Filter (after Metadata Search) */}
                <GeoFilterPanel
                    filters={filters}
                    onFilterChange={onFilterChange}
                    disabled={loading}
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
