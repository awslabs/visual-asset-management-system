/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A file opened by its direct path reads the file's details first. When that read fails, the page
 * names the cause: a file that does not exist, a refusal, or a request that got no answer, which it
 * offers to try again. The requests run through the real service layer, so these cases exercise
 * how the page reads an API rejection rather than a stubbed helper's answer.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
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
    default: () => null,
}));

const mockGet = apiClient.get as jest.Mock;

const FILE_INFO_PATH = "database/db1/assets/a1/fileInfo";
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

/** Answers the asset read with ASSET and each file-details read with the next answer; the last repeats. */
function answerFileInfo(...answers: Answer[]) {
    mockGet.mockImplementation((path: string) => {
        if (path === FILE_INFO_PATH) {
            const next = (answers.length > 1 && answers.shift()) || answers[0];
            return next();
        }
        return Promise.resolve(ASSET);
    });
}

const fileInfoRequests = () =>
    mockGet.mock.calls.filter(([path]: [string]) => path === FILE_INFO_PATH).length;

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
