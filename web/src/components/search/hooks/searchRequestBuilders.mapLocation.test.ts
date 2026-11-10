/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Map view's keyword request (#651): the user's filters, metadata query, mode and operator as
 * set, plus one filter for records with a location (`geo_MD_location`, which the indexers derive
 * from a location value or latitude / longitude fields in any letter case).
 */
import { HAS_LOCATION_FILTER, buildKeywordSearchRequest } from "./searchRequestBuilders";
import type { SearchQuery } from "../types";

const assetQuery: SearchQuery = {
    query: "",
    filters: {
        _rectype: { label: "Assets", value: "asset" },
        str_assettype: { label: "glb", value: "glb", values: ["glb"] },
    },
    metadataFilters: [
        { key: "site", value: "Plant-7", operator: "=", type: "string" },
        { key: "line", value: "A", operator: "=", type: "string" },
    ],
    sort: [],
    pagination: { from: 0, size: 25 },
};

describe("buildKeywordSearchRequest for the Map view (#651)", () => {
    it("adds one location filter and keeps the user's filters, metadata query and mode", () => {
        const table = buildKeywordSearchRequest(assetQuery, undefined, "both", "AND");
        const map = buildKeywordSearchRequest(assetQuery, undefined, "both", "AND", {
            requireLocation: true,
        });

        expect(HAS_LOCATION_FILTER).toEqual({
            query_string: { query: "_exists_:geo_MD_location" },
        });
        expect(map.filters).toEqual([...(table.filters as object[]), HAS_LOCATION_FILTER]);
        expect(map.metadataQuery).toBe("MD_str_site:Plant-7 AND MD_str_line:A");
        expect(map.metadataQuery).toBe(table.metadataQuery);
        expect(map.metadataSearchMode).toBe("both");
        expect(map.entityTypes).toEqual(["asset"]);
    });

    it("asks for no metadata rows of its own, in any letter case", () => {
        const map = buildKeywordSearchRequest(
            { ...assetQuery, metadataFilters: [] },
            undefined,
            "value",
            "AND",
            { requireLocation: true }
        );
        expect(map.metadataQuery).toBeUndefined();
        expect(map.metadataSearchMode).toBe("value");
        expect(JSON.stringify(map)).not.toMatch(/MD_str_(location|latitude|longitude)/i);
    });

    it("adds the location filter alone when the user set no filter", () => {
        const map = buildKeywordSearchRequest(
            { ...assetQuery, filters: {}, metadataFilters: [] },
            undefined,
            undefined,
            undefined,
            { requireLocation: true }
        );
        expect(map.filters).toEqual([HAS_LOCATION_FILTER]);
    });

    it("sends no location filter outside the Map view", () => {
        const table = buildKeywordSearchRequest(assetQuery, undefined, "both", "AND");
        expect(JSON.stringify(table.filters)).not.toContain("geo_MD_location");
    });
});
