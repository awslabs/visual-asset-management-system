/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * When the asset cannot be loaded the page says why, and the asset's details, files and metadata
 * (which start their own requests and, for location metadata, a map) are never mounted.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import * as APIService from "../../services/APIService";
import { apiClient, ApiError } from "../../services/apiClient";
import ViewAsset from "./ViewAsset";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import {
    ADMIN_ROUTES,
    allowedRoutesResponse,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";
import Synonyms from "../../synonyms";

// The asset request itself runs through the real service layer, so these cases exercise how the
// page reads an API rejection rather than a stubbed helper's answer.
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
jest.mock("../../services/APIService", () => ({
    ...jest.requireActual("../../services/APIService"),
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

const mockGet = apiClient.get as jest.Mock;
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
        mockGet.mockRejectedValue(
            new ApiError("Not Authorized", 403, { message: "Not Authorized" })
        );
        renderPage();

        expect(
            await screen.findByText(
                `Access denied. Your role does not allow you to view this ${Synonyms.asset}.`
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(/invalid response/)).not.toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });

    it("shows a blocked request's message and request ID, not a permission denial", async () => {
        mockGet.mockRejectedValue(
            new ApiError(
                "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)",
                403,
                {
                    message: "Request blocked by the deployment's AWS WAF web ACL",
                    blockedBy: "waf",
                    requestId: "r-1",
                }
            )
        );
        renderPage();

        expect(
            await screen.findByText(
                `Failed to load ${Synonyms.asset} data. Request blocked by the deployment's AWS WAF web ACL (request ID r-1)`
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(/Access denied/)).not.toBeInTheDocument();
        expect(screen.queryByText(/invalid response/)).not.toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });

    it("says the service could not be reached when the browser never sees a response", async () => {
        // A cross-origin request whose CORS preflight the web ACL blocked rejects like this.
        mockGet.mockRejectedValue(new TypeError("Failed to fetch"));
        renderPage();

        expect(
            await screen.findByText(
                `Failed to load ${Synonyms.asset} data. The request could not reach the service. It may have been blocked by a firewall or the network.`
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
        expect(screen.queryByText(/Access denied/)).not.toBeInTheDocument();
        expect(screen.getByText(`${Synonyms.Asset} Information Unavailable`)).toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });

    it("still says the asset is unavailable after the error alert is dismissed", async () => {
        mockGet.mockRejectedValue(
            new ApiError("Not Authorized", 403, { message: "Not Authorized" })
        );
        renderPage();
        await screen.findByText(/Access denied/);

        fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));

        await waitFor(() => expect(screen.queryByText(/Access denied/)).not.toBeInTheDocument());
        expect(screen.getByText(`${Synonyms.Asset} Information Unavailable`)).toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });

    it("keeps the not-found message for a missing asset", async () => {
        mockGet.mockRejectedValue(
            new ApiError("Asset not found in database", 404, {
                message: "Asset not found in database",
            })
        );
        renderPage();

        expect(
            await screen.findByText(new RegExp(`${Synonyms.Asset} not found`))
        ).toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });

    it("shows a loading state until the asset arrives", async () => {
        mockGet.mockReturnValue(new Promise(() => undefined));
        renderPage();

        expect(await screen.findByText(`Loading ${Synonyms.asset}...`)).toBeInTheDocument();
        expect(mockMounted).toEqual([]);
    });
});
