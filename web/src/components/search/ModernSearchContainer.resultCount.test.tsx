/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The top bar's result count is shown only once a search has answered: while the result is
 * null (first load, or right after Clear All Filters) it gets no count, not "0 results" (#1252).
 */
import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ModernSearchContainer from "./ModernSearchContainer";

const mockExecuteSearch = jest.fn();
let mockTopBarProps: any;

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
    SearchTopBar: (props: any) => {
        mockTopBarProps = props;
        return null;
    },
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
jest.mock("./SearchPageListView", () => ({ __esModule: true, default: () => null }));

const RESULT = { hits: { hits: [], total: { value: 7 } } };

beforeEach(() => {
    jest.clearAllMocks();
    mockTopBarProps = undefined;
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("ModernSearchContainer result count", () => {
    it("passes no count while there is no result, then the total once the search answers", async () => {
        let answer: (value: any) => void = () => undefined;
        mockExecuteSearch.mockImplementation(() => new Promise((resolve) => (answer = resolve)));

        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" allowedViews={["table"]} />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalled());
        expect(mockTopBarProps.resultCount).toBeUndefined();

        mockExecuteSearch.mockResolvedValue(RESULT);
        await act(async () => {
            answer(RESULT);
        });
        await waitFor(() => expect(mockTopBarProps.resultCount).toBe(7));
    });

    it("drops the count after Clear All Filters until the reloaded list arrives", async () => {
        mockExecuteSearch.mockResolvedValue(RESULT);
        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" allowedViews={["table"]} />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockTopBarProps?.resultCount).toBe(7));

        let answer: (value: any) => void = () => undefined;
        mockExecuteSearch.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
        await act(async () => {
            mockTopBarProps.onClearAll();
        });
        expect(mockTopBarProps.resultCount).toBeUndefined();

        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 700));
            answer(RESULT);
        });
        await waitFor(() => expect(mockTopBarProps.resultCount).toBe(7));
    });
});
