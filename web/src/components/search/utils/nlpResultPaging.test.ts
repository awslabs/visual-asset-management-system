/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    EMPTY_NLP_RESPONSE,
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

const response = (hits: SearchResult[], truncated = false): NlpSearchResponse => ({
    hits: { total: { value: hits.length, relation: truncated ? "gte" : "eq" }, hits },
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
        expect(page.hits.total).toEqual({ value: 3, relation: "eq" });
        expect(page.aggregationTotal).toBe(3);
        expect(page.nlp.truncated).toBe(false);
    });

    it("keeps the gte relation of a truncated response so the counter reads 100+", () => {
        const page = pageNlpResult(response(hits, true), { from: 0, size: 50 }, []);
        expect(page.hits.total.relation).toBe("gte");
        expect(page.nlp.truncated).toBe(true);
    });

    it("shows the route's total, as a minimum, when the API found more readable hits than it returned", () => {
        // D53/D61: the route counts every readable hit (its total and facets) but returns at most `size`.
        // The count is the route's total, the one the notification shows too, marked "+" because the
        // list holds fewer; the pages cover the returned hits only.
        const more = response(hits);
        more.hits.total = { value: 150, relation: "eq" };
        const page = pageNlpResult(more, { from: 0, size: 50 }, []);
        expect(page.hits.total).toEqual({ value: 150, relation: "gte" });
        expect(page.aggregationTotal).toBe(150);
        expect(page.pagedTotal).toBe(3);
        expect(formatTotalResultCount(page)).toBe("150+");
        expect(getPagedResultCount(page)).toBe(3);
        // Control: a response whose total is its returned hits stays exact.
        const exact = pageNlpResult(response(hits), { from: 0, size: 50 }, []);
        expect(exact.hits.total).toEqual({ value: 3, relation: "eq" });
        expect(formatTotalResultCount(exact)).toBe("3");
    });

    it("gives the notification, the badge and the table header one count (UAT D61)", () => {
        // The UAT search: the route found 101 matches, its window filled (gte), and it returned 100.
        const many = Array.from({ length: 100 }, (_, i) =>
            hit(`h${i}`, 1 - i / 1000, `/f${i}.glb`)
        );
        const uat = response(many, true);
        uat.hits.total = { value: 101, relation: "gte" };
        const notified = withNlpResultTotal(uat);
        const page = pageNlpResult(uat, { from: 0, size: 50 }, []);
        expect(formatTotalResultCount(notified)).toBe("101+");
        expect(formatTotalResultCount(page)).toBe("101+");
        expect(isTotalLowerBound(page)).toBe(true);
        expect(getTotalResultCount(page)).toBe(101);
        expect(getPagedResultCount(page)).toBe(100);
        expect(nlpResultTotal(uat)).toEqual({ value: 101, relation: "gte" });
    });

    it("shows a restricted user's count the way the CLI does (UAT D61, second repro)", () => {
        // uat-dbuser, "gearbox", Files: the route counted 132 readable matches (gte) and returned 100;
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
