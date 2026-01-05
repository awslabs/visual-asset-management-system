/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Input,
    FormField,
    Select,
    SpaceBetween,
    Button,
    Icon,
} from "@cloudscape-design/components";
import { MetadataValueType } from "./types/metadata.types";
import { getAvailableValueTypes } from "./utils/metadataHelpers";

interface MetadataSearchFilterProps {
    onSearchChange: (searchTerm: string) => void;
    onTypeFilter: (type: MetadataValueType | null) => void;
    onSchemaFilter: (schemaOnly: boolean) => void;
    totalRows: number;
    filteredRows: number;
}

export const MetadataSearchFilter: React.FC<MetadataSearchFilterProps> = ({
    onSearchChange,
    onTypeFilter,
    onSchemaFilter,
    totalRows,
    filteredRows,
}) => {
    const [searchTerm, setSearchTerm] = useState("");
    const [selectedType, setSelectedType] = useState<MetadataValueType | null>(null);
    const [schemaOnly, setSchemaOnly] = useState(false);

    // Debounce search input
    useEffect(() => {
        const timeoutId = setTimeout(() => {
            onSearchChange(searchTerm);
        }, 300);

        return () => clearTimeout(timeoutId);
    }, [searchTerm, onSearchChange]);

    const handleClearFilters = () => {
        setSearchTerm("");
        setSelectedType(null);
        setSchemaOnly(false);
        onSearchChange("");
        onTypeFilter(null);
        onSchemaFilter(false);
    };

    const hasActiveFilters = searchTerm !== "" || selectedType !== null || schemaOnly;

    const typeOptions = [{ label: "All Types", value: "all" }, ...getAvailableValueTypes(false)];

    return (
        <div style={{ padding: "12px", background: "#f9f9f9", borderRadius: "8px" }}>
            <SpaceBetween direction="vertical" size="s">
                <div
                    style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}
                >
                    <div style={{ flex: "1 1 300px", minWidth: "200px" }}>
                        <Input
                            value={searchTerm}
                            onChange={({ detail }) => setSearchTerm(detail.value)}
                            placeholder="Search by key or value..."
                            type="search"
                            clearAriaLabel="Clear search"
                            ariaLabel="Search metadata"
                        />
                    </div>

                    <div style={{ flex: "0 1 200px", minWidth: "150px" }}>
                        <Select
                            selectedOption={
                                selectedType
                                    ? { label: selectedType, value: selectedType }
                                    : { label: "All Types", value: "all" }
                            }
                            onChange={({ detail }) => {
                                const value = detail.selectedOption.value;
                                const type = value === "all" ? null : (value as MetadataValueType);
                                setSelectedType(type);
                                onTypeFilter(type);
                            }}
                            options={typeOptions}
                            ariaLabel="Filter by metadata type"
                            expandToViewport={true}
                        />
                    </div>

                    <div style={{ flex: "0 0 auto" }}>
                        <Button
                            variant={schemaOnly ? "primary" : "normal"}
                            onClick={() => {
                                const newValue = !schemaOnly;
                                setSchemaOnly(newValue);
                                onSchemaFilter(newValue);
                            }}
                            iconName="filter"
                            ariaLabel="Filter schema fields only"
                        >
                            Schema Only
                        </Button>
                    </div>

                    {hasActiveFilters && (
                        <div style={{ flex: "0 0 auto" }}>
                            <Button
                                onClick={handleClearFilters}
                                iconName="close"
                                ariaLabel="Clear all filters"
                            >
                                Clear Filters
                            </Button>
                        </div>
                    )}
                </div>

                {hasActiveFilters && (
                    <div style={{ fontSize: "12px", color: "#666" }}>
                        <Icon name="status-info" variant="subtle" /> Showing {filteredRows} of{" "}
                        {totalRows} metadata records
                    </div>
                )}
            </SpaceBetween>
        </div>
    );
};

export default MetadataSearchFilter;
