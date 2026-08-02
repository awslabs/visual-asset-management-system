/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { searchAssetsPaged } from "./assets";

jest.mock("../../../services/APIService", () => ({
    searchAssets: jest.fn(),
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
    fetchAssetS3Files: jest.fn(),
}));
jest.mock("../../../services/appCache", () => ({ appCache: { getItem: jest.fn() } }));

const api = () => require("../../../services/APIService");
const cache = () => require("../../../services/appCache").appCache;

/** An OpenSearch response envelope with the fields the adapter reads. */
const hits = (rows: Array<[string, string, string]>, total?: number) => ({
    hits: {
        hits: rows.map(([db, id, name]) => ({
            _source: { str_databaseid: db, str_assetid: id, str_assetname: name },
        })),
        total: { value: total ?? rows.length },
    },
});

describe("searchAssetsPaged", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        cache().getItem.mockReturnValue({ featuresEnabled: [] });
    });

    it("resolves matches on the server rather than loading every asset", async () => {
        // The whole point: a database with thousands of assets must not be pulled into the browser.
        api().searchAssets.mockResolvedValue([true, hits([["db1", "a1", "Pump"]], 1)]);
        const [ok, page] = await searchAssetsPaged("pump", "db1");
        expect(ok).toBe(true);
        expect((page as any).items).toEqual([
            { databaseId: "db1", assetId: "a1", assetName: "Pump" },
        ]);
        expect((page as any).listFallback).toBe(false);
        // The full-list endpoint must NOT be consulted on the search path.
        expect(api().fetchAllAssets).not.toHaveBeenCalled();
    });

    it("scopes the search to a database when one is given", async () => {
        api().searchAssets.mockResolvedValue([true, hits([])]);
        await searchAssetsPaged("x", "db-scoped");
        const body = api().searchAssets.mock.calls[0][0];
        expect(JSON.stringify(body.filters)).toContain("db-scoped");
        expect(body.entityTypes).toEqual(["asset"]);
        expect(body.includeArchived).toBe(false);
    });

    it("sends no database filter when searching across all databases", async () => {
        api().searchAssets.mockResolvedValue([true, hits([])]);
        await searchAssetsPaged("x");
        expect(api().searchAssets.mock.calls[0][0].filters).toBeUndefined();
    });

    it("reports the server's total so the caller can say the page is capped", async () => {
        api().searchAssets.mockResolvedValue([true, hits([["db1", "a1", "One"]], 4312)]);
        const [, page] = await searchAssetsPaged("", "db1");
        expect((page as any).total).toBe(4312);
        expect((page as any).items).toHaveLength(1);
    });

    it("uses the list endpoint when OpenSearch is disabled for the deployment", async () => {
        cache().getItem.mockReturnValue({ featuresEnabled: ["NOOPENSEARCH"] });
        api().fetchDatabaseAssets.mockResolvedValue([
            { databaseId: "db1", assetId: "a1", assetName: "Pump station" },
            { databaseId: "db1", assetId: "a2", assetName: "Valve" },
        ]);
        const [ok, page] = await searchAssetsPaged("pump", "db1");
        expect(ok).toBe(true);
        expect(api().searchAssets).not.toHaveBeenCalled();
        expect((page as any).items.map((a: any) => a.assetId)).toEqual(["a1"]);
        // Flagged, so the UI can say the result may be partial rather than authoritative.
        expect((page as any).listFallback).toBe(true);
    });

    it("falls back to the list endpoint when a search call fails", async () => {
        // A search outage must degrade to the only other option, not strand the picker empty.
        api().searchAssets.mockRejectedValue(new Error("search down"));
        api().fetchDatabaseAssets.mockResolvedValue([
            { databaseId: "db1", assetId: "a1", assetName: "Pump" },
        ]);
        const [ok, page] = await searchAssetsPaged("pump", "db1");
        expect(ok).toBe(true);
        expect((page as any).listFallback).toBe(true);
    });

    it("treats a search response with no hit envelope as unavailable, not as zero matches", async () => {
        api().searchAssets.mockResolvedValue([true, { unexpected: "shape" }]);
        api().fetchDatabaseAssets.mockResolvedValue([
            { databaseId: "db1", assetId: "a1", assetName: "Pump" },
        ]);
        const [, page] = await searchAssetsPaged("pump", "db1");
        expect((page as any).listFallback).toBe(true);
        expect((page as any).items).toHaveLength(1);
    });

    it("matches on asset id as well as name in the list fallback", async () => {
        cache().getItem.mockReturnValue({ featuresEnabled: ["NOOPENSEARCH"] });
        api().fetchDatabaseAssets.mockResolvedValue([
            { databaseId: "db1", assetId: "xabc123", assetName: "Unrelated" },
        ]);
        const [, page] = await searchAssetsPaged("xabc", "db1");
        expect((page as any).items).toHaveLength(1);
    });

    it("returns an unfiltered first page for an empty query, to seed the picker", async () => {
        api().searchAssets.mockResolvedValue([true, hits([["db1", "a1", "One"]])]);
        const [ok] = await searchAssetsPaged("   ", "db1");
        expect(ok).toBe(true);
        expect(api().searchAssets.mock.calls[0][0].query).toBe("");
    });

    it("surfaces a failure when neither path can produce assets", async () => {
        api().searchAssets.mockRejectedValue(new Error("down"));
        api().fetchDatabaseAssets.mockResolvedValue("boom");
        const [ok, err] = await searchAssetsPaged("x", "db1");
        expect(ok).toBe(false);
        expect(typeof err).toBe("string");
    });

    it("treats an unreadable config as search-available rather than stranding the picker", async () => {
        cache().getItem.mockImplementation(() => {
            throw new Error("no config");
        });
        api().searchAssets.mockResolvedValue([true, hits([["db1", "a1", "One"]])]);
        const [ok, page] = await searchAssetsPaged("one", "db1");
        expect(ok).toBe(true);
        expect((page as any).listFallback).toBe(false);
    });
});
