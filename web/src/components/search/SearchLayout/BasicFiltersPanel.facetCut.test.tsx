/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A facet whose bucket list is cut (#1283): the answer counted records of values it did not return,
 * in `sum_other_doc_count`. The Database filter lists every database from the database API, so a
 * database without a bucket shows no count then, rather than 0; its records may be past the cut. The
 * type and tag lists come from the buckets, so they say they hold the most common values only. A
 * count of 0, or none reported, is a complete list.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import BasicFiltersPanel from "./BasicFiltersPanel";
import { fetchAllDatabases, fetchTags } from "../../../services/APIService";
import type { SearchFilters } from "../types";

jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
    fetchTags: jest.fn(),
}));

const assetMode: SearchFilters = { _rectype: { label: "Assets", value: "asset" } };
const fileMode: SearchFilters = { _rectype: { label: "Files", value: "file" } };

type Buckets = Array<{ key: string; doc_count: number }>;
const facet = (buckets: Buckets, other?: number | null) => ({
    buckets,
    ...(other === undefined ? {} : { sum_other_doc_count: other }),
});
const answer = (aggregations: Record<string, any>) => ({
    hits: { total: { value: 0, relation: "eq" }, hits: [] },
    aggregations,
});

const field = (container: HTMLElement, label: string) => {
    const found = createWrapper(container)
        .findAllFormFields()
        .find((f) => f.findLabel()?.getElement().textContent === label);
    if (!found) throw new Error(`${label} field not rendered`);
    return found;
};

const optionLabels = (container: HTMLElement, label: string): (string | null)[] => {
    const multiselect = field(container, label).findControl()?.findMultiselect();
    if (!multiselect) throw new Error(`${label} multiselect not rendered`);
    multiselect.openDropdown();
    const labels = multiselect
        .findDropdown()
        .findOptions()
        .map((option) => option.findLabel().getElement().textContent);
    multiselect.closeDropdown();
    return labels;
};

const note = (container: HTMLElement, label: string) =>
    field(container, label).findConstraint()?.getElement().textContent ?? null;

const renderPanel = (filters: SearchFilters, recordType: "asset" | "file", searchResult: any) =>
    render(
        <BasicFiltersPanel
            filters={filters}
            onFilterChange={jest.fn()}
            recordType={recordType}
            searchResult={searchResult}
        />
    );

describe("BasicFiltersPanel with a cut facet (#1283)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAllDatabases as jest.Mock).mockResolvedValue([
            { databaseId: "db-a" },
            { databaseId: "db-b" },
        ]);
        (fetchTags as jest.Mock).mockResolvedValue([]);
    });

    it("shows no count for a database without a bucket when the database facet is cut", async () => {
        const { container } = renderPanel(
            assetMode,
            "asset",
            answer({ str_databaseid: facet([{ key: "db-a", doc_count: 5 }], 12) })
        );
        await waitFor(() => expect(optionLabels(container, "Database")).toHaveLength(2));
        expect(optionLabels(container, "Database")).toEqual(["db-a (5 results)", "db-b"]);
    });

    it.each([
        ["0", 0],
        ["null", null],
        ["not reported", undefined],
    ])(
        "counts 0 for a database without a bucket when sum_other_doc_count is %s",
        async (_, other) => {
            const { container } = renderPanel(
                assetMode,
                "asset",
                answer({ str_databaseid: facet([{ key: "db-a", doc_count: 5 }], other) })
            );
            await waitFor(() => expect(optionLabels(container, "Database")).toHaveLength(2));
            expect(optionLabels(container, "Database")).toEqual([
                "db-a (5 results)",
                "db-b (0 results)",
            ]);
        }
    );

    it("says the tag list holds the most common tags only when the tag facet is cut", async () => {
        const { container } = renderPanel(
            fileMode,
            "file",
            answer({ list_tags: facet([{ key: "engine", doc_count: 9 }], 40) })
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(note(container, "Tags")).toBe("Only the most common tags are listed.");
        expect(optionLabels(container, "Tags")).toEqual(["engine (9)"]);
    });

    it("says the same of the asset type and file type lists", async () => {
        const assets = renderPanel(
            assetMode,
            "asset",
            answer({ str_assettype: facet([{ key: "glb", doc_count: 3 }], 7) })
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(note(assets.container, "Asset Type")).toBe("Only the most common types are listed.");
        assets.unmount();

        const files = renderPanel(
            fileMode,
            "file",
            answer({ str_fileext: facet([{ key: ".glb", doc_count: 3 }], 7) })
        );
        expect(note(files.container, "File Type")).toBe("Only the most common types are listed.");
    });

    it.each([
        ["0", 0],
        ["null", null],
        ["not reported", undefined],
    ])("adds no note when sum_other_doc_count is %s", async (_, other) => {
        const { container } = renderPanel(
            fileMode,
            "file",
            answer({
                list_tags: facet([{ key: "engine", doc_count: 9 }], other),
                str_fileext: facet([{ key: ".glb", doc_count: 3 }], other),
            })
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(note(container, "Tags")).toBeNull();
        expect(note(container, "File Type")).toBeNull();
    });

    it("keeps the note while a filter narrows the answer and the list still offers the cut search's tags", async () => {
        const { container, rerender } = renderPanel(
            fileMode,
            "file",
            answer({
                list_tags: facet(
                    [
                        { key: "engine", doc_count: 9 },
                        { key: "gear", doc_count: 4 },
                    ],
                    40
                ),
            })
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());

        // A tag filter narrows the answer to "engine", whose own tag list is complete.
        rerender(
            <BasicFiltersPanel
                filters={{
                    ...fileMode,
                    list_tags: { label: "engine", value: "engine", values: ["engine"] },
                }}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={answer({ list_tags: facet([{ key: "engine", doc_count: 9 }], 0) })}
            />
        );
        expect(optionLabels(container, "Tags")).toEqual(["engine (9)", "gear"]);
        expect(note(container, "Tags")).toBe("Only the most common tags are listed.");
    });

    it("adds no note to a narrowed answer when the cached search's list was complete", async () => {
        const { container, rerender } = renderPanel(
            fileMode,
            "file",
            answer({ list_tags: facet([{ key: "engine", doc_count: 9 }], 0) })
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        rerender(
            <BasicFiltersPanel
                filters={{
                    ...fileMode,
                    list_tags: { label: "engine", value: "engine", values: ["engine"] },
                }}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={answer({ list_tags: facet([{ key: "engine", doc_count: 9 }], 0) })}
            />
        );
        expect(note(container, "Tags")).toBeNull();
    });
});
