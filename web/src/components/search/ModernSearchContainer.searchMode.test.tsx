/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const keywordRows = {
    hits: {
        total: { value: 2, relation: "eq" },
        hits: [
            {
                _id: "k1",
                _score: 1,
                _index_type: "asset",
                _source: {
                    str_databaseid: "db1",
                    str_assetid: "k1",
                    str_assetname: "Keyword Alpha",
                },
            },
            {
                _id: "k2",
                _score: 1,
                _index_type: "asset",
                _source: {
                    str_databaseid: "db1",
                    str_assetid: "k2",
                    str_assetname: "Keyword Beta",
                },
            },
        ],
    },
    aggregations: {},
    aggregationTotal: 2,
};

// The route's answer: its total is a minimum whether or not a window filled (D75, approximate
// matching); `truncated` alone says a window filled.
const nlpEnvelope = (truncated: boolean) => ({
    took: 5,
    timed_out: false,
    _shards: { total: 1, successful: 1, skipped: 0, failed: 0 },
    hits: {
        total: { value: 2, relation: "gte" },
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

/** The visually hidden live region's source: the count the last finished search announces. */
const countAnnouncement = () => screen.getByTestId("search-count-announcement");

/**
 * What the polite live regions on the page read out. Cloudscape's LiveRegion copies its source
 * into its own `aria-live="polite"` announcer after a 1 s throttle, so this waits that long.
 */
const expectPoliteAnnouncement = (text: string) =>
    waitFor(
        () =>
            expect(
                Array.from(document.querySelectorAll('[aria-live="polite"]')).map((el) =>
                    (el.textContent ?? "").replace(/\.$/, "")
                )
            ).toContain(text),
        { timeout: 3000 }
    );

// Several cases wait out the page's 500 ms auto-refresh window on real timers, so a full parallel run can
// take them past the 5-second default.
jest.setTimeout(20000);

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
        // One engine, nothing to choose: no mode control, and the query box is in natural-language mode.
        await screen.findByPlaceholderText("Describe what you are looking for...");
        expect(screen.queryByTestId("nlp")).toBeNull();
        expect(screen.queryByTestId("keyword")).toBeNull();
        // Reduced sidebar: the record-type selector and the NLP filters, none of the OpenSearch panels.
        expect(screen.getByText("Search for")).toBeInTheDocument();
        expect(screen.getByLabelText("Include archived items")).toBeInTheDocument();
        expect(screen.getByLabelText("Search inside files")).toBeChecked();
        expect(
            screen.getByText("Also matches file contents, including image and video scenes.")
        ).toBeInTheDocument();
        expect(screen.queryByText("Basic Filters")).toBeNull();
        expect(screen.queryByText("Metadata Search")).toBeNull();
        // An empty query has nothing to embed: no request is sent, and the table explains what to
        // type instead of reporting no matches or offering to clear filters that are not the cause.
        // The count badge is withheld too: "0 results" would describe a search that never ran.
        await screen.findByText("Describe what you are looking for");
        expect(screen.queryByText("No matches")).toBeNull();
        expect(screen.queryByRole("button", { name: "Clear filter" })).toBeNull();
        expect(screen.queryByText(/\d+ results$/)).toBeNull();
        expect(searchNlp).not.toHaveBeenCalled();
        expect(searchAssets).not.toHaveBeenCalled();
        // A search that never ran announces nothing.
        expect(countAnnouncement().textContent).toBe("");
    });

    it("marks the search page's splitter for the phone layout, which stacks the filters above the results", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        const { container } = renderContainer();
        await screen.findByPlaceholderText("Describe what you are looking for...");
        const splitter = container.querySelector(".resizable-splitter-container");
        expect(splitter).toHaveClass("search-splitter");
        // The narrow layout keys on the page's own width (a size container), not the window's.
        expect(splitter?.closest(".search-page")).not.toBeNull();
    });

    it("a natural-language search that ran but matched nothing reports No matches and its count", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        (searchNlp as jest.Mock).mockResolvedValue([
            true,
            { ...nlpEnvelope(false), hits: { total: { value: 0, relation: "eq" }, hits: [] } },
        ]);
        renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "purple submarine");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        await screen.findByText("No matches");
        expect(screen.queryByText("Describe what you are looking for")).toBeNull();
        expect(screen.getByText("0 results")).toBeInTheDocument();
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

    it("announces an explicit natural-language search's count in the polite live region, with no completion toast", async () => {
        // D72: a "Search completed" toast covered the title row's view switch and Clear all
        // filters for 5 s after every search; the badge shows the count, the live region reads it out.
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        (searchNlp as jest.Mock).mockResolvedValue([true, nlpEnvelope(true)]);
        renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck{Enter}");
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        await screen.findByText("Truck");
        expect(screen.queryByText("Search completed")).toBeNull();
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 2+ results"));
        await expectPoliteAnnouncement("Found 2+ results");
        // Let a pending auto-refresh window pass: still no toast, and the search is not re-run.
        await new Promise((resolve) => setTimeout(resolve, 900));
        expect(screen.queryByText("Search completed")).toBeNull();
        expect(searchNlp).toHaveBeenCalledTimes(1);
    });

    it("shows one count for a natural-language search: the route's total, in the announcement, the badge and the table header", async () => {
        // UAT D61: the notification said "Found 101+ results" (the route's total) while the badge and
        // the table header said "100+" (the returned hits). The route counted 150 readable matches,
        // a minimum (D75), and returned 2, so every place shows 150+ (as `vamscli search nlp` does);
        // the table still pages the 2 returned hits.
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        const envelope = nlpEnvelope(false);
        (searchNlp as jest.Mock).mockResolvedValue([
            true,
            {
                ...envelope,
                hits: { ...envelope.hits, total: { value: 150, relation: "gte" } },
                aggregationTotal: 150,
            },
        ]);
        const { container } = renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 150+ results"));
        expect(createWrapper(container).findBadge()?.getElement().textContent).toBe("150+ results");
        const table = createWrapper(container).findTable();
        expect(table?.findHeaderSlot()?.getElement().textContent).toContain("150+");
        expect(screen.queryByText(/\b2\+/)).toBeNull();
        expect(createWrapper(container).findPagination()?.findPageNumbers()).toHaveLength(1);
        expect(screen.getByText("Truck")).toBeInTheDocument();
        expect(screen.getByText("Lorry")).toBeInTheDocument();
    });

    it("shows a natural-language count whose windows did not fill as a minimum, with the approximate-matching reason (D75)", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        const { container } = renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 2+ results"));
        expect(createWrapper(container).findBadge()?.getElement().textContent).toBe("2+ results");
        const header = createWrapper(container).findTable()?.findHeaderSlot()?.getElement();
        expect(header?.textContent).toContain("2+");
        // No window filled, so no "top N" line; the popover names approximate matching.
        expect(screen.queryByText(/semantic matches; more may exist/)).toBeNull();
        fireEvent.click(screen.getByText("\u24D8"));
        expect(
            screen.getByText(/finds the closest matches approximately, so it can miss a weak match/)
        ).toBeInTheDocument();
        expect(screen.queryByText(/result limit/)).toBeNull();
    });

    it("announces a single natural-language hit as a minimum: Found 1+ results (D75)", async () => {
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        const envelope = nlpEnvelope(false);
        (searchNlp as jest.Mock).mockResolvedValue([
            true,
            {
                ...envelope,
                hits: { total: { value: 1, relation: "gte" }, hits: [envelope.hits.hits[0]] },
                aggregationTotal: 1,
            },
        ]);
        const { container } = renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 1+ results"));
        expect(createWrapper(container).findBadge()?.getElement().textContent).toBe("1+ results");
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
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 0 results"));
    });

    it("announces a keyword search's count without a toast, on load and after an explicit search", async () => {
        setFeatures([]);
        (searchAssets as jest.Mock).mockResolvedValue([true, keywordRows]);
        renderContainer();
        await screen.findByText("Keyword Alpha");
        expect(screen.queryByText("Search completed")).toBeNull();
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 2 results"));
        await expectPoliteAnnouncement("Found 2 results");
        await userEvent.type(screen.getByPlaceholderText("Search by keywords..."), "alpha");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 2 results"));
        expect(screen.queryByText("Search completed")).toBeNull();
        expect(screen.getByText("Keyword Alpha")).toBeInTheDocument();
    });

    it("announces a single result in the singular", async () => {
        setFeatures([]);
        (searchAssets as jest.Mock).mockResolvedValue([
            true,
            {
                ...keywordRows,
                hits: { total: { value: 1, relation: "eq" }, hits: [keywordRows.hits.hits[0]] },
                aggregationTotal: 1,
            },
        ]);
        renderContainer();
        await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 1 result"));
        await expectPoliteAnnouncement("Found 1 result");
        expect(screen.queryByText("Search completed")).toBeNull();
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
        // Typing arms nothing; from here the clock is frozen, so the submit lands inside the 500 ms
        // window however long the two clicks take on a loaded host.
        jest.useFakeTimers();
        try {
            const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
            // Switching arms the auto-refresh for "red truck"; an immediate submit must not be doubled.
            await user.click(screen.getByTestId("nlp"));
            await user.click(screen.getByRole("button", { name: "Search" }));
            await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
            await act(async () => {
                jest.advanceTimersByTime(900);
            });
            expect(searchNlp).toHaveBeenCalledTimes(1);
        } finally {
            jest.useRealTimers();
        }
        expect((searchNlp as jest.Mock).mock.calls[0][0].query).toBe("red truck");
    });

    describe("response warnings (D77: rare ones in a Search notice above the results, never a toast)", () => {
        const truncatedMessage = "A result window of 100 filled; more files may match";
        const segmentMessage = "A segment window of 100 filled; more segments may match";
        const ignoredMessage = "OpenSearch is not enabled; ignored: filters, tags";
        const noneAccessibleMessage = "No searchable database is accessible to the caller";
        // D76: what the page shows for those two codes instead of the API's message.
        const ignoredText =
            "Some filters aren't available for natural-language search in this deployment and weren't applied.";
        const noneAccessibleText =
            "You don't have access to any database this search covers. Ask your administrator for access.";
        const truncatedLine = "Showing the top 2 semantic matches; more may exist.";
        const segmentLine =
            "Some files have more matching scenes or passages than were checked; more may exist.";

        /** The page's Search notice: the Alert headed "Search notice", or null when none is shown. */
        const searchNotice = (container: HTMLElement) =>
            createWrapper(container)
                .findAllAlerts()
                .find(
                    (alert) => alert.findHeader()?.getElement().textContent === "Search notice"
                ) ?? null;
        /** The notice's lines, one per rare warning, as the user reads them. */
        const noticeLines = (container: HTMLElement) =>
            Array.from(searchNotice(container)?.findContent().getElement().children ?? []).map(
                (line) => line.textContent
            );
        /** Press the notice's dismiss button, as a user does. */
        const dismissNotice = (container: HTMLElement) => {
            const button = searchNotice(container)?.findDismissButton()?.getElement();
            if (!button) throw new Error("no dismissible Search notice on the page");
            fireEvent.click(button);
        };
        /** The headers of the toasts on screen; the toast stack is a Flashbar. */
        const toastHeaders = (container: HTMLElement) =>
            createWrapper(container)
                .findFlashbar()
                ?.findItems()
                .map((item) => item.findHeader()?.getElement().textContent) ?? [];

        const searchWith = async (
            truncated: boolean,
            warnings: Array<{ code: string; message: string }>
        ) => {
            setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
            (searchNlp as jest.Mock).mockResolvedValue([
                true,
                { ...nlpEnvelope(truncated), warnings },
            ]);
            const { container } = renderContainer();
            const input = await screen.findByPlaceholderText(
                "Describe what you are looking for..."
            );
            await userEvent.type(input, "red truck");
            await userEvent.click(screen.getByRole("button", { name: "Search" }));
            await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
            await screen.findByText("Truck");
            return container;
        };

        it("shows a rare warning in the Search notice above the results, in the page's words, and raises no toast (D77)", async () => {
            const container = await searchWith(false, [
                { code: "databases:none_accessible", message: noneAccessibleMessage },
            ]);
            const notice = searchNotice(container);
            expect(notice).not.toBeNull();
            expect(notice?.getElement()).toHaveAttribute("data-testid", "search-notice");
            expect(noticeLines(container)).toEqual([noneAccessibleText]);
            // An info notice with a named status icon and a named dismiss button.
            expect(notice?.getElement().querySelector('[aria-label="Info"]')).not.toBeNull();
            expect(notice?.findDismissButton()?.getElement()).toHaveAttribute(
                "aria-label",
                "Dismiss search notice"
            );
            // It sits above the results, in the results column.
            const table = createWrapper(container).findTable()?.getElement();
            const noticeElement = notice?.getElement();
            if (!table || !noticeElement) throw new Error("the notice or the results are missing");
            expect(
                noticeElement.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING
            ).toBeTruthy();
            // Nothing is toasted: no toast covers the title row's view switch or Clear all filters.
            expect(createWrapper(container).findFlashbar()).toBeNull();
            expect(screen.queryByText(noneAccessibleMessage)).toBeNull();
            expect(screen.queryByText(/caller/)).toBeNull();
            expect(screen.queryByText(segmentLine)).toBeNull();
        });

        it("shows each rare warning once, in the API's order, on its own line, and never the API's message, the object or a window-full message (D76, D77)", async () => {
            const container = await searchWith(true, [
                { code: "truncated:window", message: truncatedMessage },
                { code: "opensearch:fields_ignored", message: ignoredMessage },
                { code: "databases:none_accessible", message: noneAccessibleMessage },
                { code: "opensearch:fields_ignored", message: ignoredMessage },
            ]);
            expect(noticeLines(container)).toEqual([ignoredText, noneAccessibleText]);
            expect(toastHeaders(container)).toEqual([]);
            expect(screen.queryByText(new RegExp(ignoredMessage))).toBeNull();
            expect(screen.queryByText(/caller/)).toBeNull();
            expect(screen.queryByText(new RegExp(truncatedMessage))).toBeNull();
            expect(screen.queryByText(/\[object Object\]/)).toBeNull();
            // The filled window is stated under the results instead.
            expect(screen.getByText(truncatedLine)).toBeInTheDocument();
        });

        it("shows the API's message in the Search notice for a code the page does not know", async () => {
            const container = await searchWith(false, [
                { code: "future:code", message: "A new kind of notice" },
            ]);
            expect(noticeLines(container)).toEqual(["A new kind of notice"]);
            expect(toastHeaders(container)).toEqual([]);
        });

        it("shows no Search notice for a truncated:window warning alone; the page states it", async () => {
            const container = await searchWith(true, [
                { code: "truncated:window", message: truncatedMessage },
            ]);
            expect(screen.getByText(truncatedLine)).toBeInTheDocument();
            await new Promise((resolve) => setTimeout(resolve, 300));
            expect(searchNotice(container)).toBeNull();
            expect(screen.queryByText("Search notice")).toBeNull();
            expect(screen.queryByText(truncatedMessage)).toBeNull();
            // No notice, so the announcement is the count alone.
            expect(countAnnouncement().textContent).toBe("Found 2+ results");
        });

        it("shows no Search notice for a segments:window_full warning alone and states it on the page", async () => {
            const container = await searchWith(false, [
                { code: "segments:window_full", message: segmentMessage },
            ]);
            expect(screen.getByText(segmentLine)).toBeInTheDocument();
            // A segment window is not a truncated answer: no whole-file line. The count is the route's
            // minimum (D75, approximate matching).
            expect(screen.queryByText(truncatedLine)).toBeNull();
            expect(countAnnouncement().textContent).toBe("Found 2+ results");
            await new Promise((resolve) => setTimeout(resolve, 300));
            expect(searchNotice(container)).toBeNull();
            expect(screen.queryByText("Search notice")).toBeNull();
            expect(screen.queryByText(segmentMessage)).toBeNull();
        });

        it("announces the count, then the notice's text, in the polite live region (D77)", async () => {
            // The no-access answer: nothing found, an exact zero, and the one rare warning.
            setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
            (searchNlp as jest.Mock).mockResolvedValue([
                true,
                {
                    ...nlpEnvelope(false),
                    hits: { total: { value: 0, relation: "eq" }, hits: [] },
                    aggregationTotal: 0,
                    warnings: [
                        { code: "databases:none_accessible", message: noneAccessibleMessage },
                    ],
                },
            ]);
            const { container } = renderContainer();
            const input = await screen.findByPlaceholderText(
                "Describe what you are looking for..."
            );
            await userEvent.type(input, "gearbox{Enter}");
            await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
            await screen.findByText("No matches");
            const announcement = `Found 0 results. ${noneAccessibleText}`;
            await waitFor(() => expect(countAnnouncement().textContent).toBe(announcement));
            await expectPoliteAnnouncement(announcement.replace(/\.$/, ""));
            expect(noticeLines(container)).toEqual([noneAccessibleText]);
            expect(screen.getByText("0 results")).toBeInTheDocument();
        });

        it("dismissing hides the notice for that answer only; the next answer shows its notice again (D77)", async () => {
            setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
            const answer = () => ({
                ...nlpEnvelope(false),
                warnings: [{ code: "databases:none_accessible", message: noneAccessibleMessage }],
            });
            (searchNlp as jest.Mock).mockImplementation(async () => [true, answer()]);
            const { container } = renderContainer();
            const input = await screen.findByPlaceholderText(
                "Describe what you are looking for..."
            );
            await userEvent.type(input, "red truck");
            await userEvent.click(screen.getByRole("button", { name: "Search" }));
            await screen.findByText("Truck");
            expect(noticeLines(container)).toEqual([noneAccessibleText]);

            dismissNotice(container);
            await waitFor(() => expect(searchNotice(container)).toBeNull());
            // The same answer stays on screen, and its notice stays dismissed.
            await new Promise((resolve) => setTimeout(resolve, 700));
            expect(searchNotice(container)).toBeNull();
            expect(screen.getByText("Truck")).toBeInTheDocument();

            // A new search is a new answer: its notice shows.
            await userEvent.click(screen.getByRole("button", { name: "Search" }));
            await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(2));
            await waitFor(() => expect(noticeLines(container)).toEqual([noneAccessibleText]));

            // Clear all filters drops the answer, and the next search's notice shows again.
            dismissNotice(container);
            await waitFor(() => expect(searchNotice(container)).toBeNull());
            await userEvent.click(screen.getByRole("button", { name: "Clear all filters" }));
            expect(searchNotice(container)).toBeNull();
            await userEvent.type(
                screen.getByPlaceholderText("Describe what you are looking for..."),
                "red truck"
            );
            await userEvent.click(screen.getByRole("button", { name: "Search" }));
            await waitFor(() => expect(noticeLines(container)).toEqual([noneAccessibleText]));
            // "Search cleared" is still a toast; the notice never is.
            expect(toastHeaders(container)).not.toContain("Search notice");
        });

        it("never shows a Search notice for a keyword answer, and a mode switch brings the next natural-language notice back (D77)", async () => {
            setFeatures(["VECTORSEARCH"]);
            // A keyword answer carrying a warnings entry still shows no notice.
            (searchAssets as jest.Mock).mockResolvedValue([
                true,
                {
                    ...keywordRows,
                    warnings: [
                        { code: "databases:none_accessible", message: noneAccessibleMessage },
                    ],
                },
            ]);
            (searchNlp as jest.Mock).mockImplementation(async () => [
                true,
                {
                    ...nlpEnvelope(false),
                    warnings: [{ code: "opensearch:fields_ignored", message: ignoredMessage }],
                },
            ]);
            const { container } = renderContainer();
            await screen.findByText("Keyword Alpha");
            await waitFor(() => expect(countAnnouncement().textContent).toBe("Found 2 results"));
            expect(searchNotice(container)).toBeNull();

            await userEvent.click(screen.getByTestId("nlp"));
            const input = await screen.findByPlaceholderText(
                "Describe what you are looking for..."
            );
            await userEvent.type(input, "red truck");
            await userEvent.click(screen.getByRole("button", { name: "Search" }));
            await screen.findByText("Truck");
            expect(noticeLines(container)).toEqual([ignoredText]);
            dismissNotice(container);
            await waitFor(() => expect(searchNotice(container)).toBeNull());

            // Back to keyword: its answer has no notice.
            await userEvent.click(screen.getByTestId("keyword"));
            await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(2), { timeout: 3000 });
            await screen.findByText("Keyword Alpha");
            expect(searchNotice(container)).toBeNull();

            // And to natural language again: the mode switch re-runs the query, a new answer whose
            // notice shows although the last one was dismissed.
            await userEvent.click(screen.getByTestId("nlp"));
            await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(2), { timeout: 3000 });
            await waitFor(() => expect(noticeLines(container)).toEqual([ignoredText]));
            expect(toastHeaders(container)).toEqual([]);
        });
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

