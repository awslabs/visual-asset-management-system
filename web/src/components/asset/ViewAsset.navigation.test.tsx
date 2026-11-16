/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Moving between asset pages inside the app keeps ViewAsset mounted, so the page must load each
 * asset afresh: an error from the previous asset is cleared, each asset is fetched once, and the
 * asset detail context names the asset the route names. The details pane and tabs are stubbed to
 * record what the page hands them.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
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

const mockPaneAssets: any[] = [];
jest.mock("./AssetDetailsPane", () => ({
    __esModule: true,
    default: (props: any) => {
        mockPaneAssets.push(props.asset);
        return <div data-testid="pane">{props.asset?.assetName}</div>;
    },
}));
const mockDetailStates: any[] = [];
jest.mock("./TabbedContainer", () => ({
    __esModule: true,
    default: function MockTabbedContainer() {
        const { useContext: useCtx } = jest.requireActual("react");
        const { AssetDetailContext: Ctx } = jest.requireActual("../../context/AssetDetailContext");
        const { state } = useCtx(Ctx);
        mockDetailStates.push({ assetId: state.assetId, assetName: state.assetName });
        return null;
    },
}));
jest.mock("../metadataV2", () => ({ MetadataContainer: () => null }));
jest.mock("../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../createupdate/UpdateAsset", () => ({ UpdateAsset: () => null }));

const mockFetchAsset = APIService.fetchAssetWithStatus as jest.Mock;
const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;

const ASSETS: Record<string, any> = {
    a1: { assetId: "a1", assetName: "Bracket", databaseId: "db1" },
    a2: { assetId: "a2", assetName: "Housing", databaseId: "db1" },
};

function Nav() {
    const navigate = useNavigate();
    return (
        <>
            <button type="button" onClick={() => navigate("/databases/db1/assets/a1")}>
                open a1
            </button>
            <button type="button" onClick={() => navigate("/databases/db1/assets/a2")}>
                open a2
            </button>
        </>
    );
}

function renderAt(assetId: string) {
    render(
        <MemoryRouter initialEntries={[`/databases/db1/assets/${assetId}`]}>
            <Nav />
            <Routes>
                <Route path="/databases/:databaseId/assets/:assetId" element={<ViewAsset />} />
            </Routes>
        </MemoryRouter>
    );
}

const unavailableHeader = `${Synonyms.Asset} Information Unavailable`;

describe("asset page across in-app navigation", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        mockPaneAssets.length = 0;
        mockDetailStates.length = 0;
        mockFetchAllowedApiRoutes.mockResolvedValue(allowedRoutesResponse(ADMIN_ROUTES));
        mockFetchAsset.mockImplementation(async ({ assetId }: any) =>
            ASSETS[assetId] ? [true, ASSETS[assetId]] : [false, "Not Authorized", 403]
        );
    });

    it("shows a permitted asset after the previous one could not be loaded", async () => {
        renderAt("denied");
        expect(await screen.findByText(unavailableHeader)).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "open a1" }));

        await waitFor(() => expect(screen.getByTestId("pane")).toHaveTextContent("Bracket"));
        expect(screen.queryByText(unavailableHeader)).not.toBeInTheDocument();
    });

    it("fetches each asset once", async () => {
        renderAt("a1");
        await waitFor(() => expect(screen.getByTestId("pane")).toHaveTextContent("Bracket"));
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
        expect(mockFetchAsset).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByRole("button", { name: "open a2" }));
        await waitFor(() => expect(screen.getByTestId("pane")).toHaveTextContent("Housing"));
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
        expect(mockFetchAsset).toHaveBeenCalledTimes(2);
        expect(mockFetchAsset).toHaveBeenLastCalledWith(
            expect.objectContaining({ databaseId: "db1", assetId: "a2" })
        );
    });

    it("names the routed asset in the asset detail context", async () => {
        renderAt("a1");
        await waitFor(() =>
            expect(mockDetailStates).toContainEqual({ assetId: "a1", assetName: "Bracket" })
        );

        fireEvent.click(screen.getByRole("button", { name: "open a2" }));
        await waitFor(() =>
            expect(mockDetailStates).toContainEqual({ assetId: "a2", assetName: "Housing" })
        );
        expect(mockDetailStates).not.toContainEqual({ assetId: "a2", assetName: "Bracket" });
    });

    it("ignores a slow answer for an asset the user already left", async () => {
        let answerA1: (value: any) => void = () => undefined;
        mockFetchAsset.mockImplementation(({ assetId }: any) =>
            assetId === "a1"
                ? new Promise((resolve) => {
                      answerA1 = resolve;
                  })
                : Promise.resolve([true, ASSETS[assetId]])
        );
        renderAt("a1");
        await waitFor(() => expect(mockFetchAsset).toHaveBeenCalledTimes(1));

        fireEvent.click(screen.getByRole("button", { name: "open a2" }));
        await waitFor(() => expect(screen.getByTestId("pane")).toHaveTextContent("Housing"));
        const shownSinceA2 = mockPaneAssets.length;
        await act(async () => {
            answerA1([true, ASSETS.a1]);
            await new Promise((resolve) => setTimeout(resolve, 20));
        });

        expect(screen.getByTestId("pane")).toHaveTextContent("Housing");
        expect(mockPaneAssets.slice(shownSinceA2).map((asset) => asset?.assetId)).not.toContain(
            "a1"
        );
    });
});
