/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    NLP_SEARCH_SIZE,
    buildKeywordSearchRequest,
    buildNlpSearchRequest,
} from "./searchRequestBuilders";
import type { SearchQuery } from "../types";

const fileQuery = (over: Partial<SearchQuery> = {}): SearchQuery => ({
    query: "red truck",
    filters: { _rectype: { label: "Files", value: "file" } },
    metadataFilters: [],
    sort: [],
    pagination: { from: 50, size: 25 },
    ...over,
});

// The sidebar state one search can carry, with every clause family present once.
const richFilters = {
    _rectype: { label: "Files", value: "file" as const },
    str_databaseid: { label: "db-a, db-b", value: "db-a", values: ["db-a", "db-b"] },
    str_fileext: { label: ".glb, .obj", value: ".glb", values: [".glb", ".obj"] },
    bool_has_asset_children: { value: true },
    bool_archived: { value: true },
    date_lastmodified_filter: { operator: ">" as const, value: "2026-01-01" },
    num_filesize_filter: { operator: "between" as const, value: [10, 20] },
    geo_filter: { point: { lat: 1, lon: 2, radiusMeters: 500 } },
    showResultExplanation: true,
    includeMetadataInKeywordSearch: false,
};
const richMetadata = [
    { key: "MD_str_color", value: "red", operator: "=" as const, type: "string" as const },
];

describe("buildKeywordSearchRequest", () => {
    it("translates the sidebar state into the SearchRequestModel body", () => {
        const body = buildKeywordSearchRequest(
            fileQuery({
                filters: richFilters,
                metadataFilters: richMetadata,
                sort: [{ field: "str_key", order: "asc" }],
            }),
            "smoke-db",
            "both",
            "OR"
        );
        expect(body).toEqual({
            query: "red truck",
            filters: [
                { query_string: { query: '(str_databaseid:("db-a" OR "db-b"))' } },
                { query_string: { query: '(str_fileext:(".glb" OR ".obj"))' } },
                { query_string: { query: "(bool_has_asset_children:true)" } },
                { query_string: { query: "date_lastmodified:>=2026-01-01" } },
                { query_string: { query: "num_filesize:[10 TO 20]" } },
                { query_string: { query: 'str_databaseid.keyword:"smoke-db"' } },
            ],
            sort: [{ field: "str_key", order: "asc" }],
            from: 50,
            size: 25,
            entityTypes: ["file"],
            metadataQuery: "MD_str_color:red",
            metadataSearchMode: "both",
            includeMetadataInSearch: false,
            aggregations: true,
            includeHighlights: true,
            explainResults: true,
            includeArchived: true,
            geoSearch: { point: { lat: 1, lon: 2, radiusMeters: 500 } },
        });
    });

    it("sends no filters, no query and both entity types for an untouched sidebar", () => {
        const body = buildKeywordSearchRequest(
            fileQuery({ query: "", filters: {} }),
            undefined,
            undefined,
            undefined
        );
        expect(body.query).toBeUndefined();
        expect(body.filters).toBeUndefined();
        expect(body.entityTypes).toEqual(["asset", "file"]);
        expect(body.metadataSearchMode).toBe("both");
        expect(body.includeArchived).toBe(false);
    });

    it("joins metadata keys only in key mode and values only in value mode", () => {
        const keyBody = buildKeywordSearchRequest(
            fileQuery({
                metadataFilters: [{ key: "str_color", value: "x", operator: "=", type: "string" }],
            }),
            undefined,
            "key",
            "AND"
        );
        expect(keyBody.metadataQuery).toBe("MD_str_color");
        const valueBody = buildKeywordSearchRequest(
            fileQuery({
                metadataFilters: [
                    { key: "a", value: "red", operator: "=", type: "string" },
                    { key: "b", value: "blue", operator: "=", type: "string" },
                ],
            }),
            undefined,
            "value",
            "OR"
        );
        expect(valueBody.metadataQuery).toBe("red OR blue");
    });

    it("treats the `Search inside files` flag as a UI flag, never a query_string clause", () => {
        const body = buildKeywordSearchRequest(
            fileQuery({ filters: { ...richFilters, includeSegments: true } }),
            undefined,
            undefined,
            undefined
        );
        expect(JSON.stringify(body.filters)).not.toContain("includeSegments");
        expect(body).not.toHaveProperty("includeSegments");
    });
});

