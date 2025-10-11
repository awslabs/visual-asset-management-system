/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
    ExpandableSection,
    FormField,
    Toggle,
    SpaceBetween,
    Checkbox,
    Box,
    Grid,
    Select,
    DatePicker,
    Input,
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
    // Local state for file filters
    const [dateOperator, setDateOperator] = useState<">" | "<" | "=" | "between">(
        filters.date_lastmodified_filter?.operator || ">"
    );
    const [sizeOperator, setSizeOperator] = useState<">" | "<" | "=" | "between">(
        filters.num_filesize_filter?.operator || ">"
    );
    const [sizeUnit, setSizeUnit] = useState<"bytes" | "KB" | "MB" | "GB">(
        filters.num_filesize_filter?.unit || "MB"
    );

    // Helper function to convert size to bytes
    const convertToBytes = (value: number, unit: "bytes" | "KB" | "MB" | "GB"): number => {
        switch (unit) {
            case "KB":
                return value * 1024;
            case "MB":
                return value * 1024 * 1024;
            case "GB":
                return value * 1024 * 1024 * 1024;
            default:
                return value;
        }
    };

    // Helper function to convert bytes to display unit
    const convertFromBytes = (bytes: number, unit: "bytes" | "KB" | "MB" | "GB"): number => {
        switch (unit) {
            case "KB":
                return bytes / 1024;
            case "MB":
                return bytes / (1024 * 1024);
            case "GB":
                return bytes / (1024 * 1024 * 1024);
            default:
                return bytes;
        }
    };

    const operatorOptions = [
        { label: "Greater than", value: ">" },
        { label: "Less than", value: "<" },
        { label: "Equal to", value: "=" },
        { label: "Between", value: "between" },
    ];

    const sizeUnitOptions = [
        { label: "Bytes", value: "bytes" },
        { label: "KB", value: "KB" },
        { label: "MB", value: "MB" },
        { label: "GB", value: "GB" },
    ];

    return (
        <ExpandableSection
            headerText="Advanced Filters"
            variant="footer"
            defaultExpanded={false}
            headingTagOverride="h5"
        >
            <SpaceBetween direction="vertical" size="m">
                {/* FILE MODE FILTERS - Ordered: Fields, Keyword Search, Archived */}
                {recordType === "file" && (
                    <>
                        {/* Date Modified Filter */}
                        <FormField label="Fields">
                            <SpaceBetween direction="vertical" size="s">
                                <Checkbox
                                    onChange={({ detail }) => {
                                        if (!detail.checked) {
                                            onFilterChange("date_lastmodified_filter", null);
                                        } else {
                                            onFilterChange("date_lastmodified_filter", {
                                                operator: dateOperator,
                                                value: "",
                                            });
                                        }
                                    }}
                                    checked={!!filters.date_lastmodified_filter}
                                    disabled={loading}
                                >
                                    Date modified filter
                                </Checkbox>

                                {filters.date_lastmodified_filter && (
                                    <Grid gridDefinition={[{ colspan: 4 }, { colspan: 8 }]}>
                                        <Select
                                            selectedOption={
                                                operatorOptions.find(
                                                    (opt) => opt.value === dateOperator
                                                ) || operatorOptions[0]
                                            }
                                            onChange={({ detail }) => {
                                                const newOp = detail.selectedOption
                                                    .value as typeof dateOperator;
                                                setDateOperator(newOp);
                                                onFilterChange("date_lastmodified_filter", {
                                                    operator: newOp,
                                                    value: newOp === "between" ? ["", ""] : "",
                                                });
                                            }}
                                            options={operatorOptions}
                                            disabled={loading}
                                        />

                                        {dateOperator === "between" ? (
                                            <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                                                <DatePicker
                                                    onChange={({ detail }) => {
                                                        const currentValue =
                                                            filters.date_lastmodified_filter &&
                                                            Array.isArray(
                                                                filters.date_lastmodified_filter
                                                                    .value
                                                            )
                                                                ? (filters.date_lastmodified_filter
                                                                      .value as string[])
                                                                : ["", ""];
                                                        onFilterChange("date_lastmodified_filter", {
                                                            operator: dateOperator,
                                                            value: [detail.value, currentValue[1]],
                                                        });
                                                    }}
                                                    value={
                                                        Array.isArray(
                                                            filters.date_lastmodified_filter?.value
                                                        )
                                                            ? (
                                                                  filters.date_lastmodified_filter
                                                                      .value as string[]
                                                              )[0]
                                                            : ""
                                                    }
                                                    placeholder="Start date"
                                                    disabled={loading}
                                                />
                                                <DatePicker
                                                    onChange={({ detail }) => {
                                                        const currentValue =
                                                            filters.date_lastmodified_filter &&
                                                            Array.isArray(
                                                                filters.date_lastmodified_filter
                                                                    .value
                                                            )
                                                                ? (filters.date_lastmodified_filter
                                                                      .value as string[])
                                                                : ["", ""];
                                                        onFilterChange("date_lastmodified_filter", {
                                                            operator: dateOperator,
                                                            value: [currentValue[0], detail.value],
                                                        });
                                                    }}
                                                    value={
                                                        Array.isArray(
                                                            filters.date_lastmodified_filter?.value
                                                        )
                                                            ? (
                                                                  filters.date_lastmodified_filter
                                                                      .value as string[]
                                                              )[1]
                                                            : ""
                                                    }
                                                    placeholder="End date"
                                                    disabled={loading}
                                                />
                                            </Grid>
                                        ) : (
                                            <DatePicker
                                                onChange={({ detail }) => {
                                                    onFilterChange("date_lastmodified_filter", {
                                                        operator: dateOperator,
                                                        value: detail.value,
                                                    });
                                                }}
                                                value={
                                                    typeof filters.date_lastmodified_filter
                                                        ?.value === "string"
                                                        ? filters.date_lastmodified_filter.value
                                                        : ""
                                                }
                                                placeholder="Select date"
                                                disabled={loading}
                                            />
                                        )}
                                    </Grid>
                                )}
                                <Checkbox
                                    onChange={({ detail }) => {
                                        if (!detail.checked) {
                                            onFilterChange("num_filesize_filter", null);
                                        } else {
                                            onFilterChange("num_filesize_filter", {
                                                operator: sizeOperator,
                                                value: 0,
                                                unit: sizeUnit,
                                            });
                                        }
                                    }}
                                    checked={!!filters.num_filesize_filter}
                                    disabled={loading}
                                >
                                    File size filter
                                </Checkbox>

                                {filters.num_filesize_filter && (
                                    <Grid
                                        gridDefinition={[
                                            { colspan: 3 },
                                            { colspan: 6 },
                                            { colspan: 3 },
                                        ]}
                                    >
                                        <Select
                                            selectedOption={
                                                operatorOptions.find(
                                                    (opt) => opt.value === sizeOperator
                                                ) || operatorOptions[0]
                                            }
                                            onChange={({ detail }) => {
                                                const newOp = detail.selectedOption
                                                    .value as typeof sizeOperator;
                                                setSizeOperator(newOp);
                                                onFilterChange("num_filesize_filter", {
                                                    operator: newOp,
                                                    value: newOp === "between" ? [0, 0] : 0,
                                                    unit: sizeUnit,
                                                });
                                            }}
                                            options={operatorOptions}
                                            disabled={loading}
                                        />

                                        {sizeOperator === "between" ? (
                                            <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                                                <Input
                                                    onChange={({ detail }) => {
                                                        const numValue =
                                                            parseFloat(detail.value) || 0;
                                                        const currentValue =
                                                            filters.num_filesize_filter &&
                                                            Array.isArray(
                                                                filters.num_filesize_filter.value
                                                            )
                                                                ? (filters.num_filesize_filter
                                                                      .value as number[])
                                                                : [0, 0];
                                                        const bytesValue = [
                                                            convertToBytes(numValue, sizeUnit),
                                                            currentValue[1],
                                                        ];
                                                        onFilterChange("num_filesize_filter", {
                                                            operator: sizeOperator,
                                                            value: bytesValue,
                                                            unit: sizeUnit,
                                                        });
                                                    }}
                                                    value={
                                                        Array.isArray(
                                                            filters.num_filesize_filter?.value
                                                        )
                                                            ? String(
                                                                  convertFromBytes(
                                                                      (
                                                                          filters
                                                                              .num_filesize_filter
                                                                              .value as number[]
                                                                      )[0],
                                                                      sizeUnit
                                                                  )
                                                              )
                                                            : "0"
                                                    }
                                                    type="number"
                                                    placeholder="Min"
                                                    disabled={loading}
                                                />
                                                <Input
                                                    onChange={({ detail }) => {
                                                        const numValue =
                                                            parseFloat(detail.value) || 0;
                                                        const currentValue =
                                                            filters.num_filesize_filter &&
                                                            Array.isArray(
                                                                filters.num_filesize_filter.value
                                                            )
                                                                ? (filters.num_filesize_filter
                                                                      .value as number[])
                                                                : [0, 0];
                                                        const bytesValue = [
                                                            currentValue[0],
                                                            convertToBytes(numValue, sizeUnit),
                                                        ];
                                                        onFilterChange("num_filesize_filter", {
                                                            operator: sizeOperator,
                                                            value: bytesValue,
                                                            unit: sizeUnit,
                                                        });
                                                    }}
                                                    value={
                                                        Array.isArray(
                                                            filters.num_filesize_filter?.value
                                                        )
                                                            ? String(
                                                                  convertFromBytes(
                                                                      (
                                                                          filters
                                                                              .num_filesize_filter
                                                                              .value as number[]
                                                                      )[1],
                                                                      sizeUnit
                                                                  )
                                                              )
                                                            : "0"
                                                    }
                                                    type="number"
                                                    placeholder="Max"
                                                    disabled={loading}
                                                />
                                            </Grid>
                                        ) : (
                                            <Input
                                                onChange={({ detail }) => {
                                                    const numValue = parseFloat(detail.value) || 0;
                                                    const bytesValue = convertToBytes(
                                                        numValue,
                                                        sizeUnit
                                                    );
                                                    onFilterChange("num_filesize_filter", {
                                                        operator: sizeOperator,
                                                        value: bytesValue,
                                                        unit: sizeUnit,
                                                    });
                                                }}
                                                value={
                                                    typeof filters.num_filesize_filter?.value ===
                                                    "number"
                                                        ? String(
                                                              convertFromBytes(
                                                                  filters.num_filesize_filter.value,
                                                                  sizeUnit
                                                              )
                                                          )
                                                        : "0"
                                                }
                                                type="number"
                                                placeholder="Size"
                                                disabled={loading}
                                            />
                                        )}

                                        <Select
                                            selectedOption={
                                                sizeUnitOptions.find(
                                                    (opt) => opt.value === sizeUnit
                                                ) || sizeUnitOptions[2]
                                            }
                                            onChange={({ detail }) => {
                                                const newUnit = detail.selectedOption
                                                    .value as typeof sizeUnit;
                                                setSizeUnit(newUnit);
                                                // Keep the same byte value, just change the display unit
                                                onFilterChange("num_filesize_filter", {
                                                    ...filters.num_filesize_filter,
                                                    unit: newUnit,
                                                });
                                            }}
                                            options={sizeUnitOptions}
                                            disabled={loading}
                                        />
                                    </Grid>
                                )}
                            </SpaceBetween>
                        </FormField>
                    </>
                )}

                {/* ASSET MODE FILTERS - Ordered: Asset Relationships, Keyword Search, Archived */}
                {recordType === "asset" && (
                    <>
                        {/* Asset Relationship Filters */}
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

                {/* COMMON FILTERS - Same order for both modes: Keyword Search, Archived */}
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
            </SpaceBetween>
        </ExpandableSection>
    );
};

export default AdvancedFiltersPanel;
