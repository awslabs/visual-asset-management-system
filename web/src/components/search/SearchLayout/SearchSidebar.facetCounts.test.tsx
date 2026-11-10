/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Facet counts beside a count that is a minimum ("132+") read as minimums too ("100+"): a
 * natural-language answer's, and a keyword answer's whose total some permission rules make a lower
 * bound. Beside an exact count they keep their counts as they are.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import SearchSidebar from "./SearchSidebar";
import { fetchAllDatabases, fetchTags } from "../../../services/APIService";
import type { SearchFilters, SearchMode, SearchPreferences, SearchResult } from "../types";

jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
    fetchTags: jest.fn(),
}));
// Panels that do not render facet counts.
jest.mock("./AdvancedFiltersPanel", () => ({ __esModule: true, default: () => null }));
jest.mock("./MetadataSearchPanel", () => ({ __esModule: true, default: () => null }));
jest.mock("./PreferencesPanel", () => ({ __esModule: true, default: () => null }));
jest.mock("./GeoFilterPanel", () => ({ __esModule: true, default: () => null }));

const fileMode: SearchFilters = { _rectype: { label: "Files", value: "file" } };

const fileHit = (index: number, databaseId: string): SearchResult => ({
    _id: `${databaseId}#a#/g${index}.glb#v1`,
    _score: 1 - index / 1000,
    _source: { str_databaseid: databaseId, str_fileext: "glb", str_key: `/g${index}.glb` },
});

/**
 * A natural-language answer for a user limited to two databases (files): the route counted 132
 * readable matches, its window filled (gte), its facets count 100 + 32, and it returned 100 hits.
 */
const restrictedAnswer = (relation: "eq" | "gte", total = 132, returned = 100) => ({
    hits: {
        total: { value: total, relation },
        hits: Array.from({ length: returned }, (_, i) => fileHit(i, i < 70 ? "db-a" : "db-b")),
    },
    aggregations: {
        str_databaseid: {
            buckets: [
                { key: "db-a", doc_count: Math.round((total * 100) / 132) },
                { key: "db-b", doc_count: total - Math.round((total * 100) / 132) },
            ],
        },
        str_fileext: { buckets: [{ key: "glb", doc_count: total }] },
    },
    aggregationTotal: total,
    nlp: {
        query: "gearbox",
        embeddingModelId: "m",
        databasesSearched: 2,
        candidatesEvaluated: total,
        itemsCollapsed: 0,
        classIntent: [],
        truncated: relation === "gte",
    },
    warnings: [],
});

/** A keyword answer: 93 known-readable files in db-a and none counted in db-b. */
const keywordAnswer = (relation: "eq" | "gte") => ({
    hits: { total: { value: 2188, relation }, hits: [] },
    aggregations: {
        str_databaseid: { buckets: [{ key: "db-a", doc_count: 93 }] },
        str_fileext: { buckets: [{ key: "glb", doc_count: 93 }] },
    },
    aggregationTotal: 2188,
});

const labels = (container: HTMLElement, field: string): (string | null)[] => {
    const formField = createWrapper(container)
        .findAllFormFields()
        .find((f) => f.findLabel()?.getElement().textContent === field);
    const multiselect = formField?.findControl()?.findMultiselect();
    if (!multiselect) throw new Error(`${field} multiselect not rendered`);
    multiselect.openDropdown();
    const found = multiselect
        .findDropdown()
        .findOptions()
        .map((option) => option.findLabel().getElement().textContent);
    multiselect.closeDropdown();
    return found;
};

const renderSidebar = (searchResult: any, searchMode: SearchMode, reduced = false) =>
    render(
        <SearchSidebar
            recordType="file"
            onRecordTypeChange={jest.fn()}
            filters={fileMode}
            onFilterChange={jest.fn()}
            metadataFilters={[]}
            onAddMetadataFilter={jest.fn()}
            onRemoveMetadataFilter={jest.fn()}
            onUpdateMetadataFilter={jest.fn()}
            preferences={{} as SearchPreferences}
            onPreferencesChange={jest.fn()}
            searchResult={searchResult}
            showThumbnails={false}
            onThumbnailToggle={jest.fn()}
            searchMode={searchMode}
            reduced={reduced}
        />
    );

