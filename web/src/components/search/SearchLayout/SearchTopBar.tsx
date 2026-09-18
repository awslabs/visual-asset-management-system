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
    resultCount?: number;
    hasActiveFilters?: boolean;
    title?: string;
    description?: string;
    /** The mode the query is interpreted in; selects the query box placeholder. */
    searchMode?: SearchMode;
    searchModeControl?: SearchModeControl;
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
            {/* Header with the mode switch, inline search input, Search and Clear All Filters in the actions slot */}
            <Header
                variant="h1"
                description={description}
                actions={
                    <SpaceBetween direction="horizontal" size="xs">
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
                        <div style={{ width: "320px" }}>
                            <Input
                                placeholder={placeholder}
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
                        {hasActiveFilters && (
                            <Button onClick={onClearAll} disabled={loading}>
                                Clear All Filters
                            </Button>
                        )}
                    </SpaceBetween>
                }
                info={
                    resultCount !== undefined && (
                        <Badge color="blue">{resultCount.toLocaleString()} results</Badge>
                    )
                }
            >
                {title}
            </Header>
        </Box>
    );
};

export default SearchTopBar;
