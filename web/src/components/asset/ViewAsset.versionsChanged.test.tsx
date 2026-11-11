/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A change on the Versions tab (a version created, reverted to, edited or archived) reloads the asset
 * record and its version list, so the header's Current Last Version follows the Versions list instead
 * of keeping the version the page loaded with. The details pane and tabs are stubbed to record what
 * the page hands them.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import * as APIService from "../../services/APIService";
import * as AssetVersionService from "../../services/AssetVersionService";
import ViewAsset from "./ViewAsset";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import {
    ADMIN_ROUTES,
    allowedRoutesResponse,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";

jest.mock("../../services/APIService", () => ({
    fetchAssetWithStatus: jest.fn(),
    fetchAssetLinks: jest.fn(() => Promise.resolve({})),
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../../services/AssetVersionService", () => ({
    fetchAllAssetVersions: jest.fn(),
}));
jest.mock("../../pages/AssetUpload/AssetUpload", () => ({ AssetDetail: {} }));

jest.mock("./AssetDetailsPane", () => ({
    __esModule: true,
    default: (props: any) => (
        <div data-testid="pane">
            current v{props.asset?.currentVersion?.Version}; versions{" "}
            {props.versions.map((v: any) => v.Version).join(",") || "none"}
        </div>
    ),
}));
let mockVersionsChanged: (() => void) | undefined;
jest.mock("./TabbedContainer", () => ({
    __esModule: true,
    default: (props: any) => {
        mockVersionsChanged = props.onVersionsChanged;
        return null;
    },
}));
jest.mock("../metadataV2", () => ({ MetadataContainer: () => null }));
jest.mock("../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../createupdate/UpdateAsset", () => ({ UpdateAsset: () => null }));

const mockFetchAsset = APIService.fetchAssetWithStatus as jest.Mock;
const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;
const mockFetchVersions = AssetVersionService.fetchAllAssetVersions as jest.Mock;

const asset = (version: string) => ({
    assetId: "a1",
    assetName: "Bracket",
    databaseId: "db1",
    currentVersion: { Version: version },
});
const versionList = (...versions: string[]) => [
    true,
    { versions: versions.map((Version) => ({ Version, DateModified: `2026-01-0${Version}` })) },
];

function renderPage() {
    render(
        <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
            <Routes>
                <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
            </Routes>
        </MemoryRouter>
    );
}

describe("asset page after a change on the Versions tab", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        mockVersionsChanged = undefined;
        mockFetchAllowedApiRoutes.mockResolvedValue(allowedRoutesResponse(ADMIN_ROUTES));
        mockFetchAsset.mockResolvedValue([true, asset("1")]);
        mockFetchVersions.mockResolvedValue(versionList("1"));
    });

    it("shows the new current version and version list without a page reload", async () => {
        renderPage();
        await waitFor(() =>
            expect(screen.getByTestId("pane")).toHaveTextContent("current v1; versions 1")
        );
        expect(mockFetchAsset).toHaveBeenCalledTimes(1);

        mockFetchAsset.mockResolvedValue([true, asset("2")]);
        mockFetchVersions.mockResolvedValue(versionList("1", "2"));
        act(() => mockVersionsChanged?.());

        await waitFor(() =>
            expect(screen.getByTestId("pane")).toHaveTextContent("current v2; versions 2,1")
        );
        expect(mockFetchAsset).toHaveBeenCalledTimes(2);
        expect(mockFetchAsset).toHaveBeenLastCalledWith({
            databaseId: "db1",
            assetId: "a1",
            showArchived: true,
        });
    });

    it("keeps the details and the version list on screen while they reload", async () => {
        renderPage();
        await waitFor(() =>
            expect(screen.getByTestId("pane")).toHaveTextContent("current v1; versions 1")
        );

        mockFetchAsset.mockReturnValue(new Promise(() => undefined));
        mockFetchVersions.mockReturnValue(new Promise(() => undefined));
        act(() => mockVersionsChanged?.());

        expect(screen.getByTestId("pane")).toHaveTextContent("current v1; versions 1");
    });

    it("keeps the loaded details when the reload fails", async () => {
        renderPage();
        await waitFor(() =>
            expect(screen.getByTestId("pane")).toHaveTextContent("current v1; versions 1")
        );

        mockFetchAsset.mockResolvedValue([false, "Internal Server Error", 500]);
        act(() => mockVersionsChanged?.());
        await waitFor(() => expect(mockFetchAsset).toHaveBeenCalledTimes(2));
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 20));
        });

        expect(screen.getByTestId("pane")).toHaveTextContent("current v1");
    });
});