describe("ModernSearchContainer switching between keyword and natural language", () => {
    const idleTitle = "Describe what you are looking for";

    beforeEach(() => {
        jest.clearAllMocks();
        clearPreferenceCookie();
        setFeatures(["VECTORSEARCH"]);
        (searchAssets as jest.Mock).mockResolvedValue([true, keywordRows]);
        (searchNlp as jest.Mock).mockResolvedValue([true, nlpEnvelope(false)]);
    });

    it("an empty query in natural language shows the idle state, not the keyword listing", async () => {
        renderContainer();
        await screen.findByText("Keyword Alpha");
        expect(screen.getByText("2 results")).toBeInTheDocument();

        await userEvent.click(screen.getByTestId("nlp"));
        await waitFor(() =>
            expect(screen.getByTestId("nlp")).toHaveAttribute("aria-pressed", "true")
        );
        // Checked in the render that follows the switch, before the auto-refresh could run, and
        // again after its window: no keyword rows, no count badge, no request.
        expect(screen.getByText(idleTitle)).toBeInTheDocument();
        expect(screen.queryByText("Keyword Alpha")).toBeNull();
        expect(screen.queryByText("2 results")).toBeNull();
        await new Promise((resolve) => setTimeout(resolve, 700));
        expect(screen.getByText(idleTitle)).toBeInTheDocument();
        expect(screen.queryByText("Keyword Alpha")).toBeNull();
        expect(screen.queryByText("2 results")).toBeNull();
        expect(searchNlp).not.toHaveBeenCalled();
        expect(searchAssets).toHaveBeenCalledTimes(1);
    });

    it("a keyword search that answers after the switch does not fill the natural-language table", async () => {
        let answerKeyword: (value: unknown) => void = () => undefined;
        (searchAssets as jest.Mock).mockReturnValueOnce(
            new Promise((resolve) => {
                answerKeyword = resolve;
            })
        );
        renderContainer();
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));
        await userEvent.click(screen.getByTestId("nlp"));
        await waitFor(() =>
            expect(screen.getByTestId("nlp")).toHaveAttribute("aria-pressed", "true")
        );
        await act(async () => {
            answerKeyword([true, keywordRows]);
        });
        await new Promise((resolve) => setTimeout(resolve, 700));
        expect(screen.getByText(idleTitle)).toBeInTheDocument();
        expect(screen.queryByText("Keyword Alpha")).toBeNull();
        expect(screen.queryByText("2 results")).toBeNull();
        expect(searchNlp).not.toHaveBeenCalled();
    });

    it("switching back to keyword lists everything in scope again", async () => {
        renderContainer();
        await screen.findByText("Keyword Alpha");
        await userEvent.click(screen.getByTestId("nlp"));
        await screen.findByText(idleTitle);
        await userEvent.click(screen.getByTestId("keyword"));
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(2), { timeout: 3000 });
        await screen.findByText("Keyword Alpha");
        expect(screen.getByText("2 results")).toBeInTheDocument();
        expect(screen.queryByText(idleTitle)).toBeNull();
        expect(searchNlp).not.toHaveBeenCalled();
    });
});

