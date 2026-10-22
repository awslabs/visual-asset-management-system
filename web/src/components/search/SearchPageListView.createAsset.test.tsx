/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The list view's Create Asset action. The search container owns where the action leads (the upload
 * page for the database a scoped search is on), so the list view calls the container's handler, as
 * the card view does, and opens the plain upload page only when none is given.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import SearchPageListView from "./SearchPageListView";
import { INITIAL_STATE } from "./SearchPageTypes";
import Synonyms from "../../synonyms";

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
    result: { hits: { hits: [], total: { value: 0 } } },
    tablePreferences: { pageSize: 10, visibleContent: [] },
};

function Location() {
    const location = useLocation();
    return <div data-testid="location">{location.pathname}</div>;
}

const mount = (onCreateAsset?: () => void) =>
    render(
        <MemoryRouter initialEntries={["/databases/db1/assets"]}>
            <Routes>
                <Route
                    path="*"
                    element={
                        <>
                            <SearchPageListView
                                state={state}
                                dispatch={jest.fn()}
                                onCreateAsset={onCreateAsset}
                            />
                            <Location />
                        </>
                    }
                />
            </Routes>
        </MemoryRouter>
    );

const createAsset = () =>
    fireEvent.click(screen.getByRole("button", { name: `Create ${Synonyms.Asset}` }));

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Search list view Create Asset", () => {
    it("calls the container's handler", () => {
        const onCreateAsset = jest.fn();
        mount(onCreateAsset);
        createAsset();
        expect(onCreateAsset).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId("location")).toHaveTextContent("/databases/db1/assets");
    });

    it("opens the upload page when no handler is given", () => {
        mount();
        createAsset();
        expect(screen.getByTestId("location")).toHaveTextContent(/^\/upload$/);
    });
});
