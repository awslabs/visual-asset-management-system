/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Synonyms from "../../../synonyms";
import type { NlpSearchResponse, NlpSearchWarning, SearchResult } from "../types";

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
 * The one count a natural-language search shows, in its announcement, the heading badge and the table
 * header: the route's total (every readable hit it found, which its facets count too), shown as a minimum
 * (`gte`, "101+") whenever the route reports it as one. The route does so for every total that is not
 * zero: the vector index finds the closest matches by approximate nearest-neighbour search, so a weak
 * match can be missed even when no window filled, and a filled window or an unsearched database are
 * further reasons. The relation is the route's decision and is passed through as it came; the table
 * pages over the returned hits (at most the requested size).
 */
export function nlpResultTotal(result: NlpSearchResponse): {
    value: number;
    relation: "eq" | "gte";
} {
    const returned = result.hits.hits.length;
    const value = Math.max(result.hits.total.value ?? 0, returned);
    return { value, relation: result.hits.total.relation === "gte" ? "gte" : "eq" };
}

/**
 * Whether the counts of a natural-language answer are minimums: its count is (nlpResultTotal), which
 * holds for every answer that found something, so each facet count beside it is shown with "+" too. The
 * facets are minimums for the same reason as the total: they count the same approximate matches. False
 * for an exact answer (zero) and for anything that is not an answer.
 */
export function nlpCountsAreMinimums(result: NlpSearchResponse | null | undefined): boolean {
    if (!result?.nlp || !Array.isArray(result.hits?.hits) || !result.hits.total) return false;
    return nlpResultTotal(result).relation === "gte";
}

/**
 * The "About this count" text for a natural-language count shown as a minimum. Matching is approximate,
 * so that is the reason every such count gives; a filled window or an unsearched database is named too
 * when the answer reports one (`nlp.truncated`, with the `truncated:*` warning saying which).
 */
export function nlpCountExplanation(
    result:
        | { nlp?: { truncated?: boolean } | null; warnings?: NlpSearchWarning[] | null }
        | null
        | undefined
): string {
    const sentences = [
        "This count includes only records you can open.",
        "The + means there may be more: natural-language search finds the closest matches approximately, so it can miss a weak match.",
    ];
    if (result?.nlp?.truncated) {
        const codes = new Set((result.warnings ?? []).map((w) => w.code));
        const window = codes.has("truncated:window");
        const targets = codes.has("truncated:targets");
        if (window && targets) {
            sentences.push(
                `This search also reached its result limit and did not search every ${Synonyms.database}.`
            );
        } else if (targets) {
            sentences.push(`This search also did not search every ${Synonyms.database}.`);
        } else if (window) {
            sentences.push("This search also reached its result limit.");
        } else {
            sentences.push(
                `This search also reached its result limit or did not search every ${Synonyms.database}.`
            );
        }
    }
    return sentences.join(" ");
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
