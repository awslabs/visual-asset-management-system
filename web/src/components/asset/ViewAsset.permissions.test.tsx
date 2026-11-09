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
import * as AssetVersionService from "../../services/AssetVersionService";
import ViewAsset from "./ViewAsset";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import {
    PERMISSIONS_FAILED_REASON,
    PERMISSIONS_LOADING_REASON,
} from "../../features/orchestration/permissions/routeDisabledReason";
import {
    ADMIN_ROUTES,
    DATABASE_READONLY_ROUTES,
    DATABASE_USER_ROUTES,
    allowedRoutesResponse,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";
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
jest.mock("../modals/AssetUnarchiveModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../createupdate/UpdateAsset", () => ({ UpdateAsset: () => null }));

const mockFetchAsset = APIService.fetchAssetWithStatus as jest.Mock;
const mockFetchAllAssetVersions = AssetVersionService.fetchAllAssetVersions as jest.Mock;
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
        mockFetchAsset.mockResolvedValue([
            true,
            { assetId: "a1", assetName: "Bracket", databaseId: "db1" },
        ]);
    });

    it("a database-readonly role gets Delete disabled with the reason and a read-only editor", async () => {
        const { pane, metadata } = await renderAs(DATABASE_READONLY_ROUTES);
        expect(pane.deleteDisabledReason).toBe(
            `Your role does not allow archiving or deleting this ${Synonyms.asset}.`
        );
        expect(metadata.readOnly).toBe(true);
    });

    it("says the permissions check failed on Delete when it failed", async () => {
        mockFetchAllowedApiRoutes.mockResolvedValue([false, "Internal Server Error"]);
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
                <Routes>
                    <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
                </Routes>
            </MemoryRouter>
        );
        await waitFor(() =>
            expect(mockPaneProps[mockPaneProps.length - 1].deleteDisabledReason).toBe(
                PERMISSIONS_FAILED_REASON
            )
        );
        expect(mockMetadataProps[mockMetadataProps.length - 1].readOnly).toBe(true);
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

    it("disables Delete and the editor while the permission check loads", async () => {
        mockFetchAllowedApiRoutes.mockReturnValue(new Promise(() => undefined));
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
                <Routes>
                    <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
                </Routes>
            </MemoryRouter>
        );
        // The pane mounts once the asset has loaded, while the permission check is still pending
        await waitFor(() => expect(mockPaneProps.length).toBeGreaterThan(0));
        expect(mockPaneProps[0].deleteDisabledReason).toBe(PERMISSIONS_LOADING_REASON);
        expect(mockMetadataProps[0].readOnly).toBe(true);
    });

    it("mounts no panel before the asset has loaded", () => {
        mockFetchAsset.mockReturnValue(new Promise(() => undefined));
        mockFetchAllowedApiRoutes.mockResolvedValue(allowedRoutesResponse(ADMIN_ROUTES));
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
                <Routes>
                    <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
                </Routes>
            </MemoryRouter>
        );
        expect(mockPaneProps).toHaveLength(0);
        expect(mockMetadataProps).toHaveLength(0);
        expect(mockFetchAllAssetVersions).not.toHaveBeenCalled();
    });

    it("a live asset is not shown as archived and reads its versions without showArchived", async () => {
        const { pane } = await renderAs(ADMIN_ROUTES);
        expect(pane.isArchived).toBe(false);
        await waitFor(() => expect(mockFetchAllAssetVersions).toHaveBeenCalled());
        expect(mockFetchAllAssetVersions).toHaveBeenCalledWith(
            expect.objectContaining({ databaseId: "db1", assetId: "a1", showArchived: false })
        );
    });
});

/** The administrator listing plus the archived asset's two actions. */
const ADMIN_WITH_ARCHIVE_ACTIONS = [
    ...ADMIN_ROUTES,
    {
        path: "/database/{databaseId}/assets/{assetId}/unarchiveAsset",
        methods: ["PUT"],
        category: "test",
    },
    {
        path: "/database/{databaseId}/assets/{assetId}/deleteAsset",
        methods: ["DELETE"],
        category: "test",
    },
];

describe("archived asset page (#870)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        mockPaneProps.length = 0;
        mockMetadataProps.length = 0;
        mockFetchAsset.mockResolvedValue([
            true,
            {
                assetId: "a1",
                assetName: "Bracket",
                databaseId: "db1#deleted",
                status: "archived",
                archivedAt: "2026-10-27T10:00:00Z",
                archivedBy: "admin",
            },
            200,
        ]);
    });

    it("shows the archived asset read-only, offering Unarchive and Delete permanently", async () => {
        const { pane, metadata } = await renderAs(ADMIN_WITH_ARCHIVE_ACTIONS);
        expect(pane.isArchived).toBe(true);
        expect(pane.deleteDisabledReason).toBeUndefined();
        expect(pane.unarchiveDisabledReason).toBeUndefined();
        // The administrator may edit a live asset's metadata, but an archived asset's is read-only
        expect(metadata.readOnly).toBe(true);
    });

    it("reads the archived asset's versions with showArchived", async () => {
        await renderAs(ADMIN_WITH_ARCHIVE_ACTIONS);
        await waitFor(() => expect(mockFetchAllAssetVersions).toHaveBeenCalled());
        expect(mockFetchAllAssetVersions).toHaveBeenCalledWith(
            expect.objectContaining({ databaseId: "db1", assetId: "a1", showArchived: true })
        );
    });

    it("gates Unarchive and Delete permanently on their own routes", async () => {
        // The administrator template listing here holds archiveAsset but neither archive action
        const { pane } = await renderAs(ADMIN_ROUTES);
        expect(pane.unarchiveDisabledReason).toBe(
            `Your role does not allow unarchiving this ${Synonyms.asset}.`
        );
        expect(pane.deleteDisabledReason).toBe(
            `Your role does not allow permanently deleting this ${Synonyms.asset}.`
        );
    });

    it("says the permissions check failed on Unarchive and Delete permanently when it failed", async () => {
        mockFetchAllowedApiRoutes.mockResolvedValue([false, "Internal Server Error"]);
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
                <Routes>
                    <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
                </Routes>
            </MemoryRouter>
        );
        await waitFor(() => {
            const last = mockPaneProps[mockPaneProps.length - 1];
            expect(last?.isArchived).toBe(true);
            expect(last.unarchiveDisabledReason).toBe(PERMISSIONS_FAILED_REASON);
        });
        expect(mockPaneProps[mockPaneProps.length - 1].deleteDisabledReason).toBe(
            PERMISSIONS_FAILED_REASON
        );
    });

    it("a read-only role sees the archived asset with both actions disabled", async () => {
        const { pane, metadata } = await renderAs(DATABASE_READONLY_ROUTES);
        expect(pane.isArchived).toBe(true);
        expect(pane.unarchiveDisabledReason).toBeDefined();
        expect(pane.deleteDisabledReason).toBeDefined();
        expect(metadata.readOnly).toBe(true);
    });
});