describe("SearchSidebar facet counts", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAllDatabases as jest.Mock).mockResolvedValue([
            { databaseId: "db-a" },
            { databaseId: "db-b" },
        ]);
        (fetchTags as jest.Mock).mockResolvedValue([]);
    });

    it("shows a natural-language answer's facet counts as minimums when its count is one", async () => {
        const { container } = renderSidebar(restrictedAnswer("gte"), "nlp");
        await waitFor(() => expect(labels(container, "Database")).toHaveLength(2));
        expect(labels(container, "Database")).toEqual([
            "db-a (100+ results)",
            "db-b (32+ results)",
        ]);
        expect(labels(container, "File Type")).toEqual(["glb (132+)"]);
    });

    it("shows the facet counts as minimums when no window filled, as the route reports the total", async () => {
        // Returned every match it counted and truncated is false, yet the matching is approximate,
        // so the route says gte and the facets carry "+" for the same reason.
        const answer = { ...restrictedAnswer("gte", 66, 66) };
        answer.nlp = { ...answer.nlp, truncated: false };
        const { container } = renderSidebar(answer, "nlp");
        await waitFor(() => expect(labels(container, "Database")).toHaveLength(2));
        expect(labels(container, "Database")).toEqual(["db-a (50+ results)", "db-b (16+ results)"]);
        expect(labels(container, "File Type")).toEqual(["glb (66+)"]);
    });

    it("keeps the counts as they are when the route reports its total as exact", async () => {
        // The relation is the route's decision; an exact answer (as the route sends for zero) has no "+".
        const { container } = renderSidebar(restrictedAnswer("eq", 66, 66), "nlp");
        await waitFor(() => expect(labels(container, "Database")).toHaveLength(2));
        expect(labels(container, "Database")).toEqual(["db-a (50 results)", "db-b (16 results)"]);
    });

    it("shows a keyword answer's facet counts as minimums when its total is a lower bound", async () => {
        // The buckets count the records known to be readable, so they can be low, as the total is;
        // a database with no counted bucket is a minimum too, not an empty database.
        const { container } = renderSidebar(keywordAnswer("gte"), "keyword");
        await waitFor(() => expect(labels(container, "Database")).toHaveLength(2));
        expect(labels(container, "Database")).toEqual(["db-a (93+ results)", "db-b (0+ results)"]);
        expect(labels(container, "File Type")).toEqual(["glb (93+)"]);
    });

    it("keeps a keyword answer's facet counts as they are when its total is exact", async () => {
        const { container } = renderSidebar(keywordAnswer("eq"), "keyword");
        await waitFor(() => expect(labels(container, "Database")).toHaveLength(2));
        expect(labels(container, "Database")).toEqual(["db-a (93 results)", "db-b (0 results)"]);
        expect(labels(container, "File Type")).toEqual(["glb (93)"]);
    });

    it("labels a database with exactly one match in the singular, and a minimum of one in the plural (#911)", async () => {
        // Keyword: an exact count of one reads "1 result", as the badge and the announcement do.
        const keyword = {
            ...keywordAnswer("eq"),
            aggregations: {
                ...keywordAnswer("eq").aggregations,
                str_databaseid: {
                    buckets: [
                        { key: "db-a", doc_count: 1 },
                        { key: "db-b", doc_count: 2 },
                    ],
                },
            },
        };
        const first = renderSidebar(keyword, "keyword");
        await waitFor(() => expect(labels(first.container, "Database")).toHaveLength(2));
        expect(labels(first.container, "Database")).toEqual([
            "db-a (1 result)",
            "db-b (2 results)",
        ]);
        first.unmount();

        // Natural language: a count of one that is a minimum stays plural, as "Found 1+ results" reads.
        const nlp = restrictedAnswer("gte", 3, 3);
        nlp.aggregations.str_databaseid.buckets = [
            { key: "db-a", doc_count: 1 },
            { key: "db-b", doc_count: 2 },
        ];
        const second = renderSidebar(nlp, "nlp");
        await waitFor(() => expect(labels(second.container, "Database")).toHaveLength(2));
        expect(labels(second.container, "Database")).toEqual([
            "db-a (1+ results)",
            "db-b (2+ results)",
        ]);
    });

    it("marks the reduced panel's file-type counts the same way", async () => {
        const { container } = renderSidebar(restrictedAnswer("gte"), "nlp", true);
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(labels(container, "File Type")).toEqual(["glb (100+)"]);
    });
});
