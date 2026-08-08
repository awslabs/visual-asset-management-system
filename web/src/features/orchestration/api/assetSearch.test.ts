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

/**
 * Database scoping must be an EXACT match.
 *
 * These fields are analyzed, so the standard analyzer splits on hyphens. A quoted `query_string`
 * phrase filter for "smoke-db" searches the adjacent tokens [smoke, db], which ALSO matches
 * "smoke-db-2" ([smoke, db, 2]). Verified against the deployed index: the phrase filter returned 24
 * smoke-db assets plus 1 from smoke-db-2.
 *
 * The consequence reached the user as a console error, not as a wrong list: the picker offered an
 * asset from a different database, and selecting it produced
 * GET /database/smoke-db/assets/<smoke-db-2 asset>/fileInfo -> 400 "Asset not found in database".
 */
describe("searchAssetsPaged database scoping", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        cache().getItem.mockReturnValue({ featuresEnabled: [] });
        api().searchAssets.mockResolvedValue([true, hits([], 0)]);
    });

    const sentFilters = () => api().searchAssets.mock.calls[0][0].filters;

    it("scopes to the .keyword subfield for an exact match", async () => {
        await searchAssetsPaged("", "smoke-db");
        expect(sentFilters()).toEqual([
            { query_string: { query: 'str_databaseid.keyword:"smoke-db"' } },
        ]);
    });

    it("keeps the query_string key the backend model requires", async () => {
        // SearchFilterModel declares `query_string` as required, so a bare `term` filter is rejected
        // with `filters.0.query_string: field required` (400). Exactness has to be achieved
        // THROUGH a query_string, not by swapping the filter type.
        await searchAssetsPaged("", "smoke-db");
        for (const f of sentFilters()) {
            expect(Object.keys(f)).toEqual(["query_string"]);
        }
    });

    it("does not target the analyzed field, which would over-match", async () => {
        // `str_databaseid:("smoke-db")` matches the tokens [smoke, db] — which smoke-db-2 also has.
        await searchAssetsPaged("", "smoke-db");
        const serialized = JSON.stringify(sentFilters());
        expect(serialized).not.toContain('str_databaseid:("');
        expect(serialized).toContain("str_databaseid.keyword");
    });

    it("sends no database filter when no database is chosen", async () => {
        await searchAssetsPaged("pump");
        expect(api().searchAssets.mock.calls[0][0].filters).toBeUndefined();
    });

    it("sends no database filter for GLOBAL, which is not an asset database", async () => {
        // GLOBAL is the shared pipeline/workflow catalog and never a value of str_databaseid, so
        // filtering on it would return zero assets instead of all of them.
        await searchAssetsPaged("pump", "GLOBAL");
        expect(api().searchAssets.mock.calls[0][0].filters).toBeUndefined();
    });

    it("quotes a hyphenated database id so it stays one term", async () => {
        await searchAssetsPaged("", "smoke-db-2");
        expect(sentFilters()).toEqual([
            { query_string: { query: 'str_databaseid.keyword:"smoke-db-2"' } },
        ]);
    });

    it("escapes a quote in the id rather than emitting an unparseable query", async () => {
        // Asserted on the query STRING itself, not its JSON encoding, so the expectation is readable.
        await searchAssetsPaged("", 'we"ird');
        const query = (sentFilters()[0] as any).query_string.query;
        expect(query).toBe('str_databaseid.keyword:"we' + String.fromCharCode(92) + '"ird"');
    });
});

/**
 * GLOBAL is not an asset database.
 *
 * It is a real databaseId for PIPELINES and WORKFLOWS (the shared catalog), so a workflow-scoped
 * caller naturally holds "GLOBAL". The assets endpoint rejects it outright:
 * `databaseId is invalid. GLOBAL is not allowed for this field.` (400). Here it means "all databases",
 * so the list path must fall back to the unscoped endpoint rather than pass it through.
 */
