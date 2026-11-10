/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import FormField from "@cloudscape-design/components/form-field";
import Multiselect from "@cloudscape-design/components/multiselect";
import SpaceBetween from "@cloudscape-design/components/space-between";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Box from "@cloudscape-design/components/box";
import Input from "@cloudscape-design/components/input";
import { SearchFilters } from "../types";
import { fetchAllDatabases, fetchTags } from "../../../services/APIService";
import Synonyms from "../../../synonyms";
import { selectedValues as filterValues } from "../hooks/searchRequestBuilders";
import { lockedDatabasePlaceholder } from "./lockedDatabase";

/** Sort dropdown options case-insensitively by label so users get a predictable list. */
const sortOptionsByLabel = <T extends { label?: string; value?: string }>(opts: T[]): T[] =>
    [...opts].sort((a, b) =>
        (a.label || a.value || "").localeCompare(b.label || b.value || "", undefined, {
            sensitivity: "base",
        })
    );

/** A terms aggregation as both search routes return it. */
type FacetAggregation =
    | {
          buckets?: Array<{ key: string; doc_count: number }>;
          /** Records whose value is not among the buckets; 0 when every value is listed, null when not reported. */
          sum_other_doc_count?: number | null;
      }
    | undefined;

/**
 * Whether a facet's bucket list is cut: the search counted records of values it did not return as
 * buckets (`sum_other_doc_count` above 0, #1283). A list with no such count reads as complete.
 */
export function isFacetCut(aggregation: FacetAggregation): boolean {
    return (aggregation?.sum_other_doc_count ?? 0) > 0;
}

/** The note under a value list built from a cut facet. */
export const facetCutNote = (values: string) => `Only the most common ${values} are listed.`;

/** Values kept from the last non-filtered search, and whether that search's list was cut. */
interface CachedFacet {
    values: string[];
    cut: boolean;
}
const NO_CACHED_FACET: CachedFacet = { values: [], cut: false };

/**
 * Each value a terms aggregation's buckets name, with its bucket's count. A tag bucket's key can join
 * several values with commas (`splitKeys`); a value named by more than one bucket keeps the first count.
 */
function bucketCounts(aggregation: FacetAggregation, splitKeys = false): Map<string, number> {
    const counts = new Map<string, number>();
    (aggregation?.buckets || []).forEach((bucket) => {
        const values = splitKeys
            ? String(bucket.key)
                  .split(",")
                  .map((value) => value.trim())
            : [bucket.key];
        values.forEach((value) => {
            if (!counts.has(value)) counts.set(value, bucket.doc_count);
        });
    });
    return counts;
}

interface BasicFiltersPanelProps {
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;
    loading?: boolean;
    searchResult?: any;
    databaseLocked?: boolean;
    recordType: "asset" | "file";
    /** Show each count as a minimum ("100+"): a natural-language answer whose count is one. */
    countsAreMinimums?: boolean;
}

