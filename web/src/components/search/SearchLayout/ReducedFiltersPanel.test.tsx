/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import ReducedFiltersPanel, { fileTypeOptionsFromHits } from "./ReducedFiltersPanel";
import { fetchAllDatabases } from "../../../services/APIService";
import type { SearchResult } from "../types";

jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
}));

const hits: SearchResult[] = [
    { _id: "1", _source: { str_fileext: ".glb" }, _vector: { fileClass: "mesh" } as any },
    { _id: "2", _source: { str_fileext: ".glb" }, _vector: { fileClass: "mesh" } as any },
    { _id: "3", _source: { str_fileext: ".laz" }, _vector: { fileClass: "pointcloud" } as any },
    { _id: "4", _source: {} },
];

describe("fileTypeOptionsFromHits", () => {
    it("counts each extension once per hit and names the file class", () => {
        expect(fileTypeOptionsFromHits(hits)).toEqual([
            { label: ".glb — mesh (2)", value: ".glb" },
            { label: ".laz — pointcloud (1)", value: ".laz" },
        ]);
    });

    it("returns nothing for an empty result set", () => {
        expect(fileTypeOptionsFromHits([])).toEqual([]);
    });
});

describe("ReducedFiltersPanel", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAllDatabases as jest.Mock).mockResolvedValue([
            { databaseId: "zoo-db" },
            { databaseId: "alpha-db" },
        ]);
    });

    it("offers every database, alphabetized, and writes the multiselect as str_databaseid values", async () => {
        const onFilterChange = jest.fn();
        const { container } = render(
            <ReducedFiltersPanel filters={{}} onFilterChange={onFilterChange} recordType="file" />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        const databases = createWrapper(container).findAllMultiselects()[0];
        // Open once, then wait on the options: `openDropdown()` clicks the trigger, so calling it
        // inside a `waitFor` would toggle the list closed/open on every retry. The open dropdown
        // re-renders when the `options` prop changes after `fetchAllDatabases` resolves.
        databases.openDropdown();
        await waitFor(() => expect(databases.findDropdown().findOptions()).toHaveLength(2));
        const labels = databases
            .findDropdown()
            .findOptions()
            .map((option) => option.getElement().textContent);
        expect(labels).toEqual(["alpha-db", "zoo-db"]);
        databases.selectOption(1);
        expect(onFilterChange).toHaveBeenCalledWith("str_databaseid", {
            label: "alpha-db",
            value: "alpha-db",
            values: ["alpha-db"],
        });
    });

    it("locks the database control when the URL fixes the database", async () => {
        const { container } = render(
            <ReducedFiltersPanel
                filters={{}}
                onFilterChange={jest.fn()}
                recordType="file"
                databaseLocked
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(createWrapper(container).findAllMultiselects()[0].isDisabled()).toBe(true);
        expect(screen.getByText("Locked by URL parameter")).toBeInTheDocument();
    });

    it("derives the file-type facet from the current hits and writes str_fileext values", async () => {
        const onFilterChange = jest.fn();
        const { container } = render(
            <ReducedFiltersPanel
                filters={{}}
                onFilterChange={onFilterChange}
                recordType="file"
                searchResult={{ hits: { total: { value: 4, relation: "eq" }, hits } }}
            />
        );
        const fileTypes = createWrapper(container).findAllMultiselects()[1];
        fileTypes.openDropdown();
        expect(
            fileTypes
                .findDropdown()
                .findOptions()
                .map((option) => option.getElement().textContent)
        ).toEqual([".glb — mesh (2)", ".laz — pointcloud (1)"]);
        fileTypes.selectOption(2);
        expect(onFilterChange).toHaveBeenCalledWith("str_fileext", {
            label: ".laz",
            value: ".laz",
            values: [".laz"],
        });
    });

    it("hides the file-type facet in asset mode", () => {
        const { container } = render(
            <ReducedFiltersPanel filters={{}} onFilterChange={jest.fn()} recordType="asset" />
        );
        expect(createWrapper(container).findAllMultiselects()).toHaveLength(1);
        expect(screen.queryByText("File Type")).toBeNull();
    });

    it("toggles bool_archived", async () => {
        const onFilterChange = jest.fn();
        render(
            <ReducedFiltersPanel filters={{}} onFilterChange={onFilterChange} recordType="file" />
        );
        await userEvent.click(screen.getByLabelText("Include archived items"));
        expect(onFilterChange).toHaveBeenCalledWith("bool_archived", { value: true });
    });
});
