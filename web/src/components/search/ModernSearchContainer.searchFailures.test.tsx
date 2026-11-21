/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The first search of a page load fails with no answer on screen (an API 504, a network failure): the
 * page says the search did not finish, in the page as well as in the "Search failed" toast, and the
 * results area neither waits for a first answer that will not come nor reads as "No matches".
 * APIService hands the message of a failed call to the page as `[false, message]`.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import ModernSearchContainer, {
    SEARCH_FAILED_NOTICE,
    SEARCH_FAILED_NOTICE_HEADER,
} from "./ModernSearchContainer";
import { searchAssets } from "../../services/APIService";

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: () => ({ featuresEnabled: [] }) },
}));
jest.mock("../../services/APIService", () => ({
    searchAssets: jest.fn(),
    searchAssetsSimple: jest.fn(),
    fetchSearchMappings: jest.fn(),
    fetchAllDatabases: jest.fn().mockResolvedValue([{ databaseId: "db-a" }]),
    fetchTags: jest.fn().mockResolvedValue([]),
    fetchtagTypes: jest.fn().mockResolvedValue([]),
    fetchAsset: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
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

// API Gateway's 504 body, and the browser's message for a request that got no response
const TIMED_OUT = "Endpoint request timed out";
const NETWORK = "Failed to fetch";

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

jest.setTimeout(20000);

describe("ModernSearchContainer first search that fails with no answer on screen", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        clearPreferenceCookie();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => jest.restoreAllMocks());

    it.each([
        ["the API timed out", TIMED_OUT],
        ["the network failed", NETWORK],
    ])("says in the page that the search did not finish when %s", async (_, message) => {
        (searchAssets as jest.Mock).mockResolvedValue([false, message]);
        const { container } = renderContainer();

        const notice = await screen.findByTestId("search-failed-notice");
        expect(notice).toHaveTextContent(SEARCH_FAILED_NOTICE_HEADER);
        expect(notice).toHaveTextContent(SEARCH_FAILED_NOTICE);
        expect(toasts(container)).toContainEqual({ header: "Search failed", content: message });
        // The results area neither waits for a first answer that will not come nor reads as no matches
        expect(screen.getByText("No results to show")).toBeInTheDocument();
        expect(screen.queryByText("Loading..")).toBeNull();
        expect(screen.queryByText("No matches")).toBeNull();
        expect(screen.queryByRole("button", { name: "Clear filter" })).toBeNull();
        // The search controls stay usable for a retry.
        expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
        expect(screen.getByPlaceholderText("Search by keywords (wildcard)...")).toBeInTheDocument();
    });

    it("clears the failure once a search answers", async () => {
        (searchAssets as jest.Mock).mockResolvedValue([false, TIMED_OUT]);
        renderContainer();
        await screen.findByTestId("search-failed-notice");

        (searchAssets as jest.Mock).mockResolvedValue([true, keywordRows]);
        await userEvent.click(screen.getByRole("button", { name: "Search" }));

        await screen.findByText("Alpha");
        expect(screen.queryByTestId("search-failed-notice")).toBeNull();
        expect(screen.queryByText("No results to show")).toBeNull();
    });

    it("keeps an answer already on screen and says the failure only in the toast", async () => {
        (searchAssets as jest.Mock).mockResolvedValue([true, keywordRows]);
        const { container } = renderContainer();
        await screen.findByText("Alpha");

        (searchAssets as jest.Mock).mockResolvedValue([false, TIMED_OUT]);
        await userEvent.click(screen.getByRole("button", { name: "Search" }));

        await waitFor(() =>
            expect(toasts(container)).toContainEqual({
                header: "Search failed",
                content: TIMED_OUT,
            })
        );
        expect(screen.getByText("Alpha")).toBeInTheDocument();
        expect(screen.queryByTestId("search-failed-notice")).toBeNull();
        expect(screen.queryByText("No results to show")).toBeNull();
    });
});
