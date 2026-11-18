/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Map view's request (#651), checked on the body sent to POST /search. Entering the map adds one
 * filter for records with a location and nothing else: the user's metadata filters, metadata mode
 * and operator stay as set, on the map and after leaving it. In natural-language mode the map pages
 * through the answer held, so a view switch sends no search.
 */
import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import ModernSearchContainer from "./ModernSearchContainer";

const mockSearchAssets = jest.fn();
const mockSearchNlp = jest.fn();
let mockTopBarProps: any;
let mockSidebarProps: any;
let mockFeatures: string[] = ["LOCATIONSERVICES"];
let mockSearchModePreference: "keyword" | "nlp" = "keyword";

jest.mock("../../services/APIService", () => ({
    searchAssets: (...args: any[]) => mockSearchAssets(...args),
    searchNlp: (...args: any[]) => mockSearchNlp(...args),
    searchAssetsSimple: jest.fn(),
    fetchSearchMappings: jest.fn(),
}));
jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ featuresEnabled: mockFeatures }) },
}));
jest.mock("./hooks/usePreferences", () => ({
    usePreferences: () => ({
        preferences: {
            pageSize: 25,
            viewMode: "table",
            searchMode: mockSearchModePreference,
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

const HAS_LOCATION = { query_string: { query: "_exists_:geo_MD_location" } };
const emptyAnswer = { hits: { hits: [], total: { value: 0, relation: "eq" } } };
const nlpAnswer = {
    hits: { total: { value: 1, relation: "gte" }, hits: [] },
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
};

/** Lets the debounced auto-refresh (500 ms) run. */
const settle = async () => {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 700));
    });
};

const lastBody = () => mockSearchAssets.mock.calls[mockSearchAssets.mock.calls.length - 1][0];

/** Metadata filter rows as the user adds them in the sidebar. */
const addMetadataRows = async (rows: Array<{ key: string; value: string }>) => {
    for (const row of rows) {
        await act(async () => {
            mockSidebarProps.onAddMetadataFilter();
        });
        const index = mockSidebarProps.metadataFilters.length - 1;
        await act(async () => {
            mockSidebarProps.onUpdateMetadataFilter(index, {
                ...mockSidebarProps.metadataFilters[index],
                ...row,
            });
        });
    }
};

const chooseView = async (name: "Map" | "Table") => {
    await userEvent.click(screen.getByRole("button", { name }));
    await settle();
};

const renderSearch = async () => {
    render(
        <MemoryRouter>
            <ModernSearchContainer mode="full" />
        </MemoryRouter>
    );
    await waitFor(() => expect(mockSidebarProps).toBeDefined());
};

beforeEach(() => {
    jest.clearAllMocks();
    mockTopBarProps = undefined;
    mockSidebarProps = undefined;
    mockFeatures = ["LOCATIONSERVICES"];
    mockSearchModePreference = "keyword";
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    mockSearchAssets.mockResolvedValue([true, emptyAnswer]);
    mockSearchNlp.mockResolvedValue([true, nlpAnswer]);
});

afterEach(() => jest.restoreAllMocks());

describe("ModernSearchContainer Map view request (#651)", () => {
    it("adds only the location filter on the map and leaves the user's metadata filters, mode and AND operator as set", async () => {
        await renderSearch();
        await waitFor(() => expect(mockSearchAssets).toHaveBeenCalled());
        await addMetadataRows([
            { key: "site", value: "Plant-7" },
            { key: "line", value: "A" },
        ]);
        await act(async () => {
            mockSidebarProps.onMetadataOperatorChange("AND");
        });
        await settle();
        const tableBody = lastBody();
        expect(tableBody.metadataQuery).toBe("MD_str_site:Plant-7 AND MD_str_line:A");
        expect(JSON.stringify(tableBody.filters ?? [])).not.toContain("geo_MD_location");

        const beforeMap = mockSearchAssets.mock.calls.length;
        await chooseView("Map");
        expect(mockSearchAssets.mock.calls.length).toBe(beforeMap + 1);
        const mapBody = lastBody();
        expect(mapBody.filters).toEqual([HAS_LOCATION]);
        expect(mapBody.metadataQuery).toBe("MD_str_site:Plant-7 AND MD_str_line:A");
        expect(mapBody.metadataSearchMode).toBe("both");
        expect(mapBody.from).toBe(0);
        expect(mockSidebarProps.metadataFilters.map((f: any) => f.key)).toEqual(["site", "line"]);
        expect(mockSidebarProps.metadataOperator).toBe("AND");
        expect(mockSidebarProps.metadataSearchMode).toBe("both");
        // Nothing to clear but the user's own two rows.
        expect(mockTopBarProps.hasActiveFilters).toBe(true);

        await chooseView("Table");
        const backBody = lastBody();
        expect(backBody.filters).toBeUndefined();
        expect(backBody.metadataQuery).toBe("MD_str_site:Plant-7 AND MD_str_line:A");
        expect(mockSidebarProps.metadataOperator).toBe("AND");
        expect(mockSidebarProps.metadataFilters).toHaveLength(2);
    });

    it("keeps the metadata mode the user chose on the map", async () => {
        await renderSearch();
        await waitFor(() => expect(mockSearchAssets).toHaveBeenCalled());
        await addMetadataRows([{ key: "site", value: "Plant-7" }]);
        await act(async () => {
            mockSidebarProps.onMetadataSearchModeChange("value");
        });
        await settle();
        await chooseView("Map");
        expect(lastBody().metadataSearchMode).toBe("value");
        expect(lastBody().metadataQuery).toBe("Plant-7");
        expect(mockSidebarProps.metadataSearchMode).toBe("value");
    });

    it("on a fresh load the map offers no Clear all filters", async () => {
        await renderSearch();
        await waitFor(() => expect(mockSearchAssets).toHaveBeenCalled());
        await settle();
        await chooseView("Map");
        expect(lastBody().filters).toEqual([HAS_LOCATION]);
        expect(mockSidebarProps.metadataFilters).toEqual([]);
        expect(mockTopBarProps.hasActiveFilters).toBe(false);
    });

    it("keeps the user's own location metadata filter when files replace the map with the table", async () => {
        await renderSearch();
        await waitFor(() => expect(mockSearchAssets).toHaveBeenCalled());
        await addMetadataRows([{ key: "Location", value: "*" }]);
        await settle();
        await chooseView("Map");

        await act(async () => {
            mockSidebarProps.onRecordTypeChange("file");
        });
        await settle();
        expect(mockSidebarProps.metadataFilters.map((f: any) => f.key)).toEqual(["Location"]);
        expect(lastBody().entityTypes).toEqual(["file"]);
        expect(JSON.stringify(lastBody().filters ?? [])).not.toContain("geo_MD_location");
        expect(lastBody().metadataQuery).toBe("MD_str_Location:*");
    });

    it("keeps the map's location scope through Clear all filters", async () => {
        await renderSearch();
        await waitFor(() => expect(mockSearchAssets).toHaveBeenCalled());
        await addMetadataRows([{ key: "site", value: "Plant-7" }]);
        await settle();
        await chooseView("Map");

        await act(async () => {
            mockTopBarProps.onClearAll();
        });
        await settle();
        expect(lastBody().filters).toEqual([HAS_LOCATION]);
        expect(lastBody().metadataQuery).toBeUndefined();
    });

    it("sends no search in natural-language mode when the user enters or leaves the map", async () => {
        mockFeatures = ["LOCATIONSERVICES", "VECTORSEARCH"];
        mockSearchModePreference = "nlp";
        await renderSearch();
        await waitFor(() => expect(mockTopBarProps).toBeDefined());
        await act(async () => {
            mockTopBarProps.onQueryChange("red truck");
        });
        await act(async () => {
            await mockTopBarProps.onSearch();
        });
        await waitFor(() => expect(mockSearchNlp).toHaveBeenCalledTimes(1));

        await chooseView("Map");
        await chooseView("Table");
        expect(mockSearchNlp).toHaveBeenCalledTimes(1);
        expect(mockSearchAssets).not.toHaveBeenCalled();
    });
});
