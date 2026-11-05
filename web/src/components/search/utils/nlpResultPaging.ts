/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { NlpSearchResponse, SearchResult } from "../types";

/**
 * Paging and sorting over a natural-language result set. `POST /search/nlp` returns at most 100
 * candidates and has no cursor, so the table pages within the returned hits here instead of
 * re-querying. The count shown is the route's own total (see nlpResultTotal); `pagedTotal` is the
 * returned count, so the paginator sizes itself from the hits it can show.
 */

export interface HitSort {
    field: string;
    order: "asc" | "desc";
}

/** What the table shows before any query has been typed in natural-language mode. */
export const EMPTY_NLP_RESPONSE: NlpSearchResponse = {
    hits: { total: { value: 0, relation: "eq" }, hits: [] },
    nlp: {
        query: "",
        embeddingModelId: "",
        databasesSearched: 0,
        candidatesEvaluated: 0,
        itemsCollapsed: 0,
        classIntent: [],
        truncated: false,
    },
    warnings: [],
};

function hitValue(hit: SearchResult, field: string): unknown {
    return field === "_score" ? hit._score : hit._source?.[field];
}

function compareValues(a: unknown, b: unknown): number {
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a).localeCompare(String(b), undefined, { sensitivity: "base" });
}

/** A sorted copy of `hits`; only the first sort key is applied. Missing values sort last either way. */
export function sortNlpHits(hits: SearchResult[], sort: HitSort[]): SearchResult[] {
    if (sort.length === 0) return hits;
    const { field, order } = sort[0];
    const direction = order === "desc" ? -1 : 1;
    return [...hits].sort((a, b) => {
        const va = hitValue(a, field);
        const vb = hitValue(b, field);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return compareValues(va, vb) * direction;
    });
}

/**
 * The one count a natural-language search shows, in its notification, the heading badge and the table
 * header: the route's total (every readable hit it found, which its facets count too), and a minimum
 * (`gte`, shown as "101+") when the route reports its total as one (a search window filled) or returned
 * fewer hits than it counted (it returns at most the requested size).
 */
export function nlpResultTotal(result: NlpSearchResponse): {
    value: number;
    relation: "eq" | "gte";
} {
    const returned = result.hits.hits.length;
    const value = Math.max(result.hits.total.value ?? 0, returned);
    const lowerBound = result.hits.total.relation === "gte" || returned < value;
    return { value, relation: lowerBound ? "gte" : "eq" };
}

/** `result` with its total replaced by nlpResultTotal, for the completion notification. */
export function withNlpResultTotal(result: NlpSearchResponse): NlpSearchResponse {
    const total = nlpResultTotal(result);
    return { ...result, hits: { ...result.hits, total }, aggregationTotal: total.value };
}

/**
 * The page of `result` the table renders. Its total is nlpResultTotal's, so every place that shows the
 * count shows the same one; `pagedTotal` is the number of returned hits, which the pages cover.
 */
export function pageNlpResult(
    result: NlpSearchResponse,
    pagination: { from: number; size: number },
    sort: HitSort[]
): NlpSearchResponse {
    const all = sortNlpHits(result.hits.hits, sort);
    const from = Math.max(0, pagination.from);
    const size = Math.max(1, pagination.size);
    const total = nlpResultTotal(result);
    return {
        ...result,
        hits: {
            ...result.hits,
            total,
            hits: all.slice(from, from + size),
        },
        aggregationTotal: total.value,
        pagedTotal: all.length,
    };
}
