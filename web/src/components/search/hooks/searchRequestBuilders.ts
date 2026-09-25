/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { MetadataFilter, NlpSearchRequest, SearchQuery } from "../types";

/**
 * Translation of the search sidebar state into request bodies. Pure so the two request shapes
 * (`POST /search` and `POST /search/nlp`) are built by one set of rules and tested without React.
 */

/** Sidebar keys that are UI flags or ride in dedicated request fields, never query_string clauses. */
const NON_CLAUSE_FILTER_KEYS = new Set([
    "includeMetadataInKeywordSearch",
    "showResultExplanation",
    "includeSegments",
    "_rectype",
    "bool_archived",
    "date_lastmodified_filter",
    "num_filesize_filter",
    "geo_filter",
]);

/** Candidates one NLP call returns: the DynamoDB SearchVectors TopK ceiling. */
export const NLP_SEARCH_SIZE = 100;

const TYPE_PREFIXES = ["str_", "num_", "bool_", "date_", "list_", "gp_", "gs_"];

/** `MD_str_<name>` from what a person typed: the MD_ prefix, a type prefix and wildcards are dropped. */
function metadataFieldName(key: string): string {
    let fieldName = key;
    if (fieldName.startsWith("MD_")) {
        fieldName = fieldName.substring(3);
    }
    for (const prefix of TYPE_PREFIXES) {
        if (fieldName.startsWith(prefix)) {
            fieldName = fieldName.substring(prefix.length);
            break;
        }
    }
    fieldName = fieldName.replace(/[*?]/g, "");
    return `MD_str_${fieldName}`;
}

/**
 * The `query_string` clauses for the sidebar filters. `omitKeys` names filter keys carried elsewhere
 * in a request (the NLP body has `databaseIds`/`fileExtensions` fields), and also suppresses the
 * URL-locked database clause when it contains "str_databaseid".
 */
export function buildKeywordFilters(
    searchQuery: SearchQuery,
    databaseId?: string,
    omitKeys: string[] = []
): object[] {
    const omit = new Set(omitKeys);
    const filters: object[] = [];

    Object.keys(searchQuery.filters).forEach((key) => {
        if (NON_CLAUSE_FILTER_KEYS.has(key) || omit.has(key)) return;
        const filter = searchQuery.filters[key];

        if (key.startsWith("bool_") && filter && typeof filter.value === "boolean") {
            filters.push({ query_string: { query: `(${key}:${filter.value})` } });
        } else if (
            filter &&
            filter.values &&
            Array.isArray(filter.values) &&
            filter.values.length > 0
        ) {
            const orQuery = filter.values.map((val: string) => `"${val}"`).join(" OR ");
            filters.push({ query_string: { query: `(${key}:(${orQuery}))` } });
        } else if (filter && filter.value !== "all" && filter.value !== "") {
            filters.push({ query_string: { query: `(${key}:("${filter.value}"))` } });
        }
    });

    const dateFilter = searchQuery.filters.date_lastmodified_filter;
    if (dateFilter) {
        let queryString = "";
        if (dateFilter.operator === "between" && Array.isArray(dateFilter.value)) {
            const [startDate, endDate] = dateFilter.value;
            if (startDate && endDate) {
                queryString = `date_lastmodified:[${startDate} TO ${endDate}]`;
            }
        } else if (typeof dateFilter.value === "string" && dateFilter.value) {
            switch (dateFilter.operator) {
                case ">":
                    queryString = `date_lastmodified:>=${dateFilter.value}`;
                    break;
                case "<":
                    queryString = `date_lastmodified:<=${dateFilter.value}`;
                    break;
                case "=":
                    queryString = `date_lastmodified:${dateFilter.value}`;
                    break;
            }
        }
        if (queryString) {
            filters.push({ query_string: { query: queryString } });
        }
    }

    const sizeFilter = searchQuery.filters.num_filesize_filter;
    if (sizeFilter) {
        let queryString = "";
        if (sizeFilter.operator === "between" && Array.isArray(sizeFilter.value)) {
            const [minSize, maxSize] = sizeFilter.value;
            if (minSize !== undefined && maxSize !== undefined) {
                queryString = `num_filesize:[${minSize} TO ${maxSize}]`;
            }
        } else if (typeof sizeFilter.value === "number") {
            switch (sizeFilter.operator) {
                case ">":
                    queryString = `num_filesize:>=${sizeFilter.value}`;
                    break;
                case "<":
                    queryString = `num_filesize:<=${sizeFilter.value}`;
                    break;
                case "=":
                    queryString = `num_filesize:${sizeFilter.value}`;
                    break;
            }
        }
        if (queryString) {
            filters.push({ query_string: { query: queryString } });
        }
    }

    // The URL-locked database targets the `.keyword` subfield for an exact match: str_databaseid is
    // analyzed, so a phrase on it also matches "smoke-db-2" when locked to "smoke-db". It stays a
    // query_string because the backend's SearchFilterModel requires that key.
    if (databaseId && !omit.has("str_databaseid")) {
        filters.push({ query_string: { query: `str_databaseid.keyword:"${databaseId}"` } });
    }

    return filters;
}

