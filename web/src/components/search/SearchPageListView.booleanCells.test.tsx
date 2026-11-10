/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A bool_ results column shows each value as a read-only checkbox named after the column header and
 * the value ("Archived: yes"), so a screen reader can tell what each box states (#1301). The boxes
 * had no name, which axe reports as a critical `label` violation on every row.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { MemoryRouter } from "react-router-dom";
import SearchPageListView from "./SearchPageListView";
import { INITIAL_STATE } from "./SearchPageTypes";

jest.mock("../../services/APIService", () => ({
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
}));
jest.mock("../filemanager/modals/FileViewerModal", () => ({
    __esModule: true,
    default: () => null,
}));
// The viewer registry pulls in Vite's import.meta.glob, so the registry modules are stubbed.
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    areFilenamesViewableTogether: () => false,
    areFilesComparableTogether: () => false,
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));

const state = {
    ...INITIAL_STATE,
    initialResult: true,
    recordType: "asset",
    result: {
        hits: {
            hits: [
                {
                    _id: "a1",
                    _source: {
                        str_assetname: "Bracket",
                        str_databaseid: "db1",
                        bool_archived: true,
                        bool_isdistributable: false,
                    },
                },
                {
                    _id: "a2",
                    _source: {
                        str_assetname: "Turbine blade",
                        str_databaseid: "db1",
                        bool_archived: false,
                        bool_isdistributable: true,
                    },
                },
            ],
            total: { value: 2 },
        },
    },
    tablePreferences: {
        pageSize: 10,
        visibleContent: ["str_assetname", "bool_archived", "bool_isdistributable"],
    },
};

const mount = () =>
    render(
        <MemoryRouter>
            <SearchPageListView state={state} dispatch={jest.fn()} onCreateAsset={jest.fn()} />
        </MemoryRouter>
    );

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Search results boolean cells", () => {
    it("names each checkbox after its column header and its value", () => {
        mount();
        const archivedYes = screen.getByRole("checkbox", { name: "Archived: yes" });
        const archivedNo = screen.getByRole("checkbox", { name: "Archived: no" });
        expect(archivedYes).toBeChecked();
        expect(archivedNo).not.toBeChecked();
        expect(screen.getByRole("checkbox", { name: "Distributable: yes" })).toBeChecked();
        expect(screen.getByRole("checkbox", { name: "Distributable: no" })).not.toBeChecked();
    });

    it("uses the header text the table shows", () => {
        const { container } = mount();
        const table = createWrapper(container).findTable();
        expect(table).not.toBeNull();
        const headers = (table?.findColumnHeaders() ?? []).map((header) =>
            header.getElement().textContent?.trim()
        );
        expect(headers).toEqual(expect.arrayContaining(["Archived", "Distributable"]));
        const archivedColumn = headers.indexOf("Archived") + 1;
        const cell = table?.findBodyCell(1, archivedColumn)?.getElement();
        expect(cell?.querySelector("input[type=checkbox]")).toHaveAttribute(
            "aria-label",
            "Archived: yes"
        );
    });

    it("leaves no checkbox in the table without a name", () => {
        mount();
        for (const box of screen.getAllByRole("checkbox")) {
            expect(box).toHaveAccessibleName();
        }
    });
});
