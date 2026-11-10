/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    EMPTY_NLP_RESPONSE,
    nlpCountExplanation,
    nlpCountsAreMinimums,
    nlpResultTotal,
    pageNlpResult,
    sortNlpHits,
    withNlpResultTotal,
} from "./nlpResultPaging";
import {
    formatTotalResultCount,
    getPagedResultCount,
    getTotalResultCount,
    isTotalLowerBound,
} from "../types";
import type { NlpSearchResponse, SearchResult } from "../types";

const hit = (id: string, score: number, key: string, size?: number): SearchResult => ({
    _id: id,
    _score: score,
    _source: { str_key: key, num_filesize: size },
});

// The route's relation: every total that is not zero is a minimum, a truncated one included.
const response = (hits: SearchResult[], truncated = false): NlpSearchResponse => ({
    hits: {
        total: { value: hits.length, relation: truncated || hits.length > 0 ? "gte" : "eq" },
        hits,
    },
    nlp: {
        query: "q",
        embeddingModelId: "m",
        databasesSearched: 1,
        candidatesEvaluated: hits.length,
        itemsCollapsed: 0,
        classIntent: [],
        truncated,
    },
    warnings: [],
});

const hits = [hit("a", 0.9, "/z.glb", 30), hit("b", 0.5, "/a.glb"), hit("c", 0.7, "/m.glb", 10)];

describe("sortNlpHits", () => {
    it("keeps the API order (best first) when no sort is set", () => {
        expect(sortNlpHits(hits, []).map((h) => h._id)).toEqual(["a", "b", "c"]);
    });

    it("sorts by _score in either direction", () => {
        expect(sortNlpHits(hits, [{ field: "_score", order: "desc" }]).map((h) => h._id)).toEqual([
            "a",
            "c",
            "b",
        ]);
        expect(sortNlpHits(hits, [{ field: "_score", order: "asc" }]).map((h) => h._id)).toEqual([
            "b",
            "c",
            "a",
        ]);
    });

    it("sorts by a _source string field and keeps missing values last in both directions", () => {
        expect(sortNlpHits(hits, [{ field: "str_key", order: "asc" }]).map((h) => h._id)).toEqual([
            "b",
            "c",
            "a",
        ]);
        expect(
            sortNlpHits(hits, [{ field: "num_filesize", order: "asc" }]).map((h) => h._id)
        ).toEqual(["c", "a", "b"]);
        expect(
            sortNlpHits(hits, [{ field: "num_filesize", order: "desc" }]).map((h) => h._id)
        ).toEqual(["a", "c", "b"]);
    });

    it("does not mutate its input", () => {
        const copy = [...hits];
        sortNlpHits(hits, [{ field: "_score", order: "asc" }]);
        expect(hits).toEqual(copy);
    });
});

