/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page reads the asset's details beside the file, and shows the visualizer only once they
 * load, since it cannot tell otherwise whether the asset is distributable. Opened from the File
 * Manager, the page gets the file from the navigation state, so the asset lookup is its only request
 * and starts at mount. Opened by its direct path, the page looks the asset up at mount and again
 * once the file has loaded, and the last answer decides what the page shows. The requests run
 * through the real service layer, with only the HTTP client stubbed.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import ViewFile from "./ViewFile";
import { apiClient, ApiError } from "../../services/apiClient";
import Synonyms from "../../synonyms";

jest.mock("../../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(),
}));
jest.mock("../../services/apiClient", () => ({
    ...jest.requireActual("../../services/apiClient"),
    apiClient: { get: jest.fn() },
}));
jest.mock("../../services/FileOperationsService", () => ({ archiveFile: jest.fn() }));
// ViewFile asks the viewer registry which modes a file opens in; the registry pulls in Vite's
// import.meta.glob, so the registry modules are stubbed: every file opens in visualize mode only.
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    availableModesForFiles: () => ({ visualize: true, compare: false }),
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));
jest.mock("../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));
jest.mock("../filemanager/components/FileVersionsTable", () => ({
    FileVersionsTable: () => null,
}));
jest.mock("../../visualizerPlugin/components/DynamicViewer", () => ({
    __esModule: true,
    default: () => <div data-testid="viewer" />,
}));

const mockGet = apiClient.get as jest.Mock;

const FILE_INFO_PATH = "database/db1/assets/a1/fileInfo";
const ASSET_PATH = "database/db1/assets/a1";
const ASSET = { assetId: "a1", databaseId: "db1", assetName: "Asset One", isDistributable: true };
const FILE_INFO = {
    key: "a1/model.glb",
    fileName: "model.glb",
    versions: [{ versionId: "v1", isLatest: true }],
};
const UNAVAILABLE = `${Synonyms.Asset} Details Unavailable`;
const UNREACHABLE = `Failed to load the ${Synonyms.asset}'s details. The request could not reach the service. It may have been blocked by a firewall or the network.`;
const HELD_BACK = `Visualizer is not available because this ${Synonyms.asset}'s details could not be loaded.`;

type Answer = () => Promise<unknown>;
const noResponse: Answer = () => Promise.reject(new TypeError("Failed to fetch"));
const assetRecord: Answer = () => Promise.resolve(ASSET);
const refused: Answer = () =>
    Promise.reject(new ApiError("Not Authorized", 403, { message: "Not Authorized" }));

/** An answer the test settles when it chooses. */
function pending() {
    let resolve!: (value: unknown) => void;
    const promise = new Promise((settle) => {
        resolve = settle;
    });
    return { answer: () => promise, resolve };
}

// The asset reads are answered in turn (the last answer repeats); every file read with fileAnswer.
let assetAnswers: Answer[];
let fileAnswer: Answer;

beforeEach(() => {
    jest.clearAllMocks();
    document.title = "";
    fileAnswer = () => Promise.resolve(FILE_INFO);
    mockGet.mockImplementation((path: string) => {
        if (path === ASSET_PATH) {
            const next = (assetAnswers.length > 1 && assetAnswers.shift()) || assetAnswers[0];
            return next();
        }
        if (path === FILE_INFO_PATH) {
            return fileAnswer();
        }
        return Promise.reject(new Error(`unexpected request ${path}`));
    });
});

const requests = (path: string) =>
    mockGet.mock.calls.filter(([requested]: [string]) => requested === path).length;

const renderAt = (entry: Parameters<typeof MemoryRouter>[0]["initialEntries"]) =>
    render(
        <MemoryRouter initialEntries={entry}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId/file/*"
                    element={<ViewFile />}
                />
            </Routes>
        </MemoryRouter>
    );

/** The File Manager's View File: the URL names the file and version, the state carries the file. */
const openFromFileManager = () =>
    renderAt([
        {
            pathname: "/databases/db1/assets/a1/file/model.glb",
            search: "?version=v1",
            state: {
                filename: "model.glb",
                key: "a1/model.glb",
                isDirectory: false,
                versionId: "v1",
                isArchived: false,
            },
        },
    ]);

/** A link to the file by its path, which reads the file's details before showing the page. */
const openByPath = () => renderAt(["/databases/db1/assets/a1/file/model.glb"]);

const assetCrumbs = (name: string) =>
    screen
        .queryAllByRole("link", { name })
        .filter((link) => link.getAttribute("href") === "#/databases/db1/assets/a1");
const visualizerControl = (container: HTMLElement) =>
    container.querySelector(".visualizer-segment-control");
const retryButton = () => screen.getByRole("button", { name: "Retry" });

describe("file page opened from the File Manager when the asset's details cannot be loaded", () => {
    it.each([
        ["no response", noResponse, UNAVAILABLE, UNREACHABLE, true],
        [
            "a permission denial (403)",
            refused,
            `${Synonyms.Asset} Access Denied`,
            `Your role does not allow you to view this ${Synonyms.asset}'s details.`,
            false,
        ],
    ])(
        "keeps the file and holds the visualizer back after %s",
        async (_case, answer, header, text, retryable) => {
            assetAnswers = [answer];
            const { container } = openFromFileManager();

            expect(await screen.findByText(header)).toBeInTheDocument();
            expect(screen.getByText(text)).toBeInTheDocument();
            expect(
                screen.getByRole("heading", { level: 1, name: /model\.glb/ })
            ).toBeInTheDocument();
            expect(!!screen.queryByRole("button", { name: "Retry" })).toBe(retryable);
            // The breadcrumb still links back to the asset, by its ID.
            expect(assetCrumbs("a1")).toHaveLength(1);
            // Fail closed: the viewer that mounted before the answer is gone, and so are the selectors.
            expect(screen.getByText(HELD_BACK)).toBeInTheDocument();
            expect(screen.queryByTestId("viewer")).toBeNull();
            expect(visualizerControl(container)).toBeNull();
            // The file came with the navigation, so the asset lookup is the page's only request.
            expect(requests(ASSET_PATH)).toBe(1);
            expect(requests(FILE_INFO_PATH)).toBe(0);
        }
    );

    it("shows Retry as loading while the lookup runs again, then shows the asset", async () => {
        const retried = pending();
        assetAnswers = [noResponse, retried.answer];
        const { container } = openFromFileManager();

        expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
        expect(retryButton()).not.toHaveAttribute("aria-disabled");
        fireEvent.click(retryButton());

        expect(retryButton()).toHaveAttribute("aria-disabled", "true");
        expect(requests(ASSET_PATH)).toBe(2);
        // A second press while the lookup runs does not start another one.
        fireEvent.click(retryButton());
        expect(requests(ASSET_PATH)).toBe(2);

        await act(async () => retried.resolve(ASSET));

        expect(await screen.findByTestId("viewer")).toBeInTheDocument();
        expect(screen.queryByText(UNAVAILABLE)).toBeNull();
        expect(screen.queryByText(HELD_BACK)).toBeNull();
        expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
        expect(assetCrumbs("Asset One")).toHaveLength(1);
        expect(visualizerControl(container)).not.toBeNull();
        expect(requests(ASSET_PATH)).toBe(2);
    });

    it("ends the loading state and keeps the warning when Retry fails again", async () => {
        const retried = pending();
        assetAnswers = [noResponse, () => retried.answer().then(() => noResponse())];
        openFromFileManager();

        expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
        fireEvent.click(retryButton());
        expect(retryButton()).toHaveAttribute("aria-disabled", "true");

        await act(async () => retried.resolve(undefined));

        await waitFor(() => expect(retryButton()).not.toHaveAttribute("aria-disabled"));
        expect(screen.getByText(UNREACHABLE)).toBeInTheDocument();
        expect(screen.getByText(HELD_BACK)).toBeInTheDocument();
        expect(screen.queryByTestId("viewer")).toBeNull();
        expect(requests(ASSET_PATH)).toBe(2);
    });
});

describe("file page opened by its path when a later asset lookup fails", () => {
    it("drops the asset it had and holds the visualizer back: the last answer wins", async () => {
        const file = pending();
        fileAnswer = file.answer;
        assetAnswers = [assetRecord, noResponse];
        const { container } = openByPath();

        // The lookup made at mount answers with the record while the file is still loading.
        await waitFor(() => expect(document.title).toContain("Asset One"));
        expect(screen.getByText("Loading file...")).toBeInTheDocument();
        expect(requests(ASSET_PATH)).toBe(1);

        // The file's arrival looks the asset up again, and that lookup gets no response.
        await act(async () => file.resolve(FILE_INFO));

        expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
        expect(requests(ASSET_PATH)).toBe(2);
        expect(screen.getByText(UNREACHABLE)).toBeInTheDocument();
        expect(screen.getByRole("heading", { level: 1, name: /model\.glb/ })).toBeInTheDocument();
        // The earlier record is gone: the breadcrumb and title fall back to the asset's ID.
        expect(assetCrumbs("a1")).toHaveLength(1);
        expect(assetCrumbs("Asset One")).toHaveLength(0);
        expect(document.title).not.toContain("Asset One");
        expect(screen.getByText(HELD_BACK)).toBeInTheDocument();
        expect(screen.queryByTestId("viewer")).toBeNull();
        expect(visualizerControl(container)).toBeNull();
        expect(retryButton()).toBeInTheDocument();
    });
});
