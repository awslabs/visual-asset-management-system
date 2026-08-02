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
