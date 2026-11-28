/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * #1860: the subscribe toggle asks about, subscribes to and unsubscribes from the asset of the page's
 * database. Two databases may hold assets with the same asset id, so every request names the database.
 * The API decides each request; the pane only shows the state the API returned.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssetDetailsPane } from "./AssetDetailsPane";

jest.mock("react-router", () => ({ useNavigate: () => jest.fn() }));

const mockCheckSubscription = jest.fn();
const mockCreateSubscription = jest.fn();
const mockUnsubscribe = jest.fn();
jest.mock("../../services/APIService", () => ({
    downloadAsset: jest.fn(),
    checkSubscription: (...args: any[]) => mockCheckSubscription(...args),
    createSubscription: (...args: any[]) => mockCreateSubscription(...args),
    unsubscribeFromAsset: (...args: any[]) => mockUnsubscribe(...args),
}));

jest.mock("../common/StatusMessage", () => ({
    useStatusMessage: () => ({ showMessage: jest.fn() }),
}));

jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => false }),
}));
jest.mock("../../services/ComplianceService", () => ({
    ...jest.requireActual("../../services/ComplianceService"),
    fetchComplianceState: jest.fn(),
}));

const asset = {
    assetId: "x1",
    assetName: "Widget",
    description: "A widget",
    databaseId: "db-b",
    isDistributable: true,
};

const renderPane = () =>
    render(
        <AssetDetailsPane
            asset={asset}
            databaseId="db-b"
            assetId="x1"
            onOpenUpdateAsset={jest.fn()}
            onOpenDeleteModal={jest.fn()}
            onOpenUnarchiveModal={jest.fn()}
        />
    );

describe("AssetDetailsPane subscription requests", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.setItem("user", JSON.stringify({ username: "alice" }));
    });

    afterEach(() => {
        localStorage.removeItem("user");
    });

    it("asks about the subscription of the page's database", async () => {
        mockCheckSubscription.mockResolvedValue([true, "Subscription doesn't exists."]);
        renderPane();
        await waitFor(() => expect(mockCheckSubscription).toHaveBeenCalled());
        expect(mockCheckSubscription.mock.calls[0][0]).toEqual({
            userId: "alice",
            assetId: "x1",
            databaseId: "db-b",
        });
    });

    it("subscribes and unsubscribes with the page's database in the body", async () => {
        mockCheckSubscription.mockResolvedValue([true, "Subscription doesn't exists."]);
        mockCreateSubscription.mockResolvedValue({ message: "success" });
        mockUnsubscribe.mockResolvedValue([true, "success"]);
        renderPane();
        await waitFor(() => expect(mockCheckSubscription).toHaveBeenCalled());

        fireEvent.click(await screen.findByRole("button", { name: "Subscribe" }));
        await waitFor(() => expect(mockCreateSubscription).toHaveBeenCalled());
        expect(mockCreateSubscription.mock.calls[0][0]).toMatchObject({
            entityName: "Asset",
            entityId: "x1",
            databaseId: "db-b",
            subscribers: ["alice"],
        });

        // Subscribed: the same toggle now unsubscribes
        fireEvent.click(await screen.findByRole("button", { name: "Subscribed" }));
        await waitFor(() => expect(mockUnsubscribe).toHaveBeenCalled());
        expect(mockUnsubscribe.mock.calls[0][0]).toMatchObject({
            entityId: "x1",
            databaseId: "db-b",
        });
    });
});
