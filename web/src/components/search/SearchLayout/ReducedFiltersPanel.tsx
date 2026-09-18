/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useState } from "react";
import Box from "@cloudscape-design/components/box";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import FormField from "@cloudscape-design/components/form-field";
import Multiselect from "@cloudscape-design/components/multiselect";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Toggle from "@cloudscape-design/components/toggle";
import { SearchFilters, SearchResponse, SearchResult } from "../types";
import { fetchAllDatabases } from "../../../services/APIService";
import Synonyms from "../../../synonyms";

/**
 * The filters natural-language search can honour without OpenSearch: which databases to search
 * (`databaseIds`), which file extensions to keep (`fileExtensions`), and whether archived versions
 * count. Everything OpenSearch-only (metadata, geo, relationships, dates, sizes) is absent; the
 * mode-level `Search inside files` checkbox is `SearchSidebar`'s.
 */

interface Option {
    label: string;
    value: string;
}

/** One option per extension in the current hits, counted, with the vector file class when known. */
export function fileTypeOptionsFromHits(hits: SearchResult[]): Option[] {
    const counts = new Map<string, { count: number; fileClass?: string }>();
    hits.forEach((hit) => {
        const ext = hit._source?.str_fileext;
        if (typeof ext !== "string" || ext === "") return;
        const entry = counts.get(ext) || { count: 0, fileClass: undefined };
        entry.count += 1;
        if (!entry.fileClass && hit._vector?.fileClass) entry.fileClass = hit._vector.fileClass;
        counts.set(ext, entry);
    });
    return Array.from(counts.entries())
        .sort(([a], [b]) => a.localeCompare(b, undefined, { sensitivity: "base" }))
        .map(([ext, { count, fileClass }]) => ({
            label: fileClass ? `${ext} — ${fileClass} (${count})` : `${ext} (${count})`,
            value: ext,
        }));
}

interface ReducedFiltersPanelProps {
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;
    loading?: boolean;
    searchResult?: SearchResponse | null;
    databaseLocked?: boolean;
    recordType: "asset" | "file";
}

const ReducedFiltersPanel: React.FC<ReducedFiltersPanelProps> = ({
    filters,
    onFilterChange,
    loading = false,
    searchResult,
    databaseLocked = false,
    recordType,
}) => {
    const [databases, setDatabases] = useState<Option[]>([]);

    useEffect(() => {
        fetchAllDatabases().then((res) => {
            if (res && Array.isArray(res)) {
                setDatabases(
                    res
                        .map((db: any) => ({ label: db.databaseId, value: db.databaseId }))
                        .sort((a, b) =>
                            a.label.localeCompare(b.label, undefined, { sensitivity: "base" })
                        )
                );
            }
        });
    }, []);

    const fileTypeOptions = useMemo(
        () => fileTypeOptionsFromHits(searchResult?.hits?.hits || []),
        [searchResult]
    );

    const selected = (values?: string[]): Option[] =>
        (values || []).map((value) => ({ label: value, value }));

    const writeMultiselect = (key: string, values: string[]) => {
        if (values.length === 0) {
            onFilterChange(key, null);
        } else {
            onFilterChange(key, { label: values.join(", "), value: values[0], values });
        }
    };

    return (
        <ExpandableSection
            headerText="Filters"
            variant="footer"
            defaultExpanded={true}
            headingTagOverride="h5"
        >
            <Box variant="p" color="text-body-secondary" margin={{ bottom: "s" }}>
                Natural-language search matches on meaning; these filters narrow where it looks.
            </Box>
            <SpaceBetween direction="vertical" size="m">
                <FormField
                    label={Synonyms.Database}
                    description={
                        databaseLocked
                            ? "Locked by URL parameter"
                            : `Select one or more ${Synonyms.databases} (empty = all)`
                    }
                >
                    <Multiselect
                        selectedOptions={selected(filters.str_databaseid?.values)}
                        onChange={({ detail }) =>
                            writeMultiselect(
                                "str_databaseid",
                                detail.selectedOptions
                                    .map((opt) => opt.value)
                                    .filter((val): val is string => val !== undefined)
                            )
                        }
                        options={databases}
                        placeholder={`All ${Synonyms.databases}`}
                        disabled={loading || databaseLocked}
                        filteringType="auto"
                        filteringAriaLabel={`Filter ${Synonyms.databases}`}
                    />
                </FormField>

                {recordType === "file" && (
                    <FormField
                        label="File Type"
                        description="Types present in the current results (empty = all)"
                    >
                        <Multiselect
                            selectedOptions={selected(filters.str_fileext?.values)}
                            onChange={({ detail }) =>
                                writeMultiselect(
                                    "str_fileext",
                                    detail.selectedOptions
                                        .map((opt) => opt.value)
                                        .filter((val): val is string => val !== undefined)
                                )
                            }
                            options={fileTypeOptions}
                            placeholder="All types"
                            disabled={loading}
                            filteringType="auto"
                            filteringAriaLabel="Filter file types"
                        />
                    </FormField>
                )}

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

export default ReducedFiltersPanel;