const BasicFiltersPanel: React.FC<BasicFiltersPanelProps> = ({
    filters,
    onFilterChange,
    loading = false,
    searchResult,
    databaseLocked = false,
    recordType,
    countsAreMinimums = false,
}) => {
    const facetCount = (value: number) => `${value}${countsAreMinimums ? "+" : ""}`;
    const [databases, setDatabases] = useState<any[]>([]);
    const [tags, setTags] = useState<any[]>([]);

    // The type and tag values of the last non-filtered search, kept so a filtered search can still
    // offer the other values. Values only: their counts belong to that search, not to the one shown.
    // Whether that search's list was cut is kept with them, for the note under the list.
    const [cachedAssetTypes, setCachedAssetTypes] = useState<CachedFacet>(NO_CACHED_FACET);
    const [cachedFileTypes, setCachedFileTypes] = useState<CachedFacet>(NO_CACHED_FACET);
    const [cachedTags, setCachedTags] = useState<CachedFacet>(NO_CACHED_FACET);

    useEffect(() => {
        // Load databases
        fetchAllDatabases().then((res) => {
            if (res && Array.isArray(res)) {
                setDatabases(res);
            }
        });

        // Load tags
        fetchTags().then((res) => {
            if (res && Array.isArray(res)) {
                setTags(res);
            }
        });
    }, []);

    // Check if this is a non-filtered search (no query, no basic filters except rectype). The
    // keyword and natural-language search options are not filters: they change how a query is
    // matched, not which records are in scope, so the facets of such an answer are still complete.
    const isNonFilteredSearch = () => {
        const hasQuery = filters._rectype; // Always has rectype
        const filterKeys = Object.keys(filters).filter(
            (key) =>
                key !== "_rectype" &&
                key !== "includeMetadataInKeywordSearch" &&
                key !== "showResultExplanation" &&
                key !== "includeSegments" &&
                filters[key] !== null &&
                filters[key] !== undefined
        );
        return filterKeys.length === 0;
    };

    // Update cache when we get results from a non-filtered search
    useEffect(() => {
        if (searchResult?.aggregations && isNonFilteredSearch()) {
            const aggregations = searchResult.aggregations;
            const cache = (aggregation: FacetAggregation, splitKeys = false): CachedFacet => ({
                values: Array.from(bucketCounts(aggregation, splitKeys).keys()),
                cut: isFacetCut(aggregation),
            });
            if (recordType === "asset" && aggregations.str_assettype?.buckets) {
                setCachedAssetTypes(cache(aggregations.str_assettype));
            }
            if (recordType === "file" && aggregations.str_fileext?.buckets) {
                setCachedFileTypes(cache(aggregations.str_fileext));
            }
            if (aggregations.list_tags?.buckets) {
                setCachedTags(cache(aggregations.list_tags, true));
            }
        }
    }, [searchResult, recordType, filters]);

    /**
     * A facet's options: `value (count)` from the answer on screen. In a filtered search the values
     * kept from the last non-filtered search are offered too, so another value can still be picked;
     * each shows the count of the answer on screen, and a value with no bucket in it shows none, so
     * no option repeats a count from a search that is no longer shown.
     */
    const facetList = (aggregation: FacetAggregation, cached: CachedFacet, splitKeys = false) => {
        const counts = bucketCounts(aggregation, splitKeys);
        const usesCache = cached.values.length > 0 && !isNonFilteredSearch();
        const values = usesCache
            ? Array.from(new Set([...cached.values, ...Array.from(counts.keys())]))
            : Array.from(counts.keys());
        return {
            options: sortOptionsByLabel(
                values.map((value) => {
                    const count = counts.get(value);
                    return {
                        label: count === undefined ? value : `${value} (${facetCount(count)})`,
                        value,
                    };
                })
            ),
            // The list may lack values when a facet it was built from was cut: the answer's, or the
            // cached search's when its values are offered too.
            cut: isFacetCut(aggregation) || (usesCache && cached.cut),
        };
    };

    // Build database options with result counts (alphabetized by database id). Every database comes
    // from the database API; a database without a bucket counts 0, unless the facet is cut, when its
    // records may be among those past the returned buckets, so it shows no count.
    const databaseAggregation: FacetAggregation = searchResult?.aggregations?.str_databaseid;
    const databaseFacetCut = isFacetCut(databaseAggregation);
    const databaseOptions = sortOptionsByLabel(
        databases.map((db: any) => {
            const bucket = (databaseAggregation?.buckets || []).find(
                (b: any) => b.key === db.databaseId
            );
            if (!bucket && databaseFacetCut) {
                return { label: db.databaseId, value: db.databaseId };
            }
            const count = bucket ? bucket.doc_count : 0;

            // "1 result" for exactly one match; a minimum stays plural ("1+ results"), as the page's
            // "Found 1+ results" reads.
            const noun = count === 1 && !countsAreMinimums ? "result" : "results";
            return {
                label: `${db.databaseId} (${facetCount(count)} ${noun})`,
                value: db.databaseId,
            };
        })
    );

    const assetTypes = facetList(searchResult?.aggregations?.str_assettype, cachedAssetTypes);
    const fileTypes = facetList(searchResult?.aggregations?.str_fileext, cachedFileTypes);
    const tagList = facetList(searchResult?.aggregations?.list_tags, cachedTags, true);

    // Get selected options for each filter. A database route locks the filter with `value` only, so the
    // locked control shows that database rather than its "All databases" placeholder.
    const selectedDatabases = filterValues(filters.str_databaseid).map((val) => ({
        label: val,
        value: val,
    }));

    const selectedAssetTypes = filters.str_assettype?.values
        ? filters.str_assettype.values.map((val) => ({
              label: val,
              value: val,
          }))
        : [];

    const selectedFileTypes = filters.str_fileext?.values
        ? filters.str_fileext.values.map((val) => ({
              label: val,
              value: val,
          }))
        : [];

    const selectedTags = filters.list_tags?.values
        ? filters.list_tags.values.map((val) => ({
              label: val,
              value: val,
          }))
        : [];

    return (
        <ExpandableSection
            headerText="Basic Filters"
            variant="footer"
            defaultExpanded={true}
            headingTagOverride="h5"
        >
            <Box variant="p" color="text-body-secondary" margin={{ bottom: "s" }}>
                Note: Type and tag values are kept from the last unfiltered search for easier
                multi-selection. Counts are for the current results.
            </Box>
            <SpaceBetween direction="vertical" size="m">
                {/* Asset Name Filter */}
                <FormField
                    label={`${Synonyms.Asset} Name`}
                    description={`Search by ${Synonyms.asset} name (exact)`}
                >
                    <Input
                        value={filters.str_assetname?.value || ""}
                        onChange={({ detail }) => {
                            if (detail.value.trim() === "") {
                                // Clear filter if empty
                                onFilterChange("str_assetname", null);
                            } else {
                                onFilterChange("str_assetname", {
                                    value: detail.value,
                                });
                            }
                        }}
                        placeholder={`Enter ${Synonyms.asset} name (e.g., My${Synonyms.Asset} or My*)`}
                        readOnly={loading}
                        type="text"
                    />
                </FormField>

                {/* Database Filter */}
                <FormField
                    label={Synonyms.Database}
                    description={
                        databaseLocked
                            ? "Locked by URL parameter"
                            : `Select one or more ${Synonyms.databases} (empty = all)`
                    }
                >
                    <Multiselect
                        selectedOptions={selectedDatabases}
                        onChange={({ detail }) => {
                            const selectedValues = detail.selectedOptions
                                .map((opt) => opt.value)
                                .filter((val): val is string => val !== undefined);

                            if (selectedValues.length === 0) {
                                // No selection = All databases
                                onFilterChange("str_databaseid", null);
                            } else {
                                onFilterChange("str_databaseid", {
                                    label: selectedValues.join(", "),
                                    value: selectedValues[0], // Keep first for backward compatibility
                                    values: selectedValues,
                                });
                            }
                        }}
                        options={databaseOptions}
                        // Locked: the control names the database it is locked to.
                        placeholder={lockedDatabasePlaceholder(databaseLocked, selectedDatabases)}
                        hideTokens={databaseLocked}
                        disabled={databaseLocked}
                        readOnly={loading}
                        filteringType="auto"
                        filteringAriaLabel={`Filter ${Synonyms.databases}`}
                    />
                </FormField>

                {/* Asset Type Filter - Only show for assets */}
                {recordType === "asset" && (
                    <FormField
                        label={`${Synonyms.Asset} Type`}
                        description="Select one or more types (empty = all)"
                        constraintText={assetTypes.cut ? facetCutNote("types") : undefined}
                    >
                        <Multiselect
                            selectedOptions={selectedAssetTypes}
                            onChange={({ detail }) => {
                                const selectedValues = detail.selectedOptions
                                    .map((opt) => opt.value)
                                    .filter((val): val is string => val !== undefined);

                                if (selectedValues.length === 0) {
                                    // No selection = All types
                                    onFilterChange("str_assettype", null);
                                } else {
                                    onFilterChange("str_assettype", {
                                        label: selectedValues.join(", "),
                                        value: selectedValues[0], // Keep first for backward compatibility
                                        values: selectedValues,
                                    });
                                }
                            }}
                            options={assetTypes.options}
                            placeholder="All types"
                            readOnly={loading}
                            filteringType="auto"
                            filteringAriaLabel={`Filter ${Synonyms.asset} types`}
                        />
                    </FormField>
                )}

                {/* File Type Filter - Only show for files */}
                {recordType === "file" && (
                    <FormField
                        label="File Type"
                        description="Select one or more types (empty = all)"
                        constraintText={fileTypes.cut ? facetCutNote("types") : undefined}
                    >
                        <Multiselect
                            selectedOptions={selectedFileTypes}
                            onChange={({ detail }) => {
                                const selectedValues = detail.selectedOptions
                                    .map((opt) => opt.value)
                                    .filter((val): val is string => val !== undefined);

                                if (selectedValues.length === 0) {
                                    // No selection = All types
                                    onFilterChange("str_fileext", null);
                                } else {
                                    onFilterChange("str_fileext", {
                                        label: selectedValues.join(", "),
                                        value: selectedValues[0], // Keep first for backward compatibility
                                        values: selectedValues,
                                    });
                                }
                            }}
                            options={fileTypes.options}
                            placeholder="All types"
                            readOnly={loading}
                            filteringType="auto"
                            filteringAriaLabel="Filter file types"
                        />
                    </FormField>
                )}

                {/* Tags Filter */}
                <FormField
                    label="Tags"
                    description="Select one or more tags (empty = all)"
                    constraintText={tagList.cut ? facetCutNote("tags") : undefined}
                >
                    <Multiselect
                        selectedOptions={selectedTags}
                        onChange={({ detail }) => {
                            const selectedValues = detail.selectedOptions
                                .map((opt) => opt.value)
                                .filter((val): val is string => val !== undefined);

                            if (selectedValues.length === 0) {
                                // No selection = All tags
                                onFilterChange("list_tags", null);
                            } else {
                                onFilterChange("list_tags", {
                                    label: selectedValues.join(", "),
                                    value: selectedValues[0], // Keep first for backward compatibility
                                    values: selectedValues,
                                });
                            }
                        }}
                        options={tagList.options}
                        placeholder="All tags"
                        readOnly={loading}
                        filteringType="auto"
                        filteringAriaLabel="Filter tags"
                    />
                </FormField>
            </SpaceBetween>
        </ExpandableSection>
    );
};

export default BasicFiltersPanel;
