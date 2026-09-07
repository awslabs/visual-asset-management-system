/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { searchAssetFilesPaged } from "./assets";

jest.mock("../../../services/APIService", () => ({
    searchAssets: jest.fn(),
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
    fetchAssetS3Files: jest.fn(),
}));
jest.mock("../../../services/appCache", () => ({ appCache: { getItem: jest.fn() } }));

const api = () => require("../../../services/APIService");
const cache = () => require("../../../services/appCache").appCache;

/** A file-index response envelope. `str_key` is the asset-relative path (see fileIndexer.py). */
const fileHits = (keys: string[], total?: number) => ({
    hits: {
        hits: keys.map((key) => ({ _source: { str_key: key } })),
        total: { value: total ?? keys.length },
    },
});

describe("searchAssetFilesPaged", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        cache().getItem.mockReturnValue({ featuresEnabled: [] });
    });

    it("resolves matches on the server rather than listing every file", async () => {
        // The point: an asset with thousands of files must not be pulled into the browser.
        api().searchAssets.mockResolvedValue([true, fileHits(["/models/pump.glb"])]);
        const [ok, page] = await searchAssetFilesPaged("pump", "db1", "a1");
        expect(ok).toBe(true);
        expect((page as any).items).toEqual([
            {
                fileName: "pump.glb",
                key: "/models/pump.glb",
                relativePath: "/models/pump.glb",
                isFolder: false,
            },
        ]);
        expect((page as any).listFallback).toBe(false);
        expect(api().fetchAssetS3Files).not.toHaveBeenCalled();
    });

    it("searches the file index scoped to the one asset", async () => {
        api().searchAssets.mockResolvedValue([true, fileHits([])]);
        await searchAssetFilesPaged("x", "db1", "a1");
        const body = api().searchAssets.mock.calls[0][0];
        expect(body.entityTypes).toEqual(["file"]);
        const filters = JSON.stringify(body.filters);
        expect(filters).toContain("db1");
        expect(filters).toContain("a1");
    });

    it("normalizes a key with no leading slash to the asset-relative form", async () => {
        // The execute request and the filter matcher both expect a single leading '/'.
        api().searchAssets.mockResolvedValue([true, fileHits(["models/pump.glb"])]);
        const [, page] = await searchAssetFilesPaged("", "db1", "a1");
        expect((page as any).items[0].relativePath).toBe("/models/pump.glb");
    });

    it("drops folder rows — the wizard picks a file, not a container", async () => {
        api().searchAssets.mockResolvedValue([true, fileHits(["/models/", "/models/pump.glb"])]);
        const [, page] = await searchAssetFilesPaged("", "db1", "a1");
        expect((page as any).items.map((f: any) => f.relativePath)).toEqual(["/models/pump.glb"]);
    });

    it("reports the server's total so the caller can say the page is capped", async () => {
        api().searchAssets.mockResolvedValue([true, fileHits(["/a.glb"], 8123)]);
        const [, page] = await searchAssetFilesPaged("", "db1", "a1");
        expect((page as any).total).toBe(8123);
    });

    it("uses the direct listing when OpenSearch is disabled for the deployment", async () => {
        cache().getItem.mockReturnValue({ featuresEnabled: ["NOOPENSEARCH"] });
        api().fetchAssetS3Files.mockResolvedValue([
            true,
            [
                { relativePath: "/pump.glb", isFolder: false },
                { relativePath: "/valve.obj", isFolder: false },
            ],
        ]);
        const [ok, page] = await searchAssetFilesPaged("pump", "db1", "a1");
        expect(ok).toBe(true);
        expect(api().searchAssets).not.toHaveBeenCalled();
        expect((page as any).items.map((f: any) => f.relativePath)).toEqual(["/pump.glb"]);
        // Flagged, so the UI can say the result may be partial rather than authoritative.
        expect((page as any).listFallback).toBe(true);
    });

    it("falls back to the direct listing when a search call fails", async () => {
        api().searchAssets.mockRejectedValue(new Error("search down"));
        api().fetchAssetS3Files.mockResolvedValue([
            true,
            [{ relativePath: "/pump.glb", isFolder: false }],
        ]);
        const [ok, page] = await searchAssetFilesPaged("pump", "db1", "a1");
        expect(ok).toBe(true);
        expect((page as any).listFallback).toBe(true);
    });

    it("treats a response with no hit envelope as unavailable, not as zero matches", async () => {
        api().searchAssets.mockResolvedValue([true, { unexpected: "shape" }]);
        api().fetchAssetS3Files.mockResolvedValue([
            true,
            [{ relativePath: "/pump.glb", isFolder: false }],
        ]);
        const [, page] = await searchAssetFilesPaged("pump", "db1", "a1");
        expect((page as any).listFallback).toBe(true);
        expect((page as any).items).toHaveLength(1);
    });

    it("surfaces a failure when neither path can produce files", async () => {
        api().searchAssets.mockRejectedValue(new Error("down"));
        api().fetchAssetS3Files.mockResolvedValue([false, "boom"]);
        const [ok, err] = await searchAssetFilesPaged("x", "db1", "a1");
        expect(ok).toBe(false);
        expect(typeof err).toBe("string");
    });
});

