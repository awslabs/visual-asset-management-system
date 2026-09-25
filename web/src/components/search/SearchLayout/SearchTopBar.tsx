/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import SpaceBetween from "@cloudscape-design/components/space-between";
import type { SearchMode } from "../types";
import Synonyms from "../../../synonyms";

/**
 * The keyword / natural-language switch. Present only when both engines are enabled; a
 * single-engine deployment has nothing to choose, so no switch is rendered.
 */
export interface SearchModeControl {
    mode: SearchMode;
    onChange: (mode: SearchMode) => void;
}

interface SearchTopBarProps {
    query: string;
    onQueryChange: (query: string) => void;
    onSearch: () => void;
    onClearAll: () => void;
    loading?: boolean;
    /** The count of the last search that ran; omitted (no badge) until one has. */
    resultCount?: number;
    hasActiveFilters?: boolean;
    title?: string;
    description?: string;
    /** The mode the query is interpreted in; selects the query box placeholder and the mode line. */
    searchMode?: SearchMode;
    searchModeControl?: SearchModeControl;
}

/** What each mode does, stated where the query is typed so the two engines' different defaults read as intended. */
export function searchModeDescription(mode: SearchMode): string {
    if (mode === "nlp") {
        return (
            "Natural-language search matches on meaning across file contents, including image and " +
            `video scenes, as well as ${Synonyms.asset} and file names, descriptions, tags and metadata. ` +
            "Describe what you are looking for to see results."
        );
    }
    return (
        `Keyword search matches ${Synonyms.asset} and file names, descriptions, tags and metadata. ` +
        "With an empty query it lists everything in scope; filters narrow the list."
    );
}

const SearchTopBar: React.FC<SearchTopBarProps> = ({
    query,
    onQueryChange,
    onSearch,
    onClearAll,
    loading = false,
    resultCount,
    hasActiveFilters = false,
    title = "Search",
    description,
    searchMode = "keyword",
    searchModeControl,
}) => {
    const handleKeyDown = (event: any) => {
        if (event.detail.key === "Enter") {
            onSearch();
        }
    };

    const placeholder =
        searchMode === "nlp" ? "Describe what you are looking for..." : "Search by keywords...";

    return (
        <Box padding={{ vertical: "m", horizontal: "l" }}>
            <SpaceBetween direction="vertical" size="s">
                {/* Page header: title, the last search's count, and the one secondary action */}
                <Header
                    variant="h1"
                    description={description}
                    actions={
                        hasActiveFilters && (
                            <Button onClick={onClearAll} disabled={loading}>
                                Clear all filters
                            </Button>
                        )
                    }
                    info={
                        resultCount !== undefined && (
                            <Badge color="blue">{resultCount.toLocaleString()} results</Badge>
                        )
                    }
                >
                    {title}
                </Header>

                {/* Query band: mode switch (both engines only), full-width query box, mode line */}
                <div className="search-query-band">
                    <SpaceBetween direction="vertical" size="xs">
                        {searchModeControl && (
                            <SegmentedControl
                                label="Search mode"
                                selectedId={searchModeControl.mode}
                                onChange={({ detail }) =>
                                    searchModeControl.onChange(detail.selectedId as SearchMode)
                                }
                                options={[
                                    { text: "Keyword", id: "keyword" },
                                    { text: "Natural language", id: "nlp" },
                                ]}
                            />
                        )}
                        <div style={{ display: "flex", gap: "8px", alignItems: "flex-start" }}>
                            <div style={{ flex: "1 1 auto", minWidth: 0 }}>
                                <Input
                                    placeholder={placeholder}
                                    ariaLabel={
                                        searchMode === "nlp"
                                            ? "Natural-language search query"
                                            : "Keyword search query"
                                    }
                                    type="search"
                                    value={query}
                                    onChange={(e) => onQueryChange(e.detail.value)}
                                    onKeyDown={handleKeyDown}
                                    disabled={loading}
                                    clearAriaLabel="Clear search"
                                />
                            </div>
                            <Button
                                variant="primary"
                                onClick={onSearch}
                                loading={loading}
                                iconName="search"
                            >
                                Search
                            </Button>
                        </div>
                        <Box color="text-body-secondary" fontSize="body-s">
                            {searchModeDescription(searchMode)}
                        </Box>
                    </SpaceBetween>
                </div>
            </SpaceBetween>
        </Box>
    );
};

export default SearchTopBar;
