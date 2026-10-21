/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import ViewFile from "./ViewFile";
import { fetchFileInfo } from "../../services/APIService";
import { ApiError } from "../../services/apiClient";

jest.mock("../../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(),
}));
jest.mock("../../services/APIService", () => ({
    fetchAsset: jest.fn(() => Promise.resolve(false)),
    fetchFileInfo: jest.fn(),
    isRequestBlocked: jest.requireActual("../../services/apiClient").isRequestBlocked,
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

const BLOCKED_MESSAGE = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";

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

describe("ViewFile direct-path refusals", () => {
    beforeEach(() => jest.clearAllMocks());

    it("shows a blocked request's message, not the permission sentence", async () => {
        (fetchFileInfo as jest.Mock).mockRejectedValue(
            new ApiError(BLOCKED_MESSAGE, 403, {
                message: "Request blocked by the deployment's AWS WAF web ACL",
                blockedBy: "waf",
                requestId: "r-1",
            })
        );
        openByPath();
        expect(await screen.findByText(BLOCKED_MESSAGE)).toBeInTheDocument();
        expect(screen.queryByText("You don't have permission to access this file.")).toBeNull();
    });

    it("shows the permission sentence for a permission denial", async () => {
        (fetchFileInfo as jest.Mock).mockRejectedValue(
            new ApiError("Not Authorized", 403, { message: "Not Authorized" })
        );
        openByPath();
        expect(
            await screen.findByText("You don't have permission to access this file.")
        ).toBeInTheDocument();
    });
});
