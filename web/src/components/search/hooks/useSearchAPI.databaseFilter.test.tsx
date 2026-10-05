/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// The Search page's database filter must match the selected database ids exactly. `str_databaseid`
// is mapped as analyzed text with a `.keyword` subfield, and the standard analyzer splits an id on
// hyphens and lower-cases it, so a quoted value on the field itself is a phrase over the id's words:
// "smoke-db" also matches smoke-db-2, old-smoke-db and Smoke-DB. The `.keyword` subfield holds the
// exact id. The clause stays a query_string, the only filter shape POST /search accepts.
import { renderHook } from "@testing-library/react";
import { useSearchAPI } from "./useSearchAPI";
import { searchAssets } from "../../../services/APIService";
import { SearchFilters, SearchQuery } from "../types";

jest.mock("../../../services/APIService", () => ({
    searchAssets: jest.fn(),
    searchAssetsSimple: jest.fn(),
    fetchSearchMappings: jest.fn(),
}));

const mockSearchAssets = searchAssets as jest.Mock;

const queryWith = (filters: SearchFilters): SearchQuery => ({
    query: "",
    filters: { _rectype: { label: "Assets", value: "asset" }, ...filters },
    metadataFilters: [],
    sort: [],
    pagination: { from: 0, size: 50 },
});

/** The `filters` array of the one POST /search body the hook sent. */
const sentFilters = async (searchQuery: SearchQuery, databaseId?: string): Promise<object[]> => {
    const { result } = renderHook(() => useSearchAPI());
    await result.current.executeSearch(searchQuery, databaseId);
    expect(mockSearchAssets).toHaveBeenCalledTimes(1);
    return mockSearchAssets.mock.calls[0][0].filters;
};

beforeEach(() => {
    mockSearchAssets.mockReset();
    // executeSearch destructures [success, result] from the API call.
    mockSearchAssets.mockResolvedValue([true, { hits: { hits: [], total: { value: 0 } } }]);
    // The hook logs every request body.
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("useSearchAPI database filter", () => {
    it("matches the selected databases on str_databaseid.keyword", async () => {
        const filters = await sentFilters(
            queryWith({
                str_databaseid: {
                    label: "smoke-db, smoke-db-2",
                    value: "smoke-db",
                    values: ["smoke-db", "smoke-db-2"],
                },
            })
        );
        expect(filters).toContainEqual({
            query_string: { query: '(str_databaseid.keyword:("smoke-db" OR "smoke-db-2"))' },
        });
        expect(JSON.stringify(filters)).not.toContain("str_databaseid:(");
    });

    it("matches a single database value on str_databaseid.keyword", async () => {
        const filters = await sentFilters(
            queryWith({ str_databaseid: { label: "smoke-db", value: "smoke-db" } })
        );
        expect(filters).toContainEqual({
            query_string: { query: '(str_databaseid.keyword:("smoke-db"))' },
        });
        expect(JSON.stringify(filters)).not.toContain("str_databaseid:(");
    });

    it("escapes a quote or backslash inside a database value", async () => {
        const filters = await sentFilters(
            queryWith({
                str_databaseid: { label: 'a"b, c\\d', value: 'a"b', values: ['a"b', "c\\d"] },
            })
        );
        expect(filters).toContainEqual({
            query_string: { query: '(str_databaseid.keyword:("a\\"b" OR "c\\\\d"))' },
        });
    });

    it("leaves the other value filters on their own field", async () => {
        const filters = await sentFilters(
            queryWith({ str_fileext: { label: "glb, obj", value: "glb", values: ["glb", "obj"] } })
        );
        expect(filters).toContainEqual({
            query_string: { query: '(str_fileext:("glb" OR "obj"))' },
        });
    });

    it("sends the URL-locked database as an exact match only", async () => {
        const filters = await sentFilters(
            queryWith({ str_databaseid: { label: "smoke-db", value: "smoke-db" } }),
            "smoke-db"
        );
        expect(filters).toContainEqual({
            query_string: { query: 'str_databaseid.keyword:"smoke-db"' },
        });
        expect(JSON.stringify(filters)).not.toContain("str_databaseid:(");
    });
});