describe("listAssets with GLOBAL", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        cache().getItem.mockReturnValue({ featuresEnabled: [] });
        api().fetchAllAssets.mockResolvedValue([]);
        api().fetchDatabaseAssets.mockResolvedValue([]);
    });

    it("uses the all-databases endpoint for GLOBAL", async () => {
        const { listAssets } = require("./assets");
        await listAssets("GLOBAL");
        expect(api().fetchAllAssets).toHaveBeenCalled();
        // The per-database endpoint is what returns the 400 for GLOBAL.
        expect(api().fetchDatabaseAssets).not.toHaveBeenCalled();
    });

    it("uses the all-databases endpoint when no database is given", async () => {
        const { listAssets } = require("./assets");
        await listAssets();
        expect(api().fetchAllAssets).toHaveBeenCalled();
        expect(api().fetchDatabaseAssets).not.toHaveBeenCalled();
    });

    it("still scopes to a real database", async () => {
        const { listAssets } = require("./assets");
        await listAssets("smoke-db");
        expect(api().fetchDatabaseAssets).toHaveBeenCalledWith({ databaseId: "smoke-db" });
        expect(api().fetchAllAssets).not.toHaveBeenCalled();
    });

    it("treats GLOBAL as all-databases in the search fallback too", async () => {
        // The search path degrades to the list path when the index is unavailable; the same coercion
        // has to apply there or the fallback 400s.
        const { searchAssetsPaged } = require("./assets");
        api().searchAssets.mockResolvedValue([true, {}]); // no hits envelope -> fall through
        await searchAssetsPaged("pump", "GLOBAL");
        expect(api().fetchAllAssets).toHaveBeenCalled();
        expect(api().fetchDatabaseAssets).not.toHaveBeenCalled();
    });
});

/**
 * With NOOPENSEARCH the search API must not be called at all.
 *
 * The flag means the OpenSearch collection does not exist in that deployment, so `POST /search` fails
 * — an asset/file picker that reached for it would be permanently empty rather than degraded. The
 * pickers must fall back to the plain listing endpoints and filter in the browser.
 */
describe("NOOPENSEARCH fallback", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        cache().getItem.mockReturnValue({ featuresEnabled: ["NOOPENSEARCH"] });
        api().fetchAllAssets.mockResolvedValue([
            { databaseId: "db1", assetId: "a1", assetName: "Pump housing" },
            { databaseId: "db1", assetId: "a2", assetName: "Valve" },
        ]);
        api().fetchDatabaseAssets.mockResolvedValue([
            { databaseId: "db1", assetId: "a1", assetName: "Pump housing" },
            { databaseId: "db1", assetId: "a2", assetName: "Valve" },
        ]);
    });

    it("never calls the search API", async () => {
        await searchAssetsPaged("pump", "db1");
        expect(api().searchAssets).not.toHaveBeenCalled();
    });

    it("uses the listing endpoint and filters locally", async () => {
        const [ok, page] = await searchAssetsPaged("pump", "db1");
        expect(ok).toBe(true);
        expect(api().fetchDatabaseAssets).toHaveBeenCalledWith({ databaseId: "db1" });
        expect((page as any).items.map((a: any) => a.assetId)).toEqual(["a1"]);
        // Flagged so the picker can say the list is locally filtered rather than server-resolved.
        expect((page as any).listFallback).toBe(true);
    });

    it("matches on the asset id as well as the name", async () => {
        const [, page] = await searchAssetsPaged("a2", "db1");
        expect((page as any).items.map((a: any) => a.assetId)).toEqual(["a2"]);
    });

    it("returns the full list for an empty term", async () => {
        const [, page] = await searchAssetsPaged("", "db1");
        expect((page as any).total).toBe(2);
    });

    it("still treats GLOBAL as all-databases", async () => {
        // Both guards have to hold at once: no search call AND no GLOBAL passed to the scoped endpoint.
        await searchAssetsPaged("pump", "GLOBAL");
        expect(api().searchAssets).not.toHaveBeenCalled();
        expect(api().fetchAllAssets).toHaveBeenCalled();
        expect(api().fetchDatabaseAssets).not.toHaveBeenCalled();
    });
});

/**
 * Every search must go through this service, which is where the NOOPENSEARCH guard lives.
 *
 * A component (or a new hook) calling `searchAssets` from APIService directly would bypass the guard
 * and be permanently empty in a NOOPENSEARCH deployment — with no error, since the picker would simply
 * render no options. Cheap source check; fails loudly if that boundary is crossed.
 */
describe("search boundary", () => {
    it("no orchestration file outside this service calls the search API directly", () => {
        const fs = require("fs");
        const path = require("path");
        const root = path.join(__dirname, "..");
        const offenders: string[] = [];

        const walk = (dir: string) => {
            for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
                const p = path.join(dir, entry.name);
                if (entry.isDirectory()) {
                    walk(p);
                    continue;
                }
                if (!/\.tsx?$/.test(entry.name) || entry.name.includes(".test.")) continue;
                // The service itself is the ONE place allowed to call it.
                if (p.endsWith(path.join("api", "assets.ts"))) continue;
                const src = fs.readFileSync(p, "utf-8");
                // A direct import of the raw search function from the app-wide APIService.
                if (
                    /from\s+["'][^"']*services\/APIService["']/.test(src) &&
                    /\bsearchAssets\b/.test(src)
                ) {
                    offenders.push(path.relative(root, p));
                }
            }
        };
        walk(root);

        expect(offenders).toEqual([]);
    });
});
