/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset page offers Delete and the metadata editor only to a role whose API route permissions
 * allow them. Roles come from the shipped database-readonly and database-user templates (their
 * allowed-routes listings are in templateRoleRoutes.testdata.ts) and the administrator. The details
 * pane and the metadata editor are stubbed to record what the page hands them.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import * as APIService from "../../services/APIService";
import ViewAsset from "./ViewAsset";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import { PERMISSIONS_LOADING_REASON } from "../../features/orchestration/permissions/routeDisabledReason";
import {
    ADMIN_ROUTES,
    DATABASE_READONLY_ROUTES,
    DATABASE_USER_ROUTES,
    allowedRoutesResponse,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    fetchAsset: jest.fn(),
    fetchAssetLinks: jest.fn(() => Promise.resolve({})),
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../../services/AssetVersionService", () => ({
    fetchAllAssetVersions: jest.fn(() => Promise.resolve([true, { versions: [] }])),
}));
jest.mock("../../pages/AssetUpload/AssetUpload", () => ({ AssetDetail: {} }));

const mockPaneProps: any[] = [];
jest.mock("./AssetDetailsPane", () => ({
    __esModule: true,
    default: (props: any) => {
        mockPaneProps.push(props);
        return null;
    },
}));
const mockMetadataProps: any[] = [];
jest.mock("../metadataV2", () => ({
    MetadataContainer: (props: any) => {
        mockMetadataProps.push(props);
        return null;
    },
}));
jest.mock("./TabbedContainer", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../createupdate/UpdateAsset", () => ({ UpdateAsset: () => null }));

const mockFetchAsset = APIService.fetchAsset as jest.Mock;
const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;

async function renderAs(routes: any[]) {
    mockFetchAllowedApiRoutes.mockResolvedValue(allowedRoutesResponse(routes));
    render(
        <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
            <Routes>
                <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
            </Routes>
        </MemoryRouter>
    );
    // Settled once the permission check has answered: the pane no longer shows the loading reason.
    await waitFor(() => {
        const last = mockPaneProps[mockPaneProps.length - 1];
        expect(last).toBeDefined();
        expect(last.deleteDisabledReason).not.toBe(PERMISSIONS_LOADING_REASON);
    });
    return {
        pane: mockPaneProps[mockPaneProps.length - 1],
        metadata: mockMetadataProps[mockMetadataProps.length - 1],
    };
}

describe("asset page actions by role", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        mockPaneProps.length = 0;
        mockMetadataProps.length = 0;
        mockFetchAsset.mockResolvedValue({
            assetId: "a1",
            assetName: "Bracket",
            databaseId: "db1",
        });
    });

    it("a database-readonly role gets Delete disabled with the reason and a read-only editor", async () => {
        const { pane, metadata } = await renderAs(DATABASE_READONLY_ROUTES);
        expect(pane.deleteDisabledReason).toBe(
            `Your role does not allow archiving or deleting this ${Synonyms.asset}.`
        );
        expect(metadata.readOnly).toBe(true);
    });

    it("a database-user role keeps Delete and the metadata editor", async () => {
        const { pane, metadata } = await renderAs(DATABASE_USER_ROUTES);
        expect(pane.deleteDisabledReason).toBeUndefined();
        expect(metadata.readOnly).toBe(false);
    });

    it("the administrator keeps Delete and the metadata editor", async () => {
        const { pane, metadata } = await renderAs(ADMIN_ROUTES);
        expect(pane.deleteDisabledReason).toBeUndefined();
        expect(metadata.readOnly).toBe(false);
    });

    it("disables Delete and the editor while the permission check loads", () => {
        mockFetchAllowedApiRoutes.mockReturnValue(new Promise(() => undefined));
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
                <Routes>
                    <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
                </Routes>
            </MemoryRouter>
        );
        expect(mockPaneProps[0].deleteDisabledReason).toBe(PERMISSIONS_LOADING_REASON);
        expect(mockMetadataProps[0].readOnly).toBe(true);
    });
});
