/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search table's counter and its "About this count" popover. Totals count only records the
 * caller can open. When the backend can only give a minimum (hits.total.relation "gte") the counter
 * ends in "+" and the popover says why; an exact count gets neither.
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
const exactState = {
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
        aggregationTotal: 1,
    },
    tablePreferences: { pageSize: 10, visibleContent: [] },
};

const lowerBoundState = {
    ...exactState,
    result: {
        ...exactState.result,
        hits: { ...exactState.result.hits, total: { value: 3, relation: "gte" } },
        aggregationTotal: 2,
    },
};

function counterText(container: HTMLElement): string | null | undefined {
    return container.querySelector('[class*="counter"]')?.textContent?.trim();
}

function renderView(state: any) {
    return render(
        <MemoryRouter>
            <SearchPageListView state={state} dispatch={jest.fn()} />
        </MemoryRouter>
    );
}

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Search list view count", () => {
    it("shows a minimum with + and explains it", () => {
        const { container } = renderView(lowerBoundState);

        expect(counterText(container)).toBe("3+");
        fireEvent.click(screen.getByText("\u24D8"));
        expect(screen.getByText("About this count")).toBeInTheDocument();
        expect(screen.getByText(/includes only records you can open/)).toBeInTheDocument();
        expect(screen.getByText(/there may be more/)).toBeInTheDocument();
    });

    it("shows an exact count without + or a popover", () => {
        const { container } = renderView(exactState);

        expect(counterText(container)).toBe("1");
        expect(screen.queryByText("\u24D8")).not.toBeInTheDocument();
    });
});
