/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * fetchAssetS3Files sends the same query on every page of a listing: a basic-mode listing stays
 * basic on its follow-up pages.
 */

import { fetchAssetS3Files } from "./APIService";
import { apiClient } from "./apiClient";

jest.mock("./apiClient", () => ({
    ApiError: class extends Error {},
    apiClient: { get: jest.fn() },
}));

const mockGet = apiClient.get as jest.Mock;

describe("fetchAssetS3Files paging", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => {});
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it.each([
        ["items", (items: any[], token?: string) => ({ items, NextToken: token })],
        [
            "legacy message",
            (items: any[], token?: string) => ({ message: { Items: items, NextToken: token } }),
        ],
    ])("keeps basic=true on follow-up pages (%s response)", async (_label, page) => {
        mockGet
            .mockResolvedValueOnce(page([{ key: "a" }], "t1"))
            .mockResolvedValueOnce(page([{ key: "b" }], "t2"))
            .mockResolvedValueOnce(page([{ key: "c" }]));

        const [ok, items] = await fetchAssetS3Files({
            databaseId: "db1",
            assetId: "a1",
            basic: true,
        });

        expect(ok).toBe(true);
        expect(items).toEqual([{ key: "a" }, { key: "b" }, { key: "c" }]);
        const queries = mockGet.mock.calls.map((call) => call[1].queryStringParameters);
        expect(queries).toEqual([
            { includeArchived: "false", basic: "true" },
            { includeArchived: "false", basic: "true", startingToken: "t1" },
            { includeArchived: "false", basic: "true", startingToken: "t2" },
        ]);
    });

    it("sends no basic parameter when basic is not requested", async () => {
        mockGet
            .mockResolvedValueOnce({ items: [{ key: "a" }], NextToken: "t1" })
            .mockResolvedValueOnce({ items: [{ key: "b" }] });

        await fetchAssetS3Files({ databaseId: "db1", assetId: "a1", includeArchived: true });

        expect(mockGet.mock.calls[1][1].queryStringParameters).toEqual({
            includeArchived: "true",
            startingToken: "t1",
        });
    });
});
