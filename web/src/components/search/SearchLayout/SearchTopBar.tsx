/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Box, Button, Input, SpaceBetween, Header, Badge } from "@cloudscape-design/components";

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
}) => {
    const handleKeyDown = (event: any) => {
        if (event.detail.key === "Enter") {
            onSearch();
        }
    };

    return (
        <Box padding={{ vertical: "m", horizontal: "l" }}>
            {/* Header with inline search input + Search and Clear All Filters buttons in the actions slot */}
            <Header
                variant="h1"
                description={description}
                actions={
                    <SpaceBetween direction="horizontal" size="xs">
                        <div style={{ width: "320px" }}>
                            <Input
                                placeholder="Search by keywords (wildcard)..."
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
