/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search page shows the newest search's answer. An older search that answers later (a page
 * change, a refresh or an auto-refresh still running) never replaces it, and the loading state lasts
 * until the newest search answers. The count in the title row describes an answer the page holds:
 * none while Clear all filters reloads the list.
 */
import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import ModernSearchContainer from "./ModernSearchContainer";

const mockExecuteSearch = jest.fn();
const mockExecuteNlpSearch = jest.fn();
let mockListDispatch: ((action: any) => void) | undefined;
let mockListState: any;
let mockTopBarProps: any;
let mockFeatures: string[] = [];

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ featuresEnabled: mockFeatures }) },
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
            viewMode: "table",
            cardSize: "medium",
            showThumbnails: false,
            showMapThumbnails: false,
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
jest.mock("./SearchPageMapView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchNotifications/ToastManager", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchPageListView", () => ({
    __esModule: true,
    default: ({ dispatch, state }: any) => {
        mockListDispatch = dispatch;
        mockListState = state;
        return null;
    },
}));

/** A promise and the function that settles it, for an answer the test releases when it chooses. */
function deferred<T>() {
    let resolve: (value: T) => void = () => undefined;
    const promise = new Promise<T>((r) => {
        resolve = r;
    });
    return { promise, resolve };
}

const keywordAnswer = (label: string, total: number) => ({
    hits: {
        total: { value: total, relation: "eq" },
        hits: [{ _id: label, _score: 1, _source: { str_assetname: label } }],
    },
    aggregations: {},
    aggregationTotal: total,
});

const nlpAnswer = (label: string) => ({
    hits: {
        total: { value: 1, relation: "gte" },
        hits: [{ _id: label, _score: 0.9, _source: { str_assetname: label } }],
    },
    aggregations: {},
    aggregationTotal: 1,
    nlp: {
        query: "red truck",
        embeddingModelId: "m",
        databasesSearched: 1,
        candidatesEvaluated: 1,
        itemsCollapsed: 0,
        classIntent: [],
        truncated: false,
    },
    warnings: [],
});

const shownIds = () => (mockListState?.result?.hits?.hits ?? []).map((hit: any) => hit._id);

/** An action the list view raises, as a page change or a refresh after a row action does. */
const listDispatch = (action: any) => {
    if (!mockListDispatch) throw new Error("the list view has not rendered");
    mockListDispatch(action);
};

const renderContainer = () =>
    render(
        <MemoryRouter>
            <ModernSearchContainer mode="full" />
        </MemoryRouter>
    );

/** Lets the mount search settle and the 500 ms auto-refresh window pass. */
const settle = () =>
    act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 700));
    });