describe("ModernSearchContainer natural-language File Type filter", () => {
    const fileHit = (key: string, ext: string, score: number) => ({
        _id: `db1#a1#${key}#v1`,
        _score: score,
        _index_type: "file",
        _source: {
            str_databaseid: "db1",
            str_assetid: "a1",
            str_assetname: "Tides",
            str_key: key,
            str_fileext: ext,
        },
        _vector: {
            distance: 1 - score,
            embeddingModelId: "m",
            sourceModalities: ["text"],
            indexedAt: "t",
            fileClass: ext === "glb" ? "mesh" : "document",
            segmentHits: 0,
            bestSegment: null,
        },
    });

    // The natural-language envelope with the terms buckets the keyword route also returns; its
    // total is a minimum, as the route reports every total that is not zero (D75).
    const nlpFiles = (
        hits: ReturnType<typeof fileHit>[],
        fileTypes: Array<{ key: string; doc_count: number }>
    ) => ({
        ...nlpEnvelope(false),
        hits: { total: { value: hits.length, relation: "gte" }, hits },
        aggregations: {
            str_fileext: { buckets: fileTypes },
            str_databaseid: { buckets: [{ key: "db1", doc_count: hits.length }] },
        },
        aggregationTotal: hits.length,
    });

    const fileTypeMultiselect = (container: HTMLElement) => {
        const field = createWrapper(container)
            .findAllFormFields()
            .find((f) => f.findLabel()?.getElement().textContent === "File Type");
        const multiselect = field?.findControl()?.findMultiselect();
        if (!multiselect) throw new Error("File Type multiselect not rendered");
        return multiselect;
    };

    beforeEach(() => {
        jest.clearAllMocks();
        clearPreferenceCookie();
    });

    it("offers the answer's file types and narrows the next search by the chosen one", async () => {
        setFeatures(["VECTORSEARCH"]);
        // A keyword Files listing answers with its own type bucket first, which the panel caches.
        (searchAssets as jest.Mock).mockImplementation(async (body: any) => [
            true,
            body.entityTypes?.includes("file")
                ? {
                      ...keywordEnvelope,
                      aggregations: {
                          str_fileext: { buckets: [{ key: "pdf", doc_count: 7 }] },
                      },
                  }
                : keywordEnvelope,
        ]);
        const hits = [
            fileHit("/tides/gauge-a.txt", "txt", 0.9),
            fileHit("/tides/gauge-b.txt", "txt", 0.8),
            fileHit("/tides/buoy.glb", "glb", 0.7),
        ];
        (searchNlp as jest.Mock)
            .mockResolvedValueOnce([
                true,
                nlpFiles(hits, [
                    { key: "txt", doc_count: 2 },
                    { key: "glb", doc_count: 1 },
                ]),
            ])
            .mockResolvedValueOnce([
                true,
                nlpFiles(hits.slice(0, 2), [{ key: "txt", doc_count: 2 }]),
            ]);

        const { container } = renderContainer();
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));
        await userEvent.click(screen.getByTestId("file"));
        await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(2), { timeout: 3000 });
        await userEvent.click(screen.getByTestId("nlp"));
        await waitFor(() =>
            expect(screen.getByTestId("nlp")).toHaveAttribute("aria-pressed", "true")
        );
        // `Search inside files` cleared is a search option; the answer below is still unfiltered.
        await userEvent.click(screen.getByLabelText("Search inside files"));
        await userEvent.type(
            screen.getByPlaceholderText("Describe what you are looking for..."),
            "tide gauge calibration"
        );
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(1));
        await screen.findByText("/tides/buoy.glb");

        const fileTypes = fileTypeMultiselect(container);
        fileTypes.openDropdown();
        expect(
            fileTypes
                .findDropdown()
                .findOptions()
                .map((option) => option.findLabel().getElement().textContent)
        ).toEqual(["glb (1+)", "txt (2+)"]);
        await act(async () => {
            fileTypes.selectOptionByValue("txt");
        });

        await waitFor(() => expect(searchNlp).toHaveBeenCalledTimes(2), { timeout: 3000 });
        expect((searchNlp as jest.Mock).mock.calls[1][0]).toMatchObject({
            query: "tide gauge calibration",
            entityTypes: ["file"],
            includeSegments: false,
            fileExtensions: ["txt"],
        });
        await waitFor(() => expect(screen.queryByText("/tides/buoy.glb")).toBeNull());
        expect(screen.getByText("/tides/gauge-a.txt")).toBeInTheDocument();
        expect(screen.getByText("/tides/gauge-b.txt")).toBeInTheDocument();
    });
});

describe("ModernSearchContainer natural-language query length", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        clearPreferenceCookie();
        setFeatures(["NOOPENSEARCH", "VECTORSEARCH"]);
        (searchNlp as jest.Mock).mockResolvedValue([true, nlpEnvelope(false)]);
    });

    it("does not send a query past the limit when a filter change re-runs the search", async () => {
        renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        fireEvent.change(input, { target: { value: "a".repeat(1001) } });
        expect(
            screen.getByText(/Natural-language queries can be up to 1,000 characters\./)
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
        await userEvent.click(screen.getByLabelText("Search inside files"));
        await new Promise((resolve) => setTimeout(resolve, 700));
        expect(searchNlp).not.toHaveBeenCalled();
    });

    it("shows the route's own message when it refuses a query", async () => {
        const refusal = "The query must not be blank.";
        (searchNlp as jest.Mock).mockResolvedValue([false, refusal]);
        renderContainer();
        const input = await screen.findByPlaceholderText("Describe what you are looking for...");
        await userEvent.type(input, "red truck");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await screen.findByText("Search failed");
        expect(screen.getByText(refusal)).toBeInTheDocument();
    });
});
