/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Map view pagination. In keyword mode a page change re-runs the search for that page, through the
 * same query-paginate path the table view uses. In natural-language mode the map pages through the
 * answer held, as the table does: a page change runs no search of either kind.
 */
import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import ModernSearchContainer from "./ModernSearchContainer";

const mockExecuteSearch = jest.fn();
const mockExecuteNlpSearch = jest.fn();
let mockMapDispatch: ((action: any) => void) | undefined;
let mockMapState: any;
let mockTopBarProps: any;
let mockFeatures: string[] = ["LOCATIONSERVICES"];
let mockSearchModePreference: "keyword" | "nlp" = "keyword";

jest.mock("../../services/appCache", () => ({
    appCache: {
        getItem: () => ({
            featuresEnabled: mockFeatures,
        }),
    },
}));
jest.mock("./hooks/useSearchAPI", () => ({
    useSearchAPI: () => ({
        executeSearch: mockExecuteSearch,
        executeNlpSearch: mockExecuteNlpSearch,
    }),
}));
jest.mock("./hooks/usePreferences", () => ({
    usePreferences: () => ({
        preferences: {
            pageSize: 25,
            viewMode: "map",
            searchMode: mockSearchModePreference,
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
    SearchTopBar: (props: any) => {
        mockTopBarProps = props;
        return null;
    },
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
    default: ({ dispatch, state }: any) => {
        mockMapDispatch = dispatch;
        mockMapState = state;
        return null;
    },
}));

/** A natural-language answer of `count` asset hits (ids hit-0 ... hit-<count - 1>). */
const nlpAnswer = (count: number) => ({
    hits: {
        total: { value: count, relation: "gte" },
        hits: Array.from({ length: count }, (_, i) => ({
            _id: `hit-${i}`,
            _score: 1 - i / 1000,
            _source: { str_databaseid: "db-a", str_assetid: `asset-${i}`, str_assetname: `A${i}` },
        })),
    },
    aggregations: {},
    aggregationTotal: count,
    nlp: {
        query: "red truck",
        embeddingModelId: "m",
        databasesSearched: 1,
        candidatesEvaluated: count,
        itemsCollapsed: 0,
        classIntent: [],
        truncated: false,
    },
    warnings: [],
});

const mapHitIds = () => (mockMapState?.result?.hits?.hits ?? []).map((hit: any) => hit._id);

/** An action the map view raises, as its page control does. */
const mapDispatch = (action: any) => {
    if (!mockMapDispatch) throw new Error("the map view has not rendered");
    mockMapDispatch(action);
};

beforeEach(() => {
    jest.clearAllMocks();
    mockMapDispatch = undefined;
    mockMapState = undefined;
    mockTopBarProps = undefined;
    mockFeatures = ["LOCATIONSERVICES"];
    mockSearchModePreference = "keyword";
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    mockExecuteSearch.mockResolvedValue({ hits: { hits: [], total: { value: 0 } } });
    mockExecuteNlpSearch.mockResolvedValue(nlpAnswer(60));
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
            mapDispatch({
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

    it("pages through the natural-language answer held, with no keyword or natural-language search", async () => {
        mockFeatures = ["LOCATIONSERVICES", "VECTORSEARCH"];
        mockSearchModePreference = "nlp";
        render(
            <MemoryRouter>
                <ModernSearchContainer mode="full" allowedViews={["map"]} />
            </MemoryRouter>
        );
        await waitFor(() => expect(mockTopBarProps).toBeDefined());
        await act(async () => {
            mockTopBarProps.onQueryChange("red truck");
        });
        await act(async () => {
            await mockTopBarProps.onSearch();
        });
        await waitFor(() => expect(mockExecuteNlpSearch).toHaveBeenCalledTimes(1));

        // The map holds the first page of the answer, and its pages cover the 60 hits returned.
        await waitFor(() => expect(mapHitIds()).toHaveLength(25));
        expect(mapHitIds()[0]).toBe("hit-0");
        expect(mockMapState.result.pagedTotal).toBe(60);
        expect(mockMapState.result.nlp).toBeDefined();

        await act(async () => {
            mapDispatch({ type: "query-paginate", pagination: { from: 25, size: 25 } });
        });
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 700));
        });

        // Page 2 of the same answer: hits 25-49, the natural-language answer still on the map.
        expect(mapHitIds()).toHaveLength(25);
        expect(mapHitIds()[0]).toBe("hit-25");
        expect(mockMapState.result.nlp).toBeDefined();
        expect(mockExecuteSearch).not.toHaveBeenCalled();
        expect(mockExecuteNlpSearch).toHaveBeenCalledTimes(1);
    });
});
