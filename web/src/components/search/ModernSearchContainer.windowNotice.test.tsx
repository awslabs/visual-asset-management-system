/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search container shows the result-window notice when the total passes the 10,000-result
 * window the search API pages within, in every view, the map view included.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ModernSearchContainer from "./ModernSearchContainer";

const mockExecuteSearch = jest.fn();

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ featuresEnabled: ["LOCATIONSERVICES"] }) },
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
jest.mock("./SearchPageMapView", () => ({
    __esModule: true,
    default: () => <div data-testid="map-view" />,
}));
jest.mock("./SearchNotifications/ToastManager", () => ({ __esModule: true, default: () => null }));
jest.mock("../../pages/ListPage", () => ({ __esModule: true, default: () => null }));
jest.mock("../../services/APIService", () => ({
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
}));
jest.mock("./SearchPageListView", () => ({ __esModule: true, default: () => null }));

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    mockExecuteSearch.mockResolvedValue({
        hits: { hits: [], total: { value: 50, relation: "eq" } },
        aggregationTotal: 25000,
    });
});

afterEach(() => jest.restoreAllMocks());

describe("ModernSearchContainer result-window notice", () => {
    it("shows the notice in the map view", async () => {
        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" allowedViews={["map"]} />
            </MemoryRouter>
        );
        expect(await screen.findByTestId("map-view")).toBeInTheDocument();
        expect(await screen.findByText(/Showing the first 10,000/)).toBeInTheDocument();
    });
});