/**
 * The `metadataQuery` string: `MD_str_<name>:<value>` terms in "both" mode, names only in "key" mode,
 * values only in "value" mode, joined by the operator (AND when none is given). Rows with an empty
 * field name (or, in value mode, an empty value) are ignored.
 */
export function buildMetadataQuery(
    metadataFilters: MetadataFilter[],
    metadataSearchMode?: string,
    metadataOperator?: string
): string {
    if (metadataFilters.length === 0) return "";
    const operator = metadataOperator || "AND";

    if (metadataSearchMode === "value") {
        return metadataFilters
            .filter((filter) => filter.value && filter.value.trim() !== "")
            .map((filter) => filter.value.trim())
            .join(` ${operator} `);
    }
    if (metadataSearchMode === "key") {
        return metadataFilters
            .filter((filter) => filter.key && filter.key.trim() !== "")
            .map((filter) => metadataFieldName(filter.key))
            .join(` ${operator} `);
    }
    return metadataFilters
        .filter((filter) => filter.key && filter.key.trim() !== "")
        .map((filter) => `${metadataFieldName(filter.key)}:${filter.value}`)
        .join(` ${operator} `);
}

/** The entity types the `_rectype` filter selects; both when it is absent. */
export function entityTypesFor(searchQuery: SearchQuery): ("asset" | "file")[] {
    const rectypeFilter = searchQuery.filters._rectype;
    if (!rectypeFilter) return ["asset", "file"];
    if (rectypeFilter.value === "asset") return ["asset"];
    if (rectypeFilter.value === "file") return ["file"];
    return [];
}

/** The `POST /search` body (`SearchRequestModel`). */
export function buildKeywordSearchRequest(
    searchQuery: SearchQuery,
    databaseId?: string,
    metadataSearchMode?: string,
    metadataOperator?: string
): Record<string, unknown> {
    const filters = buildKeywordFilters(searchQuery, databaseId);
    const metadataQuery = buildMetadataQuery(
        searchQuery.metadataFilters,
        metadataSearchMode,
        metadataOperator
    );
    const entityTypes = entityTypesFor(searchQuery);

    return {
        query: searchQuery.query || undefined,
        filters: filters.length > 0 ? filters : undefined,
        sort: searchQuery.sort && searchQuery.sort.length > 0 ? searchQuery.sort : undefined,
        from: searchQuery.pagination.from,
        size: searchQuery.pagination.size,
        entityTypes: entityTypes.length > 0 ? entityTypes : undefined,
        metadataQuery: metadataQuery || undefined,
        metadataSearchMode: metadataSearchMode || "both",
        includeMetadataInSearch: searchQuery.filters.includeMetadataInKeywordSearch !== false,
        aggregations: true,
        includeHighlights: true,
        explainResults: searchQuery.filters.showResultExplanation || false,
        includeArchived: !!searchQuery.filters.bool_archived,
        geoSearch: searchQuery.filters.geo_filter || undefined,
    };
}

export interface NlpRequestOptions {
    /** The URL-locked database; wins over the sidebar multiselect. */
    databaseId?: string;
    metadataSearchMode?: string;
    metadataOperator?: string;
    /** False when OpenSearch is off: the route would ignore the constraints and warn. */
    includeOpenSearchConstraints: boolean;
}

/** The values a multiselect-style filter holds (`values`, else its single `value`). */
function selectedValues(filter: any): string[] {
    if (!filter) return [];
    if (Array.isArray(filter.values) && filter.values.length > 0) return filter.values;
    if (filter.value && filter.value !== "all") return [filter.value];
    return [];
}

/** The `POST /search/nlp` body (`NlpSearchRequestModel`). */
export function buildNlpSearchRequest(
    searchQuery: SearchQuery,
    options: NlpRequestOptions
): NlpSearchRequest {
    const { filters } = searchQuery;
    const body: NlpSearchRequest = {
        query: searchQuery.query.trim(),
        size: NLP_SEARCH_SIZE,
        includeArchived: !!filters.bool_archived,
    };
    // `Search inside files` cleared: rank over whole-file vectors only. Checked (the default)
    // sends nothing, so the route's own default applies.
    if (filters.includeSegments === false) {
        body.includeSegments = false;
    }

    const entityTypes = entityTypesFor(searchQuery);
    if (entityTypes.length === 1) {
        body.entityTypes = entityTypes;
    }

    const databaseIds = options.databaseId
        ? [options.databaseId]
        : selectedValues(filters.str_databaseid);
    if (databaseIds.length > 0) {
        body.databaseIds = databaseIds;
    }

    const fileExtensions = selectedValues(filters.str_fileext);
    if (fileExtensions.length > 0) {
        body.fileExtensions = fileExtensions;
    }

    if (options.includeOpenSearchConstraints) {
        const clauses = buildKeywordFilters(searchQuery, undefined, [
            "str_databaseid",
            "str_fileext",
        ]);
        if (clauses.length > 0) {
            body.filters = clauses;
        }
        const metadataQuery = buildMetadataQuery(
            searchQuery.metadataFilters,
            options.metadataSearchMode,
            options.metadataOperator
        );
        if (metadataQuery) {
            body.metadataQuery = metadataQuery;
            body.metadataSearchMode = options.metadataSearchMode || "both";
        }
        if (filters.geo_filter) {
            body.geoSearch = filters.geo_filter;
        }
    }

    return body;
}
