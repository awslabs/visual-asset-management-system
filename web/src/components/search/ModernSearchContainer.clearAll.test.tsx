/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Clear all filters is offered only when the user has changed the search from the page's defaults
 * (#1076): not on a fresh load, offered after a filter, and gone again once pressed. On a database's
 * page the locked database is the page's scope: it does not count, and Clear all filters keeps it.
 */
import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import ModernSearchContainer from "./ModernSearchContainer";

const mockExecuteSearch = jest.fn();
let mockTopBarProps: any;
let mockSidebarProps: any;

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ featuresEnabled: [] }) },
}));
jest.mock("./hooks/useSearchAPI", () => ({
    useSearchAPI: () => ({ executeSearch: mockExecuteSearch, executeNlpSearch: jest.fn() }),
}));
jest.mock("./hooks/usePreferences", () => ({
    usePreferences: () => ({
        preferences: {
            pageSize: 25,
            viewMode: "table",
            searchMode: "keyword",
            cardSize: "medium",
            showThumbnails: false,
            showMapThumbnails: false,
            assetTableColumns: [],
            fileTableColumns: [],
            sidebarWidth: 400,
        },
        updatePreferences: jest.fn(),
        isLoaded: true,
    }),
}));
jest.mock("../../visualizerPlugin", () => ({
    initializePluginRegistry: () => Promise.resolve(),
}));
jest.mock("./SearchLayout", () => ({
    SearchTopBar: (props: any) => {
        mockTopBarProps = props;
        return null;
    },
    SearchSidebar: (props: any) => {
        mockSidebarProps = props;
        return null;
    },
}));
jest.mock("./SearchResults/CardView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchPageListView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchPageMapView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchNotifications/ToastManager", () => ({ __esModule: true, default: () => null }));

const emptyAnswer = { hits: { hits: [], total: { value: 0, relation: "eq" } } };
const assetType = { label: "glb", value: "glb", values: ["glb"] };

/** Lets the debounced auto-refresh (500 ms) run. */
const settle = async () => {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 700));
    });
};

beforeEach(() => {
    jest.clearAllMocks();
    mockTopBarProps = undefined;
    mockSidebarProps = undefined;
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    mockExecuteSearch.mockResolvedValue(emptyAnswer);
});

afterEach(() => jest.restoreAllMocks());

describe("ModernSearchContainer Clear all filters (#1076)", () => {
    it("is not offered on a fresh load, is offered after a filter, and is gone after it is pressed", async () => {
        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalled());
        await settle();
        expect(mockTopBarProps.hasActiveFilters).toBe(false);

        await act(async () => {
            mockSidebarProps.onFilterChange("str_assettype", assetType);
        });
        expect(mockTopBarProps.hasActiveFilters).toBe(true);

        await act(async () => {
            mockTopBarProps.onClearAll();
        });
        await settle();
        expect(mockTopBarProps.hasActiveFilters).toBe(false);
    });

    it("is not offered for the record type", async () => {
        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalled());
        await act(async () => {
            mockSidebarProps.onRecordTypeChange("file");
        });
        await settle();
        expect(mockTopBarProps.hasActiveFilters).toBe(false);
    });

    it("on a database's page does not count the locked database, and keeps it through Clear all filters", async () => {
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets"]}>
                <Routes>
                    <Route
                        path="/databases/:databaseId/assets"
                        element={<ModernSearchContainer mode="full" databaseId="db1" />}
                    />
                </Routes>
            </MemoryRouter>
        );
        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalled());
        await settle();
        expect(mockSidebarProps.databaseLocked).toBe(true);
        expect(mockSidebarProps.filters.str_databaseid?.value).toBe("db1");
        expect(mockTopBarProps.hasActiveFilters).toBe(false);

        await act(async () => {
            mockSidebarProps.onFilterChange("str_assettype", assetType);
        });
        expect(mockTopBarProps.hasActiveFilters).toBe(true);

        const before = mockExecuteSearch.mock.calls.length;
        await act(async () => {
            mockTopBarProps.onClearAll();
        });
        await settle();
        expect(mockTopBarProps.hasActiveFilters).toBe(false);
        // The locked control still names the database, and the reloaded search is still that database's.
        expect(mockSidebarProps.filters.str_databaseid?.value).toBe("db1");
        expect(mockSidebarProps.filters.str_assettype).toBeUndefined();
        expect(mockExecuteSearch.mock.calls.length).toBeGreaterThan(before);
        const [query, databaseId] =
            mockExecuteSearch.mock.calls[mockExecuteSearch.mock.calls.length - 1];
        expect(databaseId).toBe("db1");
        expect(query.filters.str_databaseid?.value).toBe("db1");
    });
});
