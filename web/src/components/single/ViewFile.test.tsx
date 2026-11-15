/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A file opened by its direct path reads the file's details first. When that read fails, the page
 * names the cause: a file that does not exist, a refusal, or a request that got no answer, which it
 * offers to try again, with one lookup per Retry however fast it is pressed. For an archived file
 * it says so and where the file can be unarchived. The page also reads the asset's details; when
 * only that read fails it keeps the file, says why the details are missing and holds the
 * visualizer back, since it cannot tell whether the asset is distributable. The requests run
 * through the real service layer, so these cases exercise how the page reads an API rejection
 * rather than a stubbed helper's answer.
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
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
const BLOCKED_MESSAGE = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";
const NOT_FOUND = "The file you're looking for doesn't exist or has been moved.";
const UNREACHABLE =
    "Failed to load the file. The request could not reach the service. It may have been blocked by a firewall or the network.";

type Answer = () => Promise<unknown>;
const noResponse: Answer = () => Promise.reject(new TypeError("Failed to fetch"));
const rejectWith =
    (error: ApiError): Answer =>
    () =>
        Promise.reject(error);
const fileDetails: Answer = () => Promise.resolve(FILE_INFO);
const assetRecord: Answer = () => Promise.resolve(ASSET);

// How every asset read is answered; a test changes it to fail the read or to let a retry through.
let assetAnswer: Answer = assetRecord;

/** Answers each file-details read with the next answer (the last repeats) and the asset read with assetAnswer. */
function answerFileInfo(...answers: Answer[]) {
    mockGet.mockImplementation((path: string) => {
        if (path === FILE_INFO_PATH) {
            const next = (answers.length > 1 && answers.shift()) || answers[0];
            return next();
        }
        if (path === ASSET_PATH) {
            return assetAnswer();
        }
        return Promise.reject(new Error(`unexpected request ${path}`));
    });
}

beforeEach(() => {
    assetAnswer = assetRecord;
});

const fileInfoRequests = () =>
    mockGet.mock.calls.filter(([path]: [string]) => path === FILE_INFO_PATH).length;

/** An answer the test settles when it chooses. */
function pending() {
    let resolve!: (value: unknown) => void;
    const promise = new Promise((settle) => {
        resolve = settle;
    });
    return { answer: () => promise, resolve };
}

/**
 * Presses a control twice as a double-click does. Each click is its own browser event, and React
 * renders what the first press's handler set before the second one arrives; a state update that an
 * effect makes in that render waits for a later task. act() would apply every update between the
 * two presses and hide that gap, so the clicks are dispatched outside it. The pointer has not
 * moved, so the second click goes to the same control; once that control is gone, nothing gets it.
 */
async function doubleClick(control: HTMLElement) {
    const scope = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const actEnvironment = scope.IS_REACT_ACT_ENVIRONMENT;
    scope.IS_REACT_ACT_ENVIRONMENT = false;
    try {
        for (const detail of [1, 2]) {
            control.dispatchEvent(
                new MouseEvent("click", { bubbles: true, cancelable: true, detail })
            );
            // React renders a click's updates in a microtask, before the next event.
            await Promise.resolve();
        }
    } finally {
        scope.IS_REACT_ACT_ENVIRONMENT = actEnvironment;
    }
}