/**
 * Asset AND database scoping must both be exact matches.
 *
 * Same analyzed-field hazard as the asset picker: a quoted phrase on `str_databaseid` matches any
 * database whose tokens start with the same sequence, so a file search "scoped" to smoke-db returned
 * a file belonging to a smoke-db-2 asset. Verified against the deployed index — the old filter
 * returned `smoke-db-2 /scan/a.e57` for a smoke-db + <smoke-db-2 asset> pair that should match
 * nothing; the .keyword filters return zero rows.
 */
describe("searchAssetFilesPaged scoping", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        cache().getItem.mockReturnValue({ featuresEnabled: [] });
        api().searchAssets.mockResolvedValue([true, fileHits([], 0)]);
    });

    const sentFilters = () => api().searchAssets.mock.calls[0][0].filters;

    it("scopes both ids to their .keyword subfields", async () => {
        await searchAssetFilesPaged("", "smoke-db", "a1");
        expect(sentFilters()).toEqual([
            { query_string: { query: 'str_databaseid.keyword:"smoke-db"' } },
            { query_string: { query: 'str_assetid.keyword:"a1"' } },
        ]);
    });

    it("uses only the query_string key the backend model requires", async () => {
        // A bare `term` filter is rejected with `filters.N.query_string: field required` (400).
        await searchAssetFilesPaged("", "smoke-db", "a1");
        for (const f of sentFilters()) {
            expect(Object.keys(f)).toEqual(["query_string"]);
        }
    });

    it("does not target the analyzed fields", async () => {
        await searchAssetFilesPaged("", "smoke-db", "a1");
        const serialized = JSON.stringify(sentFilters());
        expect(serialized).not.toContain('str_databaseid:("');
        expect(serialized).not.toContain('str_assetid:("');
    });

    it("keeps the two ids in separate clauses so both must hold", async () => {
        await searchAssetFilesPaged("", "smoke-db", "a1");
        expect(sentFilters()).toHaveLength(2);
    });
});

/**
 * The file picker must degrade the same way when NOOPENSEARCH is set.
 *
 * `POST /search` is unavailable in those deployments, so the file list has to come from the direct
 * S3 listing endpoint and be filtered in the browser.
 */
describe("NOOPENSEARCH file fallback", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        cache().getItem.mockReturnValue({ featuresEnabled: ["NOOPENSEARCH"] });
        api().fetchAssetS3Files.mockResolvedValue([
            true,
            [
                { fileName: "pump.glb", key: "a1/pump.glb", relativePath: "/pump.glb" },
                { fileName: "valve.stl", key: "a1/valve.stl", relativePath: "/valve.stl" },
            ],
        ]);
    });

    it("never calls the search API", async () => {
        await searchAssetFilesPaged("pump", "db1", "a1");
        expect(api().searchAssets).not.toHaveBeenCalled();
    });

    it("lists the asset's files and filters locally", async () => {
        const [ok, page] = await searchAssetFilesPaged("pump", "db1", "a1");
        expect(ok).toBe(true);
        expect(api().fetchAssetS3Files).toHaveBeenCalled();
        expect((page as any).listFallback).toBe(true);
    });

    it("returns every file for an empty term", async () => {
        const [, page] = await searchAssetFilesPaged("", "db1", "a1");
        expect((page as any).items.length).toBeGreaterThan(0);
    });
});