beforeEach(() => {
    jest.clearAllMocks();
    mockListDispatch = undefined;
    mockListState = undefined;
    mockTopBarProps = undefined;
    mockFeatures = [];
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("ModernSearchContainer keeps the newest search's answer", () => {
    it("drops a keyword page that answers after a newer page was requested", async () => {
        mockExecuteSearch.mockResolvedValueOnce(keywordAnswer("page-1", 75));
        renderContainer();
        await waitFor(() => expect(shownIds()).toEqual(["page-1"]));
        await settle();

        const page2 = deferred<any>();
        const page3 = deferred<any>();
        mockExecuteSearch.mockReturnValueOnce(page2.promise).mockReturnValueOnce(page3.promise);
        await act(async () => {
            listDispatch({ type: "query-paginate", pagination: { from: 25, size: 25 } });
        });
        await act(async () => {
            listDispatch({ type: "query-paginate", pagination: { from: 50, size: 25 } });
        });
        expect(mockExecuteSearch).toHaveBeenCalledTimes(3);

        // The newer request answers first, then the older one.
        await act(async () => {
            page3.resolve(keywordAnswer("page-3", 75));
        });
        expect(shownIds()).toEqual(["page-3"]);
        expect(mockListState.loading).toBe(false);
        await act(async () => {
            page2.resolve(keywordAnswer("page-2", 75));
        });
        expect(shownIds()).toEqual(["page-3"]);
        expect(mockListState.loading).toBe(false);
    });

    it("stays loading when an older search answers first, and shows only the newest answer", async () => {
        mockExecuteSearch.mockResolvedValueOnce(keywordAnswer("first", 2));
        renderContainer();
        await waitFor(() => expect(shownIds()).toEqual(["first"]));
        await settle();

        const older = deferred<any>();
        const newer = deferred<any>();
        mockExecuteSearch.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
        await act(async () => {
            listDispatch({ type: "refresh" });
        });
        await act(async () => {
            listDispatch({ type: "refresh" });
        });

        await act(async () => {
            older.resolve(keywordAnswer("older", 2));
        });
        expect(shownIds()).toEqual(["first"]);
        expect(mockListState.loading).toBe(true);

        await act(async () => {
            newer.resolve(keywordAnswer("newer", 2));
        });
        expect(shownIds()).toEqual(["newer"]);
        expect(mockListState.loading).toBe(false);
    });

    it("drops an older search's failure once a newer search answered", async () => {
        mockExecuteSearch.mockResolvedValueOnce(keywordAnswer("first", 2));
        renderContainer();
        await waitFor(() => expect(shownIds()).toEqual(["first"]));
        await settle();

        let failOlder: (error: Error) => void = () => undefined;
        const older = new Promise((_, reject) => {
            failOlder = reject;
        });
        const newer = deferred<any>();
        mockExecuteSearch.mockReturnValueOnce(older).mockReturnValueOnce(newer.promise);
        await act(async () => {
            listDispatch({ type: "refresh" });
        });
        await act(async () => {
            listDispatch({ type: "refresh" });
        });
        await act(async () => {
            newer.resolve(keywordAnswer("newer", 2));
        });
        await act(async () => {
            failOlder(new Error("Search is busy right now. Try again in a moment."));
        });
        expect(shownIds()).toEqual(["newer"]);
        expect(mockListState.error).toBeNull();
    });

    it("drops a natural-language answer that arrives after a newer one", async () => {
        mockFeatures = ["NOOPENSEARCH", "VECTORSEARCH"];
        renderContainer();
        await waitFor(() => expect(mockTopBarProps).toBeDefined());
        await act(async () => {
            mockTopBarProps.onQueryChange("red truck");
        });
        mockExecuteNlpSearch.mockResolvedValueOnce(nlpAnswer("first"));
        await act(async () => {
            await mockTopBarProps.onSearch();
        });
        await waitFor(() => expect(shownIds()).toEqual(["first"]));
        await settle();

        const older = deferred<any>();
        const newer = deferred<any>();
        mockExecuteNlpSearch.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
        await act(async () => {
            listDispatch({ type: "refresh" });
        });
        await act(async () => {
            listDispatch({ type: "refresh" });
        });
        await act(async () => {
            newer.resolve(nlpAnswer("newer"));
        });
        await act(async () => {
            older.resolve(nlpAnswer("older"));
        });
        expect(shownIds()).toEqual(["newer"]);
        expect(mockListState.loading).toBe(false);
    });
});

describe("ModernSearchContainer count after Clear all filters", () => {
    it("shows no count until the reloaded list answers", async () => {
        mockExecuteSearch.mockResolvedValueOnce(keywordAnswer("before", 2));
        renderContainer();
        await waitFor(() => expect(mockTopBarProps?.resultCount).toBe(2));
        await settle();
        // Clear all filters is offered once something is set.
        await act(async () => {
            mockTopBarProps.onQueryChange("gearbox");
        });

        const reload = deferred<any>();
        mockExecuteSearch.mockReturnValueOnce(reload.promise);
        await act(async () => {
            mockTopBarProps.onClearAll();
        });
        expect(mockTopBarProps.resultCount).toBeUndefined();
        expect(mockTopBarProps.resultCountIsLowerBound).toBe(false);

        // The auto-refresh after the clear reloads the list; the count still waits for its answer.
        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalledTimes(2));
        expect(mockTopBarProps.resultCount).toBeUndefined();

        await act(async () => {
            reload.resolve(keywordAnswer("after", 5));
        });
        expect(mockTopBarProps.resultCount).toBe(5);
    });

    it("drops the answer of a search still running when Clear all filters is pressed", async () => {
        mockExecuteSearch.mockResolvedValueOnce(keywordAnswer("before", 2));
        renderContainer();
        await waitFor(() => expect(shownIds()).toEqual(["before"]));
        await settle();

        const running = deferred<any>();
        const reload = deferred<any>();
        mockExecuteSearch.mockReturnValueOnce(running.promise).mockReturnValueOnce(reload.promise);
        await act(async () => {
            listDispatch({ type: "refresh" });
        });
        await act(async () => {
            mockTopBarProps.onClearAll();
        });
        await act(async () => {
            running.resolve(keywordAnswer("filtered", 1));
        });
        expect(shownIds()).not.toContain("filtered");
        expect(mockTopBarProps.resultCount).toBeUndefined();

        await waitFor(() => expect(mockExecuteSearch).toHaveBeenCalledTimes(3));
        await act(async () => {
            reload.resolve(keywordAnswer("after", 4));
        });
        expect(shownIds()).toEqual(["after"]);
        expect(mockTopBarProps.resultCount).toBe(4);
    });
});
