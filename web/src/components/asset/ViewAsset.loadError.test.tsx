/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * When the asset cannot be loaded the page says why, and the asset's details, files and metadata
 * (which start their own requests and, for location metadata, a map) are never mounted.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import * as APIService from "../../services/APIService";
import ViewAsset from "./ViewAsset";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";

/** What `GET /auth/routes/api/allowed` lists for a role allowed every asset page action. */
const ADMIN_ROUTES = [
    "/database",
    "/database/{databaseId}",
    "/assets",
    "/uploads",
    "/database/{databaseId}/assets/{assetId}",
    "/database/{databaseId}/assets/{assetId}/archiveAsset",
    "/database/{databaseId}/assets/{assetId}/metadata",
].map((path) => ({ path, methods: ["GET", "POST", "PUT", "DELETE"], category: "test" }));
const allowedRoutesResponse = (routes: typeof ADMIN_ROUTES) => [
    true,
    { routes, userId: "template-user" },
];
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    fetchAssetWithStatus: jest.fn(),
    fetchAssetLinks: jest.fn(() => Promise.resolve({})),
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../../services/AssetVersionService", () => ({
    fetchAllAssetVersions: jest.fn(() => Promise.resolve([true, { versions: [] }])),
}));
jest.mock("../../pages/AssetUpload/AssetUpload", () => ({ AssetDetail: {} }));

const mockMounted: string[] = [];
jest.mock("./AssetDetailsPane", () => ({
    __esModule: true,
    default: () => {
        mockMounted.push("pane");
        return null;
    },
}));
jest.mock("./TabbedContainer", () => ({
    __esModule: true,
    default: () => {
        mockMounted.push("tabs");
        return null;
    },
}));
jest.mock("../metadataV2", () => ({
    MetadataContainer: () => {
        mockMounted.push("metadata");
        return null;
    },
}));
jest.mock("../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../createupdate/UpdateAsset", () => ({ UpdateAsset: () => null }));

const mockFetchAsset = APIService.fetchAssetWithStatus as jest.Mock;
const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;

function renderPage() {
    render(
        <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
            <Routes>
                <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
            </Routes>
        </MemoryRouter>
    );
}

describe("asset page when the asset cannot be loaded", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        mockMounted.length = 0;
        mockFetchAllowedApiRoutes.mockResolvedValue(allowedRoutesResponse(ADMIN_ROUTES));
    });

    it("says access was refused for a 403", async () => {
        mockFetchAsset.mockResolvedValue({ message: "Not Authorized", status: 403 });
        renderPage();

        expect(
            await screen.findByText(
                `Access denied. Your role does not allow you to view this ${Synonyms.asset}.`
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(/invalid response/)).not.toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });

    it("keeps the not-found message for a missing asset", async () => {
        mockFetchAsset.mockResolvedValue({ message: "Asset not found in database", status: 404 });
        renderPage();

        expect(
            await screen.findByText(new RegExp(`${Synonyms.Asset} not found`))
        ).toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });

    it("shows a loading state until the asset arrives", async () => {
        mockFetchAsset.mockReturnValue(new Promise(() => undefined));
        renderPage();

        expect(await screen.findByText(`Loading ${Synonyms.asset}...`)).toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });
});
