/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
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

const fileMode: SearchFilters = { _rectype: { label: "Files", value: "file" } };

/** An envelope carrying only OpenSearch-style terms buckets, as both search routes return them. */
const withFileTypes = (buckets: Array<{ key: string; doc_count: number }>) => ({
    hits: { total: { value: 0, relation: "eq" }, hits: [] },
    aggregations: { str_fileext: { buckets } },
});

const fileTypeLabels = (container: HTMLElement): (string | null)[] => {
    const field = createWrapper(container)
        .findAllFormFields()
        .find((f) => f.findLabel()?.getElement().textContent === "File Type");
    const multiselect = field?.findControl()?.findMultiselect();
    if (!multiselect) throw new Error("File Type multiselect not rendered");
    multiselect.openDropdown();
    const labels = multiselect
        .findDropdown()
        .findOptions()
        .map((option) => option.findLabel().getElement().textContent);
    multiselect.closeDropdown();
    return labels;
};

describe("BasicFiltersPanel file-type options", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAllDatabases as jest.Mock).mockResolvedValue([]);
        (fetchTags as jest.Mock).mockResolvedValue([]);
    });

    it("lists the file types of the current answer", async () => {
        const { container } = render(
            <BasicFiltersPanel
                filters={fileMode}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={withFileTypes([
                    { key: "txt", doc_count: 2 },
                    { key: "glb", doc_count: 1 },
                ])}
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(fileTypeLabels(container)).toEqual(["glb (1)", "txt (2)"]);
    });

    it("treats `Search inside files` as a search option, not a filter, so the answer's types are listed", async () => {
        // An earlier unfiltered search filled the type cache with its own buckets.
        const { container, rerender } = render(
            <BasicFiltersPanel
                filters={fileMode}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={withFileTypes([{ key: "pdf", doc_count: 7 }])}
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        // A natural-language answer with `Search inside files` cleared is still unfiltered: its
        // own types are offered, and they replace the cached ones.
        for (const includeSegments of [false, true]) {
            rerender(
                <BasicFiltersPanel
                    filters={{ ...fileMode, includeSegments }}
                    onFilterChange={jest.fn()}
                    recordType="file"
                    searchResult={withFileTypes([
                        { key: "txt", doc_count: 2 },
                        { key: "glb", doc_count: 1 },
                    ])}
                />
            );
            expect(fileTypeLabels(container)).toEqual(["glb (1)", "txt (2)"]);
        }
    });

    it("keeps offering the cached types once a file type narrows the answer, with the narrowed answer's counts", async () => {
        const onFilterChange = jest.fn();
        const { container, rerender } = render(
            <BasicFiltersPanel
                filters={fileMode}
                onFilterChange={onFilterChange}
                recordType="file"
                searchResult={withFileTypes([
                    { key: "txt", doc_count: 2 },
                    { key: "glb", doc_count: 1 },
                ])}
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        rerender(
            <BasicFiltersPanel
                filters={{
                    ...fileMode,
                    str_fileext: { label: "txt", value: "txt", values: ["txt"] },
                }}
                onFilterChange={onFilterChange}
                recordType="file"
                searchResult={withFileTypes([{ key: "txt", doc_count: 2 }])}
            />
        );
        // glb is still offered, without the unfiltered search's count: the answer shown has none.
        expect(fileTypeLabels(container)).toEqual(["glb", "txt (2)"]);
    });

    it("shows the filtered answer's counts on the cached options, not the unfiltered search's", async () => {
        // An unfiltered file search counts 75 usda files and 40 glb files.
        const { container, rerender } = render(
            <BasicFiltersPanel
                filters={fileMode}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={withFileTypes([
                    { key: "usda", doc_count: 75 },
                    { key: "glb", doc_count: 40 },
                ])}
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(fileTypeLabels(container)).toEqual(["glb (40)", "usda (75)"]);

        // A database filter narrows the answer to 32 usda files and no glb file.
        rerender(
            <BasicFiltersPanel
                filters={{
                    ...fileMode,
                    str_databaseid: { label: "db-a", value: "db-a", values: ["db-a"] },
                }}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={withFileTypes([{ key: "usda", doc_count: 32 }])}
            />
        );
        expect(fileTypeLabels(container)).toEqual(["glb", "usda (32)"]);
    });

    it("does the same for tags, whose bucket keys can join several values", async () => {
        const withTags = (buckets: Array<{ key: string; doc_count: number }>) => ({
            hits: { total: { value: 0, relation: "eq" }, hits: [] },
            aggregations: { list_tags: { buckets } },
        });
        const tagLabels = (root: HTMLElement) => {
            const field = createWrapper(root)
                .findAllFormFields()
                .find((f) => f.findLabel()?.getElement().textContent === "Tags");
            const multiselect = field?.findControl()?.findMultiselect();
            if (!multiselect) throw new Error("Tags multiselect not rendered");
            multiselect.openDropdown();
            const found = multiselect
                .findDropdown()
                .findOptions()
                .map((option) => option.findLabel().getElement().textContent);
            multiselect.closeDropdown();
            return found;
        };
        const { container, rerender } = render(
            <BasicFiltersPanel
                filters={fileMode}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={withTags([
                    { key: "engine", doc_count: 9 },
                    { key: "gear, shaft", doc_count: 4 },
                ])}
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(tagLabels(container)).toEqual(["engine (9)", "gear (4)", "shaft (4)"]);

        rerender(
            <BasicFiltersPanel
                filters={{ ...fileMode, list_tags: { label: "engine", value: "engine" } }}
                onFilterChange={jest.fn()}
                recordType="file"
                searchResult={withTags([{ key: "engine", doc_count: 3 }])}
            />
        );
        expect(tagLabels(container)).toEqual(["engine (3)", "gear", "shaft"]);
    });
});
