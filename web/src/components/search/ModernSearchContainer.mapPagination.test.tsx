/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Map view pagination used to call a no-op dispatch (`() => {}`), so page changes never
 * re-ran the search. The container must wire the same query-paginate path the table view uses.
 */
import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import ModernSearchContainer from "./ModernSearchContainer";

const mockExecuteSearch = jest.fn();
let mockMapDispatch: ((action: any) => void) | undefined;

jest.mock("../../services/appCache", () => ({
    appCache: {
        getItem: () => ({
            featuresEnabled: ["LOCATIONSERVICES"],
        }),
    },
}));
jest.mock("./hooks/useSearchAPI", () => ({
    useSearchAPI: () => ({ executeSearch: mockExecuteSearch }),
}));
jest.mock("./hooks/usePreferences", () => ({
    usePreferences: () => ({
        preferences: {
            pageSize: 25,
            viewMode: "map",
            cardSize: "medium",
            showThumbnails: true,
            showMapThumbnails: true,
            assetTableColumns: [],
            fileTableColumns: [],
            sidebarWidth: 400,
        },
        updatePreferences: jest.fn(),
        savePreferences: jest.fn(),
        hasUnsavedChanges: false,
        isLoaded: true,
    }),
}));
jest.mock("../../visualizerPlugin", () => ({
    initializePluginRegistry: () => Promise.resolve(),
}));
jest.mock("./SearchLayout", () => ({
    SearchTopBar: () => null,
    SearchSidebar: () => null,
}));
jest.mock("./SearchResults/CardView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchPageListView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchNotifications/ToastManager", () => ({ __esModule: true, default: () => null }));
jest.mock("../../pages/ListPage", () => ({ __esModule: true, default: () => null }));
jest.mock("../../services/APIService", () => ({
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
}));
jest.mock("./SearchPageMapView", () => ({
    __esModule: true,
    default: ({ dispatch }: any) => {
        mockMapDispatch = dispatch;
        return null;
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
    mockMapDispatch = undefined;
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    mockExecuteSearch.mockResolvedValue({ hits: { hits: [], total: { value: 0 } } });
});

afterEach(() => jest.restoreAllMocks());

describe("ModernSearchContainer map pagination", () => {
    it("re-runs the search when map view dispatches query-paginate", async () => {
        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" allowedViews={["map"]} />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockMapDispatch).toBeDefined());
        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalled());
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 700));
        });
        const before = mockExecuteSearch.mock.calls.length;

        await act(async () => {
            mockMapDispatch!({
                type: "query-paginate",
                pagination: { from: 25, size: 25 },
            });
        });

        await waitFor(() => expect(mockExecuteSearch.mock.calls.length).toBe(before + 1));
        expect(mockExecuteSearch.mock.calls[before][0]).toEqual(
            expect.objectContaining({
                pagination: expect.objectContaining({ from: 25, size: 25 }),
            })
        );
    });
});
