/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset list follows the route's database through in-app navigation (#1228). The all-assets
 * page and a database's page are different routes that render the same page component, as in
 * routes.tsx, so React keeps the page mounted when the user moves between them: the list must load
 * the new route's assets, drop the previous route's rows and selection, and never show an answer
 * that arrives late for a route the user has left.
 */
import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate, useParams } from "react-router-dom";
import AssetListProviderComponent from "./AssetListProviderComponent";
import { fetchAllAssets, fetchDatabaseAssets } from "../../../services/APIService";

jest.mock("../../../services/APIService", () => ({
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
}));
jest.mock("../../../components/modals/AssetDeleteModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../../../components/modals/AssetUnarchiveModal", () => ({
    __esModule: true,
    default: () => null,
}));

const asset = (assetId: string, assetName: string, databaseId: string) => ({
    assetId,
    assetName,
    databaseId,
    description: "d",
    assetType: "glb",
    tags: [],
});
const db1Asset = asset("a1", "Asset in db1", "db1");
const db2Asset = asset("a2", "Asset in db2", "db2");
const byDatabase: Record<string, any[]> = { db1: [db1Asset], db2: [db2Asset] };

/** The search page's part in this: the route's database, handed to the asset list view. */
const RoutePage = () => {
    const { databaseId } = useParams();
    return <AssetListProviderComponent isActive databaseId={databaseId} />;
};

/** In-app navigation, as a link, a breadcrumb or Back would do it. */
let navigateTo: (path: string) => void = () => undefined;
const Navigator = () => {
    const navigate = useNavigate();
    navigateTo = navigate;
    return null;
};

const renderAt = (path: string) =>
    render(
        <MemoryRouter initialEntries={[path]}>
            <Navigator />
            <Routes>
                {/* Three routes, one page component: the paths routes.tsx gives the search page. */}
                <Route path="/assets" element={<RoutePage />} />
                <Route path="/search/:databaseId/assets" element={<RoutePage />} />
                <Route path="/databases/:databaseId/assets" element={<RoutePage />} />
            </Routes>
        </MemoryRouter>
    );

const go = async (path: string) => {
    await act(async () => {
        navigateTo(path);
    });
};

const deferred = () => {
    let resolve: (value: any) => void = () => undefined;
    const promise = new Promise<any>((r) => {
        resolve = r;
    });
    return { promise, resolve };
};

describe("asset list route database (#1228)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAllAssets as jest.Mock).mockResolvedValue([db1Asset, db2Asset]);
        (fetchDatabaseAssets as jest.Mock).mockImplementation(
            async ({ databaseId }: any) => byDatabase[databaseId] ?? []
        );
    });

    it("lists only the route's database after in-app navigation, and every database's again on the all-assets page", async () => {
        renderAt("/assets");
        await screen.findByText("Asset in db1");
        expect(screen.getByText("Asset in db2")).toBeInTheDocument();
        expect(fetchAllAssets).toHaveBeenCalledTimes(1);

        // A database's name in the all-assets list leads to its page.
        await go("/databases/db1/assets");
        await waitFor(() =>
            expect(fetchDatabaseAssets).toHaveBeenLastCalledWith({
                databaseId: "db1",
                showArchived: false,
            })
        );
        await waitFor(() => expect(screen.queryByText("Asset in db2")).toBeNull());
        expect(screen.getByText("Asset in db1")).toBeInTheDocument();

        // Another database through the search route of the same page.
        await go("/search/db2/assets");
        await waitFor(() =>
            expect(fetchDatabaseAssets).toHaveBeenLastCalledWith({
                databaseId: "db2",
                showArchived: false,
            })
        );
        await waitFor(() => expect(screen.queryByText("Asset in db1")).toBeNull());
        expect(screen.getByText("Asset in db2")).toBeInTheDocument();

        // Back to the all-assets page.
        await go("/assets");
        await waitFor(() => expect(fetchAllAssets).toHaveBeenCalledTimes(2));
        expect(await screen.findByText("Asset in db1")).toBeInTheDocument();
        expect(screen.getByText("Asset in db2")).toBeInTheDocument();
    });

    it("drops the previous route's selection, so no bulk action applies to a row no longer listed", async () => {
        renderAt("/assets");
        const row = (await screen.findByText("Asset in db2")).closest("tr") as HTMLElement;
        await userEvent.click(within(row).getByRole("checkbox"));
        const del = screen.getByRole("button", { name: "Delete Selected" });
        await waitFor(() => expect(del).toBeEnabled());

        await go("/databases/db1/assets");
        await screen.findByText("Asset in db1");
        await waitFor(() => expect(screen.queryByText("Asset in db2")).toBeNull());
        expect(screen.getByRole("button", { name: "Delete Selected" })).toBeDisabled();
    });

    it("never shows an answer that arrives after the user left its route", async () => {
        const allAssets = deferred();
        (fetchAllAssets as jest.Mock).mockReturnValue(allAssets.promise);
        renderAt("/assets");
        await waitFor(() => expect(fetchAllAssets).toHaveBeenCalledTimes(1));

        await go("/databases/db1/assets");
        await screen.findByText("Asset in db1");

        // The all-assets answer lands after the database's page has loaded.
        await act(async () => {
            allAssets.resolve([db1Asset, db2Asset]);
            await allAssets.promise;
        });
        expect(screen.queryByText("Asset in db2")).toBeNull();
        expect(screen.getByText("Asset in db1")).toBeInTheDocument();
    });
});
