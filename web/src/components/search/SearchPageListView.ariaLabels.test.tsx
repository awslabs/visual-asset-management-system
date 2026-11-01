/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search results table names its row checkboxes after the asset name (assets) or the file path
 * (files), names its select-all checkbox, and names its page buttons.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
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

const assetState = {
    ...INITIAL_STATE,
    initialResult: true,
    recordType: "asset",
    result: {
        hits: {
            hits: [
                { _id: "a1", _source: { str_assetname: "Bracket", str_databaseid: "db1" } },
                { _id: "a2", _source: { str_assetname: "Turbine blade", str_databaseid: "db1" } },
            ],
            total: { value: 2 },
        },
    },
    tablePreferences: { pageSize: 10, visibleContent: [] },
};

const fileState = {
    ...assetState,
    recordType: "file",
    filters: { _rectype: { label: "Files", value: "file" } },
    viewerSelectMode: true,
    viewerSelection: [],
    setViewerSelection: jest.fn(),
    result: {
        hits: {
            hits: [
                {
                    _id: "f1",
                    _source: {
                        str_key: "models/part.glb",
                        str_assetname: "Bracket",
                        str_databaseid: "db1",
                    },
                },
            ],
            total: { value: 1 },
        },
    },
};

const mount = (state: any) =>
    render(
        <MemoryRouter>
            <SearchPageListView state={state} dispatch={jest.fn()} onCreateAsset={jest.fn()} />
        </MemoryRouter>
    );

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Search results table accessible names", () => {
    it("names each asset row checkbox after the asset name, and the select-all checkbox", () => {
        mount(assetState);
        expect(screen.getByRole("checkbox", { name: "Select Bracket" })).toBeInTheDocument();
        expect(screen.getByRole("checkbox", { name: "Select Turbine blade" })).toBeInTheDocument();
        expect(screen.getByRole("checkbox", { name: "Select all" })).toBeInTheDocument();
    });

    it("names each file row checkbox after the file path", () => {
        mount(fileState);
        expect(
            screen.getByRole("checkbox", { name: "Select models/part.glb" })
        ).toBeInTheDocument();
    });

    it("names the pagination buttons", () => {
        mount(assetState);
        expect(screen.getByRole("button", { name: "Next page" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Previous page" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Page 1 of all pages" })).toBeInTheDocument();
    });
});
