/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import ModernSearchContainer from "./ModernSearchContainer";
import { appCache } from "../../services/appCache";
import { searchAssets, searchNlp } from "../../services/APIService";

jest.mock("../../services/appCache", () => ({ appCache: { getItem: jest.fn() } }));
jest.mock("../../services/APIService", () => ({
    searchAssets: jest.fn(),
    searchAssetsSimple: jest.fn(),
    fetchSearchMappings: jest.fn(),
    searchNlp: jest.fn(),
    fetchAllDatabases: jest.fn().mockResolvedValue([{ databaseId: "db1" }]),
    fetchTags: jest.fn().mockResolvedValue([]),
    fetchtagTypes: jest.fn().mockResolvedValue([]),
    fetchAsset: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));
// The two modules below use import.meta.glob (unparseable under Jest); the container only
// initializes the viewer registry and asks whether rows are viewable.
jest.mock("../../visualizerPlugin", () => ({
    initializePluginRegistry: jest.fn().mockResolvedValue(undefined),
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({
    PluginRegistry: {
        getInstance: () => ({
            isInitialized: () => true,
            initialize: jest.fn().mockResolvedValue(undefined),
            getCompatibleViewers: () => [],
        }),
    },
    getFileExtension: (filename: string) => filename.slice(filename.lastIndexOf(".")).toLowerCase(),
    getFileExtensions: () => [],
}));
// ESM-only map libraries and heavy modals are not under test here.
jest.mock("./SearchPageMapView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchLayout/GeoFilterMapSelector", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("./SearchResults/MapThumbnail", () => ({ __esModule: true, default: () => null }));
jest.mock("../filemanager/modals/FileViewerModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../filemanager/modals/AssetPreviewModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/AssetUnarchiveModal", () => ({ __esModule: true, default: () => null }));

const keywordEnvelope = {
    hits: { total: { value: 0, relation: "eq" }, hits: [] },
    aggregations: {},
    aggregationTotal: 0,
};

const nlpEnvelope = (truncated: boolean) => ({
    took: 5,
    timed_out: false,
    _shards: { total: 1, successful: 1, skipped: 0, failed: 0 },
    hits: {
        total: { value: 2, relation: truncated ? "gte" : "eq" },
        hits: [
            {
                _id: "db1#a1#/x.glb#v1",
                _score: 0.9,
                _index_type: "asset",
                _source: { str_databaseid: "db1", str_assetid: "a1", str_assetname: "Truck" },
                _vector: {
                    distance: 0.1,
                    embeddingModelId: "m",
                    sourceModalities: ["render"],
                    indexedAt: "t",
                    fileClass: "mesh",
                    segmentHits: 0,
                    bestSegment: null,
                },
            },
            {
                _id: "db1#a2#/y.glb#v1",
                _score: 0.8,
                _index_type: "asset",
                _source: { str_databaseid: "db1", str_assetid: "a2", str_assetname: "Lorry" },
                _vector: {
                    distance: 0.2,
                    embeddingModelId: "m",
                    sourceModalities: ["text"],
                    indexedAt: "t",
                    fileClass: "mesh",
                    segmentHits: 0,
                    bestSegment: null,
                },
            },
        ],
    },
    aggregations: {},
    aggregationTotal: 2,
    nlp: {
        query: "red truck",
        embeddingModelId: "m",
        databasesSearched: 1,
        candidatesEvaluated: 2,
        itemsCollapsed: 0,
        classIntent: [],
        truncated,
    },
    warnings: [],
});

const setFeatures = (features: string[]) =>
    (appCache.getItem as jest.Mock).mockReturnValue({ featuresEnabled: features });

const renderContainer = () =>
    render(
        <MemoryRouter>
            <ModernSearchContainer mode="full" />
        </MemoryRouter>
    );

const clearPreferenceCookie = () => {
    document.cookie = "vams-search-preferences=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/";
};

describe("ModernSearchContainer search modes", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        clearPreferenceCookie();
        (searchAssets as jest.Mock).mockResolvedValue([true, keywordEnvelope]);
        (searchNlp as jest.Mock).mockResolvedValue([true, nlpEnvelope(false)]);
    });

    it("with OpenSearch off and vector search on: reduced sidebar, natural language forced, no request for an empty query", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        renderContainer();
        const nlpSegment = await screen.findByTestId("nlp");
        expect(nlpSegment).toHaveAttribute("aria-pressed", "true");
        expect(nlpSegment).toBeDisabled();
        expect(screen.getByTestId("keyword")).toBeDisabled();
        // Reduced sidebar: the mode selector and the NLP filters, none of the OpenSearch panels.
        expect(screen.getByText("Search Mode")).toBeInTheDocument();
        expect(screen.getByLabelText("Include archived items")).toBeInTheDocument();
        expect(screen.getByLabelText("Search inside files")).toBeChecked();
        expect(screen.queryByText("Basic Filters")).toBeNull();
        expect(screen.queryByText("Metadata Search")).toBeNull();
        // An empty query has nothing to embed: the empty state renders without a request, and the
        // "Search completed" toast is not announced for a search that never ran.
        await screen.findByText("No matches");
        expect(searchNlp).not.toHaveBeenCalled();
        expect(searchAssets).not.toHaveBeenCalled();
        expect(screen.queryByText("Search completed")).toBeNull();
    });

    it("sends the natural-language body with size 100 and shows the truncated footer", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        (searchNlp as jest.Mock).mockResolvedValue([true, nlpEnvelope(true)]);
        renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        expect((searchNlp as jest.Mock).mock.calls[0][0]).toEqual({
            query: "red truck",
            size: 100,
            entityTypes: ["asset"],
            includeArchived: false,
        });
        await screen.findByText("Showing the top 2 semantic matches; more may exist.");
        expect(screen.getByText("Truck")).toBeInTheDocument();
        expect(screen.getByText("90%")).toBeInTheDocument();
    });

    it("with only OpenSearch on: no mode control and the keyword search runs on mount", async () => {
        setFeatures([]);
        renderContainer();
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));
        expect(screen.queryByTestId("nlp")).toBeNull();
        expect(screen.queryByTestId("keyword")).toBeNull();
        expect(screen.getByText("Basic Filters")).toBeInTheDocument();
        expect(searchNlp).not.toHaveBeenCalled();
        // Keyword mode has no `includeSegments`, so the checkbox is not rendered.
        expect(screen.queryByLabelText("Search inside files")).toBeNull();
        // Positive control for the empty-query test above: a search that ran is announced.
        await screen.findByText("Search completed");
    });

    it("with both engines on: a live control defaulting to keyword; switching persists, shows `Search inside files` beside the full sidebar, and routes the next search to NLP", async () => {
        setFeatures(["VECTORSEARCH"]);
        renderContainer();
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));
        const keyword = screen.getByTestId("keyword");
        expect(keyword).toHaveAttribute("aria-pressed", "true");
        expect(keyword).not.toBeDisabled();
        expect(screen.getByTestId("nlp")).not.toBeDisabled();
        expect(screen.queryByLabelText("Search inside files")).toBeNull();
        await userEvent.click(screen.getByTestId("nlp"));
        await waitFor(() =>
            expect(screen.getByTestId("nlp")).toHaveAttribute("aria-pressed", "true")
        );
        // The checkbox follows the mode, not the sidebar layout: with OpenSearch on it renders
        // above the full set of panels.
        expect(screen.getByLabelText("Search inside files")).toBeChecked();
        expect(screen.getByText("Basic Filters")).toBeInTheDocument();
        // usePreferences writes the JSON verbatim into the cookie after a 1 s debounce.
        await waitFor(() => expect(document.cookie).toContain('"searchMode":"nlp"'), {
            timeout: 3000,
        });
        const input = screen.getByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        expect((searchNlp as jest.Mock).mock.calls[0][0].query).toBe("red truck");
    });

    it("typing after a mode switch does not search the half-typed text when the auto-refresh fires", async () => {
        setFeatures(["VECTORSEARCH"]);
        renderContainer();
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));
        // Switching with an empty box arms the 500 ms auto-refresh; the user is typing when it fires.
        await userEvent.click(screen.getByTestId("nlp"));
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck", { delay: 80 });
        await new Promise((resolve) => setTimeout(resolve, 700));
        expect(searchNlp).not.toHaveBeenCalled();
    });

    it("a submit inside the auto-refresh window after a mode switch runs the query once, not twice", async () => {
        setFeatures(["VECTORSEARCH"]);
        renderContainer();
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));
        const input = screen.getByPlaceholderText("Search by keywords...");
        await userEvent.type(input, "red truck");
        // Switching arms the auto-refresh for "red truck"; an immediate submit must not be doubled.
        await userEvent.click(screen.getByTestId("nlp"));
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        await new Promise((resolve) => setTimeout(resolve, 900));
        expect(searchNlp).toHaveBeenCalledTimes(1);
        expect((searchNlp as jest.Mock).mock.calls[0][0].query).toBe("red truck");
    });

    it("shows the message of each response warning in one toast, never the object", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        const truncatedMessage =
            "At least one vector search call returned a full window of candidates; more matches may exist. Narrow the query or the database scope.";
        const ignoredMessage =
            "filters, metadataQuery, geoSearch and tags require OpenSearch, which is not enabled on this deployment; they were ignored.";
        (searchNlp as jest.Mock).mockResolvedValue([
            true,
            {
                ...nlpEnvelope(true),
                warnings: [
                    { code: "truncated:window", message: truncatedMessage },
                    { code: "opensearch:fields_ignored", message: ignoredMessage },
                ],
            },
        ]);
        renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await screen.findByText("Search notice");
        expect(screen.getByText(`${truncatedMessage} ${ignoredMessage}`)).toBeInTheDocument();
        expect(screen.queryByText(/\[object Object\]/)).toBeNull();
    });

    it("clearing `Search inside files` re-runs the search with includeSegments false; the default and a re-check send no key", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        // The rows render in the same commit that clears `loading`, so the sidebar is enabled again.
        await screen.findByText("Truck");
        expect(searchNlp).toHaveBeenCalledTimes(1);
        expect((searchNlp as jest.Mock).mock.calls[0][0]).not.toHaveProperty("includeSegments");
        const checkbox = screen.getByLabelText("Search inside files");
        expect(checkbox).toBeChecked();
        await userEvent.click(checkbox);
        // A filter change re-runs the search through the 500 ms auto-refresh debounce.
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(2), { timeout: 3000 });
        expect((searchNlp as jest.Mock).mock.calls[1][0]).toEqual({
            query: "red truck",
            size: 100,
            entityTypes: ["asset"],
            includeArchived: false,
            includeSegments: false,
        });
        expect(screen.getByLabelText("Search inside files")).not.toBeChecked();
        // Re-checking stores `true`, which the builder omits exactly like the untouched default.
        // The sidebar is disabled while the second search is in flight, so wait for it to settle.
        await waitFor(() => expect(screen.getByLabelText("Search inside files")).toBeEnabled());
        await userEvent.click(screen.getByLabelText("Search inside files"));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(3), { timeout: 3000 });
        expect((searchNlp as jest.Mock).mock.calls[2][0]).not.toHaveProperty("includeSegments");
        expect(screen.getByLabelText("Search inside files")).toBeChecked();
    });
});
