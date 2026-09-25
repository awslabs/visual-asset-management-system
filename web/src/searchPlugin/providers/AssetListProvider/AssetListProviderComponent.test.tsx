/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import AssetListProviderComponent, {
    isArchivedAsset,
    normalizeListedAsset,
} from "./AssetListProviderComponent";
import { fetchAllAssets } from "../../../services/APIService";

jest.mock("../../../services/APIService", () => ({
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
}));

// The shared modals are exercised by their own suites; here they only need to report what the
// provider hands them.
const deleteModalProps: any[] = [];
const unarchiveModalProps: any[] = [];
jest.mock("../../../components/modals/AssetDeleteModal", () => ({
    __esModule: true,
    default: (props: any) => {
        deleteModalProps.push(props);
        return props.visible ? <div data-testid="asset-delete-modal" /> : null;
    },
}));
jest.mock("../../../components/modals/AssetUnarchiveModal", () => ({
    __esModule: true,
    default: (props: any) => {
        unarchiveModalProps.push(props);
        return props.visible ? <div data-testid="asset-unarchive-modal" /> : null;
    },
}));

const active = {
    assetId: "a-live",
    assetName: "Live asset",
    databaseId: "db1",
    description: "d",
    assetType: "glb",
    tags: [],
};
const archived = {
    assetId: "a-gone",
    assetName: "Archived asset",
    databaseId: "db1#deleted",
    description: "d",
    assetType: "glb",
    tags: [],
    status: "archived",
};

const renderProvider = () =>
    render(
        <MemoryRouter>
            <AssetListProviderComponent isActive />
        </MemoryRouter>
    );

describe("AssetListProviderComponent bulk actions", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        deleteModalProps.length = 0;
        unarchiveModalProps.length = 0;
        (fetchAllAssets as jest.Mock).mockImplementation(async ({ showArchived }: any) =>
            showArchived ? [active, archived] : [active]
        );
    });

    it("classifies and normalizes archived rows", () => {
        expect(isArchivedAsset(archived)).toBe(true);
        expect(isArchivedAsset({ databaseId: "db1#deleted" })).toBe(true);
        expect(isArchivedAsset(active)).toBe(false);
        expect(normalizeListedAsset(archived)).toMatchObject({
            databaseId: "db1",
            status: "archived",
        });
        expect(normalizeListedAsset(active).status).toBeUndefined();
    });

    it("lists active assets by default and offers Delete Selected only once something is selected", async () => {
        renderProvider();
        await screen.findByText("Live asset");
        expect(fetchAllAssets).toHaveBeenLastCalledWith({ showArchived: false });
        expect(screen.queryByText("Archived asset")).toBeNull();

        const del = screen.getByRole("button", { name: "Delete Selected" });
        expect(del).toBeDisabled();
        expect(screen.queryByRole("button", { name: "Unarchive Selected" })).toBeNull();
        expect(screen.getByRole("button", { name: /Create Asset/ })).toBeInTheDocument();

        const row = screen.getByText("Live asset").closest("tr") as HTMLElement;
        await userEvent.click(within(row).getByRole("checkbox"));
        await waitFor(() => expect(del).toBeEnabled());
        // A live selection keeps the modal in its archive-first mode.
        await userEvent.click(del);
        expect(await screen.findByTestId("asset-delete-modal")).toBeInTheDocument();
        const last = deleteModalProps[deleteModalProps.length - 1];
        expect(last.mode).toBe("asset");
        expect(last.forceDeleteMode).toBe(false);
        expect(last.selectedAssets.map((a: any) => a.assetId)).toEqual(["a-live"]);
    });

    it("Show archived refetches with showArchived, lists archived rows under their base database, and offers Unarchive for one archived selection", async () => {
        renderProvider();
        await screen.findByText("Live asset");
        await userEvent.click(screen.getByRole("checkbox", { name: "Show archived" }));
        await waitFor(() =>
            expect(fetchAllAssets).toHaveBeenLastCalledWith({ showArchived: true })
        );
        const archivedName = await screen.findByText("Archived asset");
        const row = archivedName.closest("tr") as HTMLElement;
        // The #deleted partition suffix is not shown; the row is flagged instead.
        expect(within(row).getByText("Archived")).toBeInTheDocument();
        expect(within(row).queryByText(/#deleted/)).toBeNull();
        expect(within(row).getByRole("link", { name: "db1" })).toBeInTheDocument();

        await userEvent.click(within(row).getByRole("checkbox"));
        const unarchive = await screen.findByRole("button", { name: "Unarchive Selected" });
        await userEvent.click(unarchive);
        expect(await screen.findByTestId("asset-unarchive-modal")).toBeInTheDocument();
        const lastUnarchive = unarchiveModalProps[unarchiveModalProps.length - 1];
        expect(lastUnarchive.selectedAsset.assetId).toBe("a-gone");

        // An archived asset in the selection switches the delete modal to permanent-delete mode.
        await userEvent.click(screen.getByRole("button", { name: "Delete Selected" }));
        const lastDelete = deleteModalProps[deleteModalProps.length - 1];
        expect(lastDelete.forceDeleteMode).toBe(true);
    });

    it("hides Unarchive Selected when more than one row is selected", async () => {
        renderProvider();
        await screen.findByText("Live asset");
        await userEvent.click(screen.getByRole("checkbox", { name: "Show archived" }));
        const archivedRow = (await screen.findByText("Archived asset")).closest(
            "tr"
        ) as HTMLElement;
        const liveRow = screen.getByText("Live asset").closest("tr") as HTMLElement;
        await userEvent.click(within(archivedRow).getByRole("checkbox"));
        await screen.findByRole("button", { name: "Unarchive Selected" });
        await userEvent.click(within(liveRow).getByRole("checkbox"));
        await waitFor(() =>
            expect(screen.queryByRole("button", { name: "Unarchive Selected" })).toBeNull()
        );
    });
});
