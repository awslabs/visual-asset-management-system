/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * #870: an archived asset's details pane shows an archived notice and offers Unarchive and Delete
 * permanently in place of Delete, Edit and Subscribe. It asks for no subscription status and no
 * preview, both of which belong to a live asset.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssetDetailsPane } from "./AssetDetailsPane";

jest.mock("react-router", () => ({ useNavigate: () => jest.fn() }));

const mockDownloadAsset = jest.fn();
const mockCheckSubscription = jest.fn();
jest.mock("../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
    checkSubscription: (...args: any[]) => mockCheckSubscription(...args),
    createSubscription: jest.fn(),
    unsubscribeFromAsset: jest.fn(),
}));

jest.mock("../common/StatusMessage", () => ({
    useStatusMessage: () => ({ showMessage: jest.fn() }),
}));

// The header's compliance badge asks the permissions check for the compliance state route. These
// tests are about the archived header, so the role cannot read compliance state and no state is read
// (AssetDetailsPane.test.tsx covers the badge, an archived asset's included).
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => false }),
}));
const mockFetchComplianceState = jest.fn();
jest.mock("../../services/ComplianceService", () => ({
    ...jest.requireActual("../../services/ComplianceService"),
    fetchComplianceState: (...args: any[]) => mockFetchComplianceState(...args),
}));

const archivedAsset = {
    assetId: "a1",
    assetName: "Widget",
    description: "A widget",
    databaseId: "db1#deleted",
    isDistributable: true,
    status: "archived",
    archivedAt: "2026-10-27T10:00:00Z",
    archivedBy: "admin",
    archivedReason: "Superseded",
    previewLocation: { Key: "preview/widget.png" },
};

const renderPane = (props: any) => {
    const onOpenDeleteModal = jest.fn();
    const onOpenUnarchiveModal = jest.fn();
    const onOpenUpdateAsset = jest.fn();
    render(
        <AssetDetailsPane
            asset={archivedAsset}
            databaseId="db1"
            assetId="a1"
            onOpenUpdateAsset={onOpenUpdateAsset}
            onOpenDeleteModal={onOpenDeleteModal}
            onOpenUnarchiveModal={onOpenUnarchiveModal}
            {...props}
        />
    );
    return { onOpenDeleteModal, onOpenUnarchiveModal, onOpenUpdateAsset };
};

describe("AssetDetailsPane for an archived asset", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.setItem("user", JSON.stringify({ username: "admin" }));
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/preview.png"]);
        mockCheckSubscription.mockResolvedValue([true, false]);
    });

    it("shows the archived notice with who archived it and why", () => {
        renderPane({ isArchived: true });
        expect(screen.getByText(/is archived/i)).toBeInTheDocument();
        expect(screen.getByText(/by admin\./)).toBeInTheDocument();
        expect(screen.getByText("Reason: Superseded")).toBeInTheDocument();
    });

    it("says the date was not recorded for a row without an archive date", () => {
        renderPane({
            isArchived: true,
            asset: { ...archivedAsset, archivedAt: undefined, archivedBy: undefined },
        });
        expect(screen.getByText("Archived (date not recorded).")).toBeInTheDocument();
    });

    it("never shows Invalid Date for an unreadable archive date", () => {
        renderPane({ isArchived: true, asset: { ...archivedAsset, archivedAt: "not-a-date" } });
        expect(screen.getByText("Archived by admin (date not recorded).")).toBeInTheDocument();
        expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
    });

    it("shows the current version, or none when the record carries no version", () => {
        renderPane({ isArchived: true });
        expect(screen.getByText("none")).toBeInTheDocument();
        expect(screen.queryByText(/^v$/)).not.toBeInTheDocument();
    });

    it("shows the current version's id when the record carries one", () => {
        renderPane({
            isArchived: true,
            asset: { ...archivedAsset, currentVersion: { Version: "3" } },
        });
        expect(screen.getByText(/^v3/)).toBeInTheDocument();
    });

    it("offers Unarchive and Delete permanently, and no Edit, Delete or Subscribe", () => {
        const { onOpenDeleteModal, onOpenUnarchiveModal } = renderPane({ isArchived: true });
        expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /subscribe/i })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Unarchive" }));
        expect(onOpenUnarchiveModal).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
        expect(onOpenDeleteModal).toHaveBeenCalledTimes(1);
    });

    it("disables Unarchive and Delete permanently with the role's reasons", () => {
        renderPane({
            isArchived: true,
            unarchiveDisabledReason: "no unarchive",
            deleteDisabledReason: "no delete",
        });
        expect(screen.getByRole("button", { name: "Unarchive" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(screen.getByRole("button", { name: "Delete permanently" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("asks for no subscription status and no preview", () => {
        renderPane({ isArchived: true });
        expect(mockCheckSubscription).not.toHaveBeenCalled();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
        // The role cannot read compliance state, so the badge reads none.
        expect(mockFetchComplianceState).not.toHaveBeenCalled();
    });

    it("a live asset keeps Delete, Edit and Subscribe and shows no archived notice", async () => {
        renderPane({ isArchived: false, asset: { ...archivedAsset, status: undefined } });
        expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Unarchive" })).not.toBeInTheDocument();
        expect(screen.queryByText(/is archived/i)).not.toBeInTheDocument();
        expect(await screen.findByRole("button", { name: /subscribe/i })).toBeInTheDocument();
        // Control for the archived case: a live asset does ask for its status and its preview
        await waitFor(() => expect(mockCheckSubscription).toHaveBeenCalled());
        await waitFor(() => expect(mockDownloadAsset).toHaveBeenCalled());
    });
});
