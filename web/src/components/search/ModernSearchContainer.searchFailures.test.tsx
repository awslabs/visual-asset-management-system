/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A search the API answers with a 503 failed; the feature is not off. The page reports it as
 * "Search failed" with the API's message, and keeps the search controls: no "Search is not available"
 * state, no reduced or keyword-only layout, and the mode switch still offers both engines.
 *
 * The 503 answers: a natural-language search whose metadata, tag or location filters could not be
 * applied, one throttled or timed out, or one sent while the vector index is being built; and a
 * keyword search while OpenSearch is unreachable.
 * APIService hands the message of a failed call to the page as `[false, message]`
 * (APIService.searchNlp.test.ts covers that for a 503).
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import ModernSearchContainer from "./ModernSearchContainer";
import { appCache } from "../../services/appCache";
import { searchAssets, searchNlp } from "../../services/APIService";

jest.mock("../../services/appCache", () => ({ appCache: { getItem: jest.fn() } }));
jest.mock("../../services/APIService", () => ({
    searchAssets: jest.fn(),
    searchAssetsSimple: jest.fn(),
    fetchSearchMappings: jest.fn(),
    searchNlp: jest.fn(),
    fetchAllDatabases: jest.fn().mockResolvedValue([{ databaseId: "db-a" }]),
    fetchTags: jest.fn().mockResolvedValue([]),
    fetchtagTypes: jest.fn().mockResolvedValue([]),
    fetchAsset: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));
// import.meta.glob (unparseable under Jest) and ESM-only map libraries are not under test here.
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
jest.mock("./SearchPageMapView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchLayout/GeoFilterMapSelector", () => ({ __esModule: true, default: () => null }));
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

const FILTERS_NOT_APPLIED =
    "The search filters could not be applied. Try again; if it keeps failing, contact your administrator.";
const BUSY = "Search is busy right now. Try again in a moment.";
const UNAVAILABLE = "Search is temporarily unavailable. Try again in a moment.";

const keywordRows = {
    hits: {
        total: { value: 1, relation: "eq" },
        hits: [
            {
                _id: "k1",
                _score: 1,
                _index_type: "asset",
                _source: { str_databaseid: "db-a", str_assetid: "k1", str_assetname: "Alpha" },
            },
        ],
    },
    aggregations: {},
    aggregationTotal: 1,
};

const clearPreferenceCookie = () => {
    document.cookie = "vams-search-preferences=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/";
};

const renderContainer = () =>
    render(
        <MemoryRouter>
            <ModernSearchContainer mode="full" />
        </MemoryRouter>
    );

/** The headers and messages of the toasts on screen; the toast stack is a Flashbar. */
const toasts = (container: HTMLElement) =>
    createWrapper(container)
        .findFlashbar()
        ?.findItems()
        .map((item) => ({
            header: item.findHeader()?.getElement().textContent,
            content: item.findContent()?.getElement().textContent,
        })) ?? [];

/** The search page still offers search: both modes, the query box and the Search button. */
const expectSearchStillOffered = () => {
    expect(screen.getByTestId("keyword")).toBeInTheDocument();
    expect(screen.getByTestId("nlp")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
    expect(screen.queryByText("Search is not available")).toBeNull();
    expect(screen.queryByText(/not available|isn't available|not enabled/i)).toBeNull();
};

jest.setTimeout(20000);

describe("ModernSearchContainer search that the API answers with a 503", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        clearPreferenceCookie();
        (appCache.getItem as jest.Mock).mockReturnValue({ featuresEnabled: ["VECTORSEARCH"] });
        (searchAssets as jest.Mock).mockResolvedValue([true, keywordRows]);
    });

    it.each([
        ["its filters could not be applied", FILTERS_NOT_APPLIED],
        ["it was throttled or timed out", BUSY],
        ["the vector index is being built", "Vector index is being built"],
    ])(
        "reports a natural-language search as failed when %s, with the API's message",
        async (_, message) => {
            (searchNlp as jest.Mock).mockResolvedValue([false, message]);
            const { container } = renderContainer();
            await screen.findByText("Alpha");
            await userEvent.click(screen.getByTestId("nlp"));
            const input = await screen.findByPlaceholderText(
                "Describe what you are looking for..."
            );
            await userEvent.type(input, "red truck");
            await userEvent.click(screen.getByRole("button", { name: "Search" }));
            await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));

            await waitFor(() =>
                expect(toasts(container)).toContainEqual({
                    header: "Search failed",
                    content: message,
                })
            );
            // Not a Search notice (an answer's warning): the search did not answer.
            expect(screen.queryByText("Search notice")).toBeNull();
            expectSearchStillOffered();
            expect(screen.getByTestId("nlp")).toHaveAttribute("aria-pressed", "true");
        }
    );

    it.each([["OpenSearch is unreachable", UNAVAILABLE]])(
        "reports a keyword search as failed when %s, with the API's message",
        async (_, message) => {
            const { container } = renderContainer();
            await screen.findByText("Alpha");
            (searchAssets as jest.Mock).mockResolvedValue([false, message]);
            await userEvent.type(screen.getByPlaceholderText("Search by keywords..."), "bracket");
            await userEvent.click(screen.getByRole("button", { name: "Search" }));
            await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(2));

            await waitFor(() =>
                expect(toasts(container)).toContainEqual({
                    header: "Search failed",
                    content: message,
                })
            );
            expectSearchStillOffered();
            expect(screen.getByTestId("keyword")).toHaveAttribute("aria-pressed", "true");
        }
    );
});
