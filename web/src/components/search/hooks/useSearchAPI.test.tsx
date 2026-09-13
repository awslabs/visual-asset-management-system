/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { renderHook } from "@testing-library/react";
import { useSearchAPI } from "./useSearchAPI";
import { buildKeywordSearchRequest } from "./searchRequestBuilders";
import { searchAssets, searchNlp } from "../../../services/APIService";
import type { SearchQuery } from "../types";

jest.mock("../../../services/APIService", () => ({
    searchAssets: jest.fn(),
    searchAssetsSimple: jest.fn(),
    fetchSearchMappings: jest.fn(),
    searchNlp: jest.fn(),
}));

const query: SearchQuery = {
    query: "red truck",
    filters: {
        _rectype: { label: "Files", value: "file" },
        str_fileext: { label: ".glb", value: ".glb", values: [".glb"] },
    },
    metadataFilters: [],
    sort: [],
    pagination: { from: 0, size: 50 },
};

describe("useSearchAPI", () => {
    beforeEach(() => jest.clearAllMocks());

    it("executeSearch posts the body the keyword builder produces", async () => {
        (searchAssets as jest.Mock).mockResolvedValue([
            true,
            { hits: { total: { value: 0 }, hits: [] } },
        ]);
        const { result } = renderHook(() => useSearchAPI());
        await result.current.executeSearch(query, "db1", "both", "OR");
        expect(searchAssets).toHaveBeenCalledWith(
            buildKeywordSearchRequest(query, "db1", "both", "OR")
        );
    });

    it("executeNlpSearch posts the NLP body and returns the envelope", async () => {
        const envelope = {
            hits: { total: { value: 0, relation: "eq" }, hits: [] },
            nlp: {
                query: "red truck",
                embeddingModelId: "m",
                databasesSearched: 1,
                candidatesEvaluated: 0,
                itemsCollapsed: 0,
                classIntent: [],
                truncated: false,
            },
            warnings: [],
        };
        (searchNlp as jest.Mock).mockResolvedValue([true, envelope]);
        const { result } = renderHook(() => useSearchAPI());
        const returned = await result.current.executeNlpSearch(query, {
            databaseId: "db1",
            includeOpenSearchConstraints: false,
        });
        expect(searchNlp).toHaveBeenCalledWith({
            query: "red truck",
            size: 100,
            entityTypes: ["file"],
            databaseIds: ["db1"],
            fileExtensions: [".glb"],
            includeArchived: false,
        });
        expect(returned).toBe(envelope);
    });

    it("executeNlpSearch throws the service message on failure", async () => {
        (searchNlp as jest.Mock).mockResolvedValue([false, "Vector index is being built"]);
        const { result } = renderHook(() => useSearchAPI());
        await expect(
            result.current.executeNlpSearch(query, { includeOpenSearchConstraints: false })
        ).rejects.toThrow("Vector index is being built");
    });
});
