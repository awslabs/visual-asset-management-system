/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { searchNlp } from "./APIService";
import { apiClient } from "./apiClient";

jest.mock("./apiClient", () => {
    class MockApiError extends Error {
        status: number;
        body: any;
        constructor(message: string, status: number, body?: any) {
            super(message);
            this.name = "ApiError";
            this.status = status;
            this.body = body;
        }
    }
    return {
        ApiError: MockApiError,
        apiClient: { get: jest.fn(), post: jest.fn() },
    };
});

const mockPost = apiClient.post as jest.Mock;

const envelope = {
    took: 12,
    timed_out: false,
    _shards: { total: 1, successful: 1, skipped: 0, failed: 0 },
    hits: {
        total: { value: 1, relation: "eq" },
        max_score: 0.91,
        hits: [
            {
                _index: "vams-vectors",
                _id: "db1#a1#/models/truck.glb#v1",
                _score: 0.91,
                _index_type: "file",
                _source: { str_databaseid: "db1", str_assetid: "a1", str_key: "/models/truck.glb" },
                _vector: {
                    distance: 0.09,
                    embeddingModelId: "amazon.titan-embed-text-v2:0",
                    sourceModalities: ["render", "text"],
                    indexedAt: "2026-09-08T00:00:00Z",
                    fileClass: "mesh",
                    segmentHits: 0,
                    bestSegment: null,
                },
            },
        ],
    },
    aggregations: {},
    aggregationTotal: 1,
    nlp: {
        query: "red truck",
        embeddingModelId: "amazon.titan-embed-text-v2:0",
        databasesSearched: 1,
        candidatesEvaluated: 1,
        itemsCollapsed: 0,
        classIntent: [],
        truncated: false,
    },
    warnings: [],
};

describe("searchNlp", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("POSTs the body to search/nlp and returns the envelope untouched", async () => {
        mockPost.mockResolvedValue(envelope);
        const result = await searchNlp({ query: "red truck", size: 100 });
        expect(mockPost).toHaveBeenCalledTimes(1);
        expect(mockPost.mock.calls[0][0]).toBe("search/nlp");
        expect(mockPost.mock.calls[0][1].body).toEqual({ query: "red truck", size: 100 });
        expect(result).toEqual([true, envelope]);
    });

    it("returns the failure tuple with the error message when the call rejects", async () => {
        mockPost.mockRejectedValue(new Error("Vector index is being built"));
        expect(await searchNlp({ query: "anything" })).toEqual([
            false,
            "Vector index is being built",
        ]);
    });
});