describe("pageNlpResult", () => {
    it("slices the requested page and reports the full count as the total", () => {
        const page = pageNlpResult(response(hits), { from: 2, size: 2 }, []);
        expect(page.hits.hits.map((h) => h._id)).toEqual(["c"]);
        expect(page.hits.total).toEqual({ value: 3, relation: "gte" });
        expect(page.aggregationTotal).toBe(3);
        expect(page.nlp.truncated).toBe(false);
    });

    it("keeps the gte relation of a truncated response so the counter reads 100+", () => {
        const page = pageNlpResult(response(hits, true), { from: 0, size: 50 }, []);
        expect(page.hits.total.relation).toBe("gte");
        expect(page.nlp.truncated).toBe(true);
    });

    it("shows a non-window answer as a minimum because the route reports it as one", () => {
        // No window filled (truncated false), yet the matching is approximate, so the route says gte
        // and every place shows "3+"; the "top N" line still keys on truncated alone.
        const page = pageNlpResult(response(hits), { from: 0, size: 50 }, []);
        expect(page.nlp.truncated).toBe(false);
        expect(isTotalLowerBound(page)).toBe(true);
        expect(formatTotalResultCount(page)).toBe("3+");
        expect(formatTotalResultCount(withNlpResultTotal(response(hits)))).toBe("3+");
        expect(nlpCountsAreMinimums(response(hits))).toBe(true);
    });

    it("keeps zero exact: an empty answer shows 0, never 0+", () => {
        const empty = response([]);
        expect(empty.hits.total).toEqual({ value: 0, relation: "eq" });
        expect(formatTotalResultCount(pageNlpResult(empty, { from: 0, size: 50 }, []))).toBe("0");
        expect(nlpCountsAreMinimums(empty)).toBe(false);
    });

    it("passes the route's relation through as it came, eq included", () => {
        // The relation is the route's decision; the page never derives one of its own.
        const exact = response(hits);
        exact.hits.total = { value: 3, relation: "eq" };
        expect(nlpResultTotal(exact)).toEqual({ value: 3, relation: "eq" });
        expect(nlpCountsAreMinimums(exact)).toBe(false);
    });

    it("shows the route's total when the API found more readable hits than it returned", () => {
        // The route counts every readable hit (its total and facets) but returns at most `size`.
        // The count is the route's total, the one the announcement and `vamscli search nlp` show too,
        // a minimum as the route says, and the pages cover the returned hits only.
        const more = response(hits);
        more.hits.total = { value: 150, relation: "gte" };
        const page = pageNlpResult(more, { from: 0, size: 50 }, []);
        expect(page.hits.total).toEqual({ value: 150, relation: "gte" });
        expect(page.aggregationTotal).toBe(150);
        expect(page.pagedTotal).toBe(3);
        expect(formatTotalResultCount(page)).toBe("150+");
        expect(getPagedResultCount(page)).toBe(3);
    });

    it("gives the notification, the badge and the table header one count", () => {
        // A search whose route found 101 matches, filled its window (gte) and returned 100.
        const many = Array.from({ length: 100 }, (_, i) =>
            hit(`h${i}`, 1 - i / 1000, `/f${i}.glb`)
        );
        const answer = response(many, true);
        answer.hits.total = { value: 101, relation: "gte" };
        const notified = withNlpResultTotal(answer);
        const page = pageNlpResult(answer, { from: 0, size: 50 }, []);
        expect(formatTotalResultCount(notified)).toBe("101+");
        expect(formatTotalResultCount(page)).toBe("101+");
        expect(isTotalLowerBound(page)).toBe(true);
        expect(getTotalResultCount(page)).toBe(101);
        expect(getPagedResultCount(page)).toBe(100);
        expect(nlpResultTotal(answer)).toEqual({ value: 101, relation: "gte" });
    });

    it("shows a restricted user's count the way the CLI does", () => {
        // A database-user role, "gearbox", Files: the route counted 132 readable matches (gte) and returned 100;
        // `vamscli search nlp` printed "Found 132+ files" while the page showed "100+".
        const many = Array.from({ length: 100 }, (_, i) =>
            hit(`r${i}`, 1 - i / 1000, `/g${i}.glb`)
        );
        const restricted = response(many, true);
        restricted.hits.total = { value: 132, relation: "gte" };
        expect(formatTotalResultCount(withNlpResultTotal(restricted))).toBe("132+");
        expect(formatTotalResultCount(pageNlpResult(restricted, { from: 0, size: 50 }, []))).toBe(
            "132+"
        );
        expect(getPagedResultCount(pageNlpResult(restricted, { from: 50, size: 50 }, []))).toBe(
            100
        );
    });

    it("keeps a keyword result's paging on its total", () => {
        const keyword = {
            hits: { total: { value: 1200, relation: "gte" }, hits: [] },
            aggregationTotal: 1300,
        };
        expect(getPagedResultCount(keyword)).toBe(1300);
        expect(formatTotalResultCount(keyword)).toBe("1,300+");
    });

    it("applies the sort before slicing", () => {
        const page = pageNlpResult(response(hits), { from: 0, size: 1 }, [
            { field: "str_key", order: "asc" },
        ]);
        expect(page.hits.hits[0]._id).toBe("b");
    });

    it("clamps a negative from and a zero size", () => {
        const page = pageNlpResult(response(hits), { from: -5, size: 0 }, []);
        expect(page.hits.hits.map((h) => h._id)).toEqual(["a"]);
    });

    it("exposes an empty envelope with a false truncated flag", () => {
        expect(EMPTY_NLP_RESPONSE.hits.hits).toEqual([]);
        expect(EMPTY_NLP_RESPONSE.hits.total).toEqual({ value: 0, relation: "eq" });
        expect(EMPTY_NLP_RESPONSE.nlp.truncated).toBe(false);
        expect(EMPTY_NLP_RESPONSE.warnings).toEqual([]);
    });
});

describe("nlpCountExplanation: why a natural-language count is a minimum", () => {
    const approximate =
        "The + means there may be more: natural-language search finds the closest matches approximately, so it can miss a weak match.";

    it("names approximate matching for an answer whose windows did not fill", () => {
        const text = nlpCountExplanation(response(hits));
        expect(text).toBe(`This count includes only records you can open. ${approximate}`);
        expect(text).not.toMatch(/permission rules/);
        expect(text).not.toMatch(/result limit/);
    });

    it("adds the filled window or the unsearched databases when the answer reports them", () => {
        const withWarnings = (codes: string[]) => ({
            ...response(hits, true),
            warnings: codes.map((code) => ({ code, message: "api text" })),
        });
        expect(nlpCountExplanation(withWarnings(["truncated:window"]))).toBe(
            `This count includes only records you can open. ${approximate} This search also reached its result limit.`
        );
        expect(nlpCountExplanation(withWarnings(["truncated:targets"]))).toBe(
            `This count includes only records you can open. ${approximate} This search also did not search every database.`
        );
        expect(nlpCountExplanation(withWarnings(["truncated:window", "truncated:targets"]))).toBe(
            `This count includes only records you can open. ${approximate} This search also reached its result limit and did not search every database.`
        );
        // Truncated with no cause named: both are possible.
        expect(nlpCountExplanation(withWarnings([]))).toMatch(
            /reached its result limit or did not search every database\.$/
        );
    });

    it("never repeats an API message", () => {
        const text = nlpCountExplanation({
            ...response(hits, true),
            warnings: [{ code: "truncated:window", message: "A result window of 100 filled" }],
        });
        expect(text).not.toContain("A result window of 100 filled");
    });
});