describe("buildNlpSearchRequest", () => {
    it("carries database, extension and archived choices as dedicated fields and asks for 100 candidates", () => {
        const body = buildNlpSearchRequest(
            fileQuery({
                query: "  red truck  ",
                filters: richFilters,
                metadataFilters: richMetadata,
            }),
            {
                includeOpenSearchConstraints: false,
                metadataSearchMode: "both",
                metadataOperator: "OR",
            }
        );
        expect(body).toEqual({
            query: "red truck",
            size: NLP_SEARCH_SIZE,
            entityTypes: ["file"],
            databaseIds: ["db-a", "db-b"],
            fileExtensions: [".glb", ".obj"],
            includeArchived: true,
        });
        expect(NLP_SEARCH_SIZE).toBe(100);
    });

    it("adds the OpenSearch-only constraints only when asked, without duplicating the dedicated fields", () => {
        const body = buildNlpSearchRequest(
            fileQuery({ filters: richFilters, metadataFilters: richMetadata }),
            {
                includeOpenSearchConstraints: true,
                metadataSearchMode: "both",
                metadataOperator: "OR",
            }
        );
        expect(body.filters).toEqual([
            { query_string: { query: "(bool_has_asset_children:true)" } },
            { query_string: { query: "date_lastmodified:>=2026-01-01" } },
            { query_string: { query: "num_filesize:[10 TO 20]" } },
        ]);
        expect(body.metadataQuery).toBe("MD_str_color:red");
        expect(body.metadataSearchMode).toBe("both");
        expect(body.geoSearch).toEqual({ point: { lat: 1, lon: 2, radiusMeters: 500 } });
        expect(body.databaseIds).toEqual(["db-a", "db-b"]);
        expect(body.fileExtensions).toEqual([".glb", ".obj"]);
    });

    it("lets a URL-locked database win over the multiselect", () => {
        const body = buildNlpSearchRequest(fileQuery({ filters: richFilters }), {
            databaseId: "locked-db",
            includeOpenSearchConstraints: false,
        });
        expect(body.databaseIds).toEqual(["locked-db"]);
    });

    it("omits every optional field for a bare query in asset mode", () => {
        const body = buildNlpSearchRequest(
            fileQuery({ filters: { _rectype: { label: "Assets", value: "asset" } } }),
            { includeOpenSearchConstraints: true }
        );
        expect(body).toEqual({
            query: "red truck",
            size: 100,
            entityTypes: ["asset"],
            includeArchived: false,
        });
    });

    it("sends includeSegments false only when `Search inside files` is cleared", () => {
        const cleared = buildNlpSearchRequest(
            fileQuery({ filters: { ...richFilters, includeSegments: false } }),
            { includeOpenSearchConstraints: false }
        );
        expect(cleared).toEqual({
            query: "red truck",
            size: 100,
            entityTypes: ["file"],
            databaseIds: ["db-a", "db-b"],
            fileExtensions: [".glb", ".obj"],
            includeArchived: true,
            includeSegments: false,
        });
        // Checked (the default) and never touched both omit the key. `toEqual` would accept an
        // `undefined` value, so the key's absence is asserted directly.
        const checked = buildNlpSearchRequest(
            fileQuery({ filters: { ...richFilters, includeSegments: true } }),
            { includeOpenSearchConstraints: false }
        );
        expect(checked).not.toHaveProperty("includeSegments");
        expect(
            buildNlpSearchRequest(fileQuery(), { includeOpenSearchConstraints: false })
        ).not.toHaveProperty("includeSegments");
    });
});
