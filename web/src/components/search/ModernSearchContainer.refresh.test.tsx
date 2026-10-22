/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search container handles the table's "refresh" action by re-running its current search
 * through the search API (SearchPageListView dispatches it after an archive, delete or unarchive).
 */
import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ModernSearchContainer from "./ModernSearchContainer";

const mockExecuteSearch = jest.fn();
let mockListDispatch: ((action: any) => void) | undefined;

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ featuresEnabled: [] }) },
}));
jest.mock("./hooks/useSearchAPI", () => ({
    useSearchAPI: () => ({ executeSearch: mockExecuteSearch }),
}));
jest.mock("../../visualizerPlugin", () => ({
    initializePluginRegistry: () => Promise.resolve(),
}));
jest.mock("./SearchLayout", () => ({
    SearchTopBar: () => null,
    SearchSidebar: () => null,
}));
jest.mock("./SearchResults/CardView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchPageMapView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchNotifications/ToastManager", () => ({ __esModule: true, default: () => null }));
jest.mock("../../pages/ListPage", () => ({ __esModule: true, default: () => null }));
jest.mock("../../services/APIService", () => ({
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
}));
jest.mock("./SearchPageListView", () => ({
    __esModule: true,
    default: ({ dispatch }: any) => {
        mockListDispatch = dispatch;
        return null;
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
    mockListDispatch = undefined;
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    mockExecuteSearch.mockResolvedValue({ hits: { hits: [], total: { value: 0 } } });
});

afterEach(() => jest.restoreAllMocks());

describe("ModernSearchContainer refresh", () => {
    it("re-runs the current search through the search API", async () => {
        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" allowedViews={["table"]} />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockListDispatch).toBeDefined());
        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalled());
        // Let the initial searches settle before counting.
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 700));
        });
        const before = mockExecuteSearch.mock.calls.length;

        await act(async () => {
            mockListDispatch!({ type: "refresh" });
        });

        await waitFor(() => expect(mockExecuteSearch.mock.calls.length).toBe(before + 1));
        // The container's own query (filters, metadata filters, pagination), the same one its
        // filter-driven searches send.
        expect(mockExecuteSearch.mock.calls[before][0]).toEqual(
            expect.objectContaining({
                filters: expect.any(Object),
                metadataFilters: expect.any(Array),
                pagination: expect.objectContaining({ from: 0 }),
            })
        );
    });
});
