/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    FormField,
    Input,
    Select,
    Button,
    SpaceBetween,
    Box,
    Header,
    Container,
    Grid,
    Icon,
} from "@cloudscape-design/components";
import { MetadataFilter } from "../types";

interface MetadataFiltersProps {
    metadataFilters: MetadataFilter[];
    onAddFilter: (filter: MetadataFilter) => void;
    onRemoveFilter: (index: number) => void;
    onUpdateFilter: (index: number, filter: MetadataFilter) => void;
    disabled?: boolean;
}

const MetadataFilters: React.FC<MetadataFiltersProps> = ({
    metadataFilters,
    onAddFilter,
    onRemoveFilter,
    onUpdateFilter,
    disabled = false,
}) => {
    const [newFilter, setNewFilter] = useState<MetadataFilter>({
        key: "",
        value: "",
        operator: "=",
        type: "string",
    });

    const operatorOptions = [
        { label: "Equals (=)", value: "=" },
        { label: "Not Equals (!=)", value: "!=" },
        { label: "Contains", value: "contains" },
        { label: "Greater Than (>)", value: ">" },
        { label: "Greater Than or Equal (>=)", value: ">=" },
        { label: "Less Than (<)", value: "<" },
        { label: "Less Than or Equal (<=)", value: "<=" },
    ];

    const typeOptions = [
        { label: "Text", value: "string" },
        { label: "Number", value: "number" },
        { label: "Date", value: "date" },
        { label: "Boolean", value: "boolean" },
    ];

    const handleAddFilter = () => {
        if (newFilter.key.trim() && newFilter.value.trim()) {
            onAddFilter({ ...newFilter });
            setNewFilter({
                key: "",
                value: "",
                operator: "=",
                type: "string",
            });
        }
    };

    const handleUpdateFilter = (index: number, field: keyof MetadataFilter, value: any) => {
        const updatedFilter = { ...metadataFilters[index], [field]: value };
        onUpdateFilter(index, updatedFilter);
    };

    const getAvailableOperators = (type: string) => {
        switch (type) {
            case "number":
            case "date":
                return operatorOptions;
            case "boolean":
                return operatorOptions.filter((op) => ["=", "!="].includes(op.value));
            case "string":
            default:
                return operatorOptions.filter((op) => ![">", ">=", "<", "<="].includes(op.value));
        }
    };

    const renderValueInput = (filter: MetadataFilter, index?: number) => {
        const isNewFilter = index === undefined;
        const value = isNewFilter ? newFilter.value : filter.value;
        const type = isNewFilter ? newFilter.type : filter.type;

        const onChange = (newValue: string) => {
            if (isNewFilter) {
                setNewFilter((prev) => ({ ...prev, value: newValue }));
            } else {
                handleUpdateFilter(index!, "value", newValue);
            }
        };

        switch (type) {
            case "boolean":
                return (
                    <Select
                        selectedOption={
                            value ? { label: value === "true" ? "True" : "False", value } : null
                        }
                        options={[
                            { label: "True", value: "true" },
                            { label: "False", value: "false" },
                        ]}
                        onChange={({ detail }) => onChange(detail.selectedOption?.value || "")}
                        placeholder="Select value"
                        disabled={disabled}
                    />
                );
            case "date":
                return (
                    <Input
                        value={value}
                        onChange={(e) => onChange(e.detail.value)}
                        disabled={disabled}
                        placeholder="YYYY-MM-DD"
                    />
                );
            case "number":
                return (
                    <Input
                        type="number"
                        value={value}
                        onChange={(e) => onChange(e.detail.value)}
                        placeholder="Enter number"
                        disabled={disabled}
                    />
                );
            case "string":
            default:
                return (
                    <Input
                        type="text"
                        value={value}
                        onChange={(e) => onChange(e.detail.value)}
                        placeholder="Enter value"
                        disabled={disabled}
                    />
                );
        }
    };

    return (
        <Container
            header={
                <Header
                    variant="h3"
                    description="Filter by custom metadata fields attached to assets and files"
                >
                    Metadata Filters
                </Header>
            }
        >
            <SpaceBetween direction="vertical" size="m">
                {/* Existing Filters */}
                {metadataFilters.map((filter, index) => (
                    <Box key={index} padding="s">
                        <Grid
                            gridDefinition={[
                                { colspan: { default: 3 } },
                                { colspan: { default: 2 } },
                                { colspan: { default: 2 } },
                                { colspan: { default: 4 } },
                                { colspan: { default: 1 } },
                            ]}
                        >
                            <FormField label="Field Name">
                                <Input
                                    value={filter.key}
                                    onChange={(e) =>
                                        handleUpdateFilter(index, "key", e.detail.value)
                                    }
                                    placeholder="metadata key"
                                    disabled={disabled}
                                />
                            </FormField>

                            <FormField label="Type">
                                <Select
                                    selectedOption={
                                        typeOptions.find((opt) => opt.value === filter.type) || null
                                    }
                                    options={typeOptions}
                                    onChange={({ detail }) =>
                                        handleUpdateFilter(
                                            index,
                                            "type",
                                            detail.selectedOption?.value || "string"
                                        )
                                    }
                                    disabled={disabled}
                                />
                            </FormField>

                            <FormField label="Operator">
                                <Select
                                    selectedOption={
                                        getAvailableOperators(filter.type).find(
                                            (opt) => opt.value === filter.operator
                                        ) || null
                                    }
                                    options={getAvailableOperators(filter.type)}
                                    onChange={({ detail }) =>
                                        handleUpdateFilter(
                                            index,
                                            "operator",
                                            detail.selectedOption?.value || "="
                                        )
                                    }
                                    disabled={disabled}
                                />
                            </FormField>

                            <FormField label="Value">{renderValueInput(filter, index)}</FormField>

                            <FormField label=" ">
                                <Button
                                    variant="icon"
                                    iconName="remove"
                                    onClick={() => onRemoveFilter(index)}
                                    disabled={disabled}
                                    ariaLabel="Remove filter"
                                />
                            </FormField>
                        </Grid>
                    </Box>
                ))}

                {/* Add New Filter */}
                <Box padding="s">
                    <SpaceBetween direction="vertical" size="s">
                        <Header variant="h3">Add New Metadata Filter</Header>
                        <Grid
                            gridDefinition={[
                                { colspan: { default: 3 } },
                                { colspan: { default: 2 } },
                                { colspan: { default: 2 } },
                                { colspan: { default: 4 } },
                                { colspan: { default: 1 } },
                            ]}
                        >
                            <FormField label="Field Name">
                                <Input
                                    value={newFilter.key}
                                    onChange={(e) =>
                                        setNewFilter((prev) => ({ ...prev, key: e.detail.value }))
                                    }
                                    placeholder="e.g., author, category"
                                    disabled={disabled}
                                />
                            </FormField>

                            <FormField label="Type">
                                <Select
                                    selectedOption={
                                        typeOptions.find((opt) => opt.value === newFilter.type) ||
                                        null
                                    }
                                    options={typeOptions}
                                    onChange={({ detail }) =>
                                        setNewFilter((prev) => ({
                                            ...prev,
                                            type: (detail.selectedOption?.value as any) || "string",
                                            operator: "=", // Reset operator when type changes
                                        }))
                                    }
                                    disabled={disabled}
                                />
                            </FormField>

                            <FormField label="Operator">
                                <Select
                                    selectedOption={
                                        getAvailableOperators(newFilter.type).find(
                                            (opt) => opt.value === newFilter.operator
                                        ) || null
                                    }
                                    options={getAvailableOperators(newFilter.type)}
                                    onChange={({ detail }) =>
                                        setNewFilter((prev) => ({
                                            ...prev,
                                            operator: (detail.selectedOption?.value as any) || "=",
                                        }))
                                    }
                                    disabled={disabled}
                                />
                            </FormField>

                            <FormField label="Value">{renderValueInput(newFilter)}</FormField>

                            <FormField label=" ">
                                <Button
                                    variant="primary"
                                    iconName="add-plus"
                                    onClick={handleAddFilter}
                                    disabled={
                                        disabled || !newFilter.key.trim() || !newFilter.value.trim()
                                    }
                                    ariaLabel="Add filter"
                                >
                                    Add
                                </Button>
                            </FormField>
                        </Grid>
                    </SpaceBetween>
                </Box>

                {metadataFilters.length === 0 && (
                    <Box textAlign="center" color="text-body-secondary">
                        <Icon name="search" size="big" />
                        <Box variant="p" margin={{ top: "s" }}>
                            No metadata filters added yet. Add filters above to search by custom
                            metadata fields.
                        </Box>
                    </Box>
                )}
            </SpaceBetween>
        </Container>
    );
};

export default MetadataFilters;
