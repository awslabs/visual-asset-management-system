/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from 'react';
import {
    Box,
    Button,
    Input,
    SpaceBetween,
    Header,
    Badge,
} from '@cloudscape-design/components';

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
    title = 'Search',
    description,
}) => {
    const handleKeyDown = (event: any) => {
        if (event.detail.key === 'Enter') {
            onSearch();
        }
    };

    return (
        <Box padding={{ vertical: 'm', horizontal: 'l' }}>
            <SpaceBetween direction="vertical" size="m">
                {/* Header with actions */}
                <Header
                    variant="h1"
                    description={description}
                    actions={
                        hasActiveFilters && (
                            <Button
                                onClick={onClearAll}
                                disabled={loading}
                            >
                                Clear All Filters
                            </Button>
                        )
                    }
                    info={
                        resultCount !== undefined && (
                            <Badge color="blue">
                                {resultCount.toLocaleString()} results
                            </Badge>
                        )
                    }
                >
                    {title}
                </Header>

                {/* Search input */}
                <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1 }}>
                        <Input
                            placeholder="Search by keywords..."
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
            </SpaceBetween>
        </Box>
    );
};

export default SearchTopBar;
