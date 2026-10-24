/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The "About this count" popover beside the search table's counter. The counter shows
 * aggregationTotal when the response carries one, and that total counts every matching record in the
 * caller's readable databases before each record is checked, so the popover says the table can list
 * fewer records than the count.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
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
// A renamed Database entity, so the popover is shown to read the deployment's own noun.
jest.mock("../../synonyms", () => ({
    __esModule: true,
    default: { ...jest.requireActual("../../synonyms").default, databases: "libraries" },
}));

const state = {
    ...INITIAL_STATE,
    initialResult: true,
    recordType: "asset",
    result: {
        hits: {
            hits: [
                {
                    _id: "a1",
                    _index: "assets",
                    _source: { str_assetid: "a1", str_databaseid: "db1", str_assetname: "A1" },
                },
            ],
            total: { value: 1, relation: "eq" },
        },
        aggregationTotal: 3,
    },
    tablePreferences: { pageSize: 10, visibleContent: [] },
};

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Search list view count popover", () => {
    it("says the count can be higher than the records the table lists", () => {
        render(
            <MemoryRouter>
                <SearchPageListView state={state} dispatch={jest.fn()} />
            </MemoryRouter>
        );

        fireEvent.click(screen.getByText("\u24D8"));

        expect(screen.getByText("About this count")).toBeInTheDocument();
        expect(
            screen.getByText(/every matching record in the libraries you can access/)
        ).toBeInTheDocument();
        expect(
            screen.getByText(/counted before each record's own permissions are checked/)
        ).toBeInTheDocument();
        expect(screen.getByText(/can show fewer than this count/)).toBeInTheDocument();
    });
});
