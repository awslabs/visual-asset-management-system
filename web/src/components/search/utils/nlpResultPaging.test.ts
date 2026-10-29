/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { EMPTY_NLP_RESPONSE, pageNlpResult, sortNlpHits } from "./nlpResultPaging";
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
