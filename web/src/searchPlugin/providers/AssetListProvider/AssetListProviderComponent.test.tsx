/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AssetListProviderComponent, {
    isArchivedAsset,
    normalizeListedAsset,
} from "./AssetListProviderComponent";
import { fetchAllAssets, fetchDatabaseAssets } from "../../../services/APIService";

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

    it("disables Create Asset and Delete Selected with the reasons the page passes", async () => {
        // The reasons come from the API's allowed-routes listing (SearchPage); the provider only
        // shows them, and the API still refuses the call.
        render(
            <MemoryRouter>
                <AssetListProviderComponent
                    isActive
                    assetActionDisabledReasons={{
                        create: "Your role does not allow creating assets.",
                        delete: "Your role does not allow archiving or deleting assets.",
                    }}
                />
            </MemoryRouter>
        );
        await screen.findByText("Live asset");
        // Cloudscape keeps a button with a disabledReason focusable, so it is aria-disabled.
        const create = screen.getByRole("button", { name: /Create Asset/ });
        expect(create).toHaveAttribute("aria-disabled", "true");
        expect(create).toHaveAccessibleDescription("Your role does not allow creating assets.");
        const row = screen.getByText("Live asset").closest("tr") as HTMLElement;
        await userEvent.click(within(row).getByRole("checkbox"));
        const del = screen.getByRole("button", { name: "Delete Selected" });
        expect(del).toHaveAttribute("aria-disabled", "true");
        expect(del).toHaveAccessibleDescription(
            "Your role does not allow archiving or deleting assets."
        );
        await userEvent.click(del);
        expect(screen.queryByTestId("asset-delete-modal")).toBeNull();
    });

    it.each([
        ["may not unarchive", "Your role does not allow unarchiving assets.", true],
        ["may unarchive", undefined, false],
    ])(
        "gates Unarchive Selected on the reason the page passes: a role that %s",
        async (_, reason, refused) => {
            render(
                <MemoryRouter>
                    <AssetListProviderComponent
                        isActive
                        assetActionDisabledReasons={{ unarchive: reason }}
                    />
                </MemoryRouter>
            );
            await screen.findByText("Live asset");
            await userEvent.click(screen.getByRole("checkbox", { name: "Show archived" }));
            const row = (await screen.findByText("Archived asset")).closest("tr") as HTMLElement;
            await userEvent.click(within(row).getByRole("checkbox"));
            const unarchive = await screen.findByRole("button", { name: "Unarchive Selected" });
            await userEvent.click(unarchive);
            if (refused) {
                expect(unarchive).toHaveAttribute("aria-disabled", "true");
                expect(unarchive).toHaveAccessibleDescription(reason as string);
                expect(screen.queryByTestId("asset-unarchive-modal")).toBeNull();
            } else {
                expect(unarchive).not.toHaveAttribute("aria-disabled");
                expect(await screen.findByTestId("asset-unarchive-modal")).toBeInTheDocument();
            }
        }
    );

    it("renders no page heading of its own; the table's header names the list", async () => {
        // The search page's title row carries the page h1 (SearchTabsHost); a second one here would
        // repeat it above a table already headed "Assets (n)".
        renderProvider();
        await screen.findByText("Live asset");
        expect(screen.queryAllByRole("heading", { level: 1 })).toHaveLength(0);
        expect(screen.getByRole("heading", { level: 2, name: /^Assets/ })).toBeInTheDocument();
    });

    it("on a database route repeats neither the page's breadcrumbs nor its heading", async () => {
        (fetchDatabaseAssets as jest.Mock).mockResolvedValue([active]);
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets"]}>
                <Routes>
                    <Route
                        path="/databases/:databaseId/assets"
                        element={<AssetListProviderComponent isActive databaseId="db1" />}
                    />
                </Routes>
            </MemoryRouter>
        );
        await screen.findByText("Live asset");
        expect(fetchDatabaseAssets).toHaveBeenLastCalledWith({
            databaseId: "db1",
            showArchived: false,
        });
        expect(screen.queryByRole("navigation", { name: "Breadcrumbs" })).toBeNull();
        expect(screen.queryAllByRole("heading", { level: 1 })).toHaveLength(0);
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