/** Opens a file by its direct path, which reads the file's details before anything else. */
const openByPath = () =>
    render(
        <MemoryRouter initialEntries={["/databases/db1/assets/a1/file/model.glb"]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId/file/*"
                    element={<ViewFile />}
                />
            </Routes>
        </MemoryRouter>
    );

describe("file page when the file cannot be loaded", () => {
    beforeEach(() => jest.clearAllMocks());

    it("says the service could not be reached when the request gets no response", async () => {
        answerFileInfo(noResponse);
        openByPath();

        expect(await screen.findByText(UNREACHABLE)).toBeInTheDocument();
        expect(screen.getByText("File Unavailable")).toBeInTheDocument();
        expect(screen.queryByText("File Not Found")).toBeNull();
        expect(screen.queryByText(/Failed to fetch/)).toBeNull();
        expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
        expect(screen.getByRole("link", { name: `Return to ${Synonyms.asset}` })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/a1"
        );
    });

    it.each([
        [
            "a missing file (400)",
            new ApiError("File not found.", 400, { message: "File not found." }),
        ],
        [
            "a missing asset (404)",
            new ApiError("Asset not found", 404, { message: "Asset not found" }),
        ],
    ])("keeps File Not Found for %s", async (_case, error) => {
        answerFileInfo(rejectWith(error));
        openByPath();

        expect(await screen.findByText(NOT_FOUND)).toBeInTheDocument();
        expect(screen.getByText("File Not Found")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    });

    it("says the user may not view the file for a permission denial", async () => {
        answerFileInfo(
            rejectWith(new ApiError("Not Authorized", 403, { message: "Not Authorized" }))
        );
        openByPath();

        expect(
            await screen.findByText("Your role does not allow you to view this file.")
        ).toBeInTheDocument();
        expect(screen.getByText("Access Denied")).toBeInTheDocument();
        expect(screen.queryByText("File Not Found")).toBeNull();
        expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    });

    it("says an archived file is archived and where to unarchive it, without Retry", async () => {
        answerFileInfo(() => Promise.resolve({ ...FILE_INFO, isArchived: true }));
        openByPath();

        expect(await screen.findByText("File Archived")).toBeInTheDocument();
        expect(
            screen.getByText(
                `This file is archived and cannot be viewed. It can be unarchived from the ${Synonyms.asset}'s File Manager after turning on "Show archived files".`
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(/part of an archived/)).toBeNull();
        expect(screen.queryByText("File Unavailable")).toBeNull();
        expect(screen.queryByText("File Not Found")).toBeNull();
        expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
        expect(screen.getByRole("link", { name: `Return to ${Synonyms.asset}` })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/a1"
        );
        expect(screen.queryByTestId("viewer")).toBeNull();
        expect(fileInfoRequests()).toBe(1);
    });

    it("shows a blocked request's message and request ID, not a permission denial", async () => {
        answerFileInfo(
            rejectWith(
                new ApiError(BLOCKED_MESSAGE, 403, {
                    message: "Request blocked by the deployment's AWS WAF web ACL",
                    blockedBy: "waf",
                    requestId: "r-1",
                })
            )
        );
        openByPath();

        expect(
            await screen.findByText(`Failed to load the file. ${BLOCKED_MESSAGE}`)
        ).toBeInTheDocument();
        expect(screen.getByText("File Unavailable")).toBeInTheDocument();
        expect(screen.queryByText("Access Denied")).toBeNull();
    });

    it("loads the file when Retry succeeds after a request got no response", async () => {
        answerFileInfo(noResponse, fileDetails);
        openByPath();

        fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

        expect(
            await screen.findByRole("heading", { level: 1, name: /model\.glb/ })
        ).toBeInTheDocument();
        expect(screen.queryByText("File Unavailable")).toBeNull();
        expect(fileInfoRequests()).toBe(2);
    });

    it("starts one lookup when Retry is double-clicked", async () => {
        const retried = pending();
        answerFileInfo(noResponse, retried.answer);
        openByPath();

        await doubleClick(await screen.findByRole("button", { name: "Retry" }));

        // The first press shows the loading state, so the second press starts no other lookup.
        expect(fileInfoRequests()).toBe(2);
        expect(screen.getByText("Loading file...")).toBeInTheDocument();

        await act(async () => retried.resolve(FILE_INFO));

        expect(
            await screen.findByRole("heading", { level: 1, name: /model\.glb/ })
        ).toBeInTheDocument();
        expect(screen.queryByText("File Unavailable")).toBeNull();
        expect(fileInfoRequests()).toBe(2);
    });

    it("shows the file when its details load", async () => {
        answerFileInfo(fileDetails);
        openByPath();

        expect(
            await screen.findByRole("heading", { level: 1, name: /model\.glb/ })
        ).toBeInTheDocument();
        expect(screen.queryByText("File Unavailable")).toBeNull();
        expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
        expect(fileInfoRequests()).toBe(1);
    });
});

describe("file page when the asset's details cannot be loaded", () => {
    beforeEach(() => jest.clearAllMocks());

    const HELD_BACK = `Visualizer is not available because this ${Synonyms.asset}'s details could not be loaded.`;
    const NOT_DISTRIBUTABLE = `Visualizer is not available because this ${Synonyms.asset} is not marked as distributable.`;
    const assetCrumb = (name: string) =>
        screen
            .getAllByRole("link", { name })
            .find((link) => link.getAttribute("href") === "#/databases/db1/assets/a1");
    const fileHeading = () => screen.findByRole("heading", { level: 1, name: /model\.glb/ });
    const visualizerControl = (container: HTMLElement) =>
        container.querySelector(".visualizer-segment-control");

    it.each([
        [
            "no response",
            noResponse,
            `${Synonyms.Asset} Details Unavailable`,
            `Failed to load the ${Synonyms.asset}'s details. The request could not reach the service. It may have been blocked by a firewall or the network.`,
            true,
        ],
        [
            "a blocked request",
            rejectWith(
                new ApiError(BLOCKED_MESSAGE, 403, {
                    message: "Request blocked by the deployment's AWS WAF web ACL",
                    blockedBy: "waf",
                    requestId: "r-1",
                })
            ),
            `${Synonyms.Asset} Details Unavailable`,
            `Failed to load the ${Synonyms.asset}'s details. ${BLOCKED_MESSAGE}`,
            true,
        ],
        [
            "a permission denial (403)",
            rejectWith(new ApiError("Not Authorized", 403, { message: "Not Authorized" })),
            `${Synonyms.Asset} Access Denied`,
            `Your role does not allow you to view this ${Synonyms.asset}'s details.`,
            false,
        ],
        [
            "a deleted asset (404)",
            rejectWith(new ApiError("Asset not found", 404, { message: "Asset not found" })),
            `${Synonyms.Asset} Not Found`,
            `This ${Synonyms.asset} was not found. It may have been deleted.`,
            false,
        ],
    ])(
        "keeps the file and holds the visualizer back after %s",
        async (_case, answer, header, text, retryable) => {
            assetAnswer = answer;
            answerFileInfo(fileDetails);
            const { container } = openByPath();

            expect(await fileHeading()).toBeInTheDocument();
            expect(await screen.findByText(header)).toBeInTheDocument();
            expect(screen.getByText(text)).toBeInTheDocument();
            expect(!!screen.queryByRole("button", { name: "Retry" })).toBe(retryable);
            // The breadcrumb still links back to the asset, by its ID.
            expect(assetCrumb("a1")).toBeDefined();
            // Fail closed: no viewer or view selector, and the reason in the viewer's place.
            expect(screen.getByText(HELD_BACK)).toBeInTheDocument();
            expect(screen.queryByTestId("viewer")).toBeNull();
            expect(visualizerControl(container)).toBeNull();
            expect(screen.queryByText(NOT_DISTRIBUTABLE)).toBeNull();
            expect(screen.queryByText("File Unavailable")).toBeNull();
            expect(screen.queryByText("File Not Found")).toBeNull();
        }
    );

    it("shows the asset and the visualizer when Retry loads the asset's details", async () => {
        assetAnswer = noResponse;
        answerFileInfo(fileDetails);
        const { container } = openByPath();

        const retry = await screen.findByRole("button", { name: "Retry" });
        assetAnswer = assetRecord;
        fireEvent.click(retry);

        expect(await screen.findByTestId("viewer")).toBeInTheDocument();
        expect(screen.queryByText(`${Synonyms.Asset} Details Unavailable`)).toBeNull();
        expect(screen.queryByText(HELD_BACK)).toBeNull();
        expect(assetCrumb("Asset One")).toBeDefined();
        expect(visualizerControl(container)).not.toBeNull();
    });

    it("names the asset and shows the visualizer when the asset's details load", async () => {
        answerFileInfo(fileDetails);
        const { container } = openByPath();

        expect(await fileHeading()).toBeInTheDocument();
        expect(await screen.findByTestId("viewer")).toBeInTheDocument();
        expect(assetCrumb("Asset One")).toBeDefined();
        expect(visualizerControl(container)).not.toBeNull();
        expect(screen.queryByText(HELD_BACK)).toBeNull();
        expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    });

    it("keeps the not-distributable notice for an asset that is not distributable", async () => {
        assetAnswer = () => Promise.resolve({ ...ASSET, isDistributable: false });
        answerFileInfo(fileDetails);
        const { container } = openByPath();

        expect(await screen.findByText(NOT_DISTRIBUTABLE)).toBeInTheDocument();
        expect(assetCrumb("Asset One")).toBeDefined();
        expect(screen.queryByTestId("viewer")).toBeNull();
        expect(visualizerControl(container)).toBeNull();
        expect(screen.queryByText(HELD_BACK)).toBeNull();
    });
});
