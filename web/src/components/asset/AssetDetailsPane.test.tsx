/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A non-distributable asset is refused by the download API, which serves the asset preview, so the
 * thumbnail and its enlarging modal are not offered and a notice explains why. The notice deliberately
 * occupies the slot the thumbnail would have used — always free in this state — so it adds no height.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { AssetDetailsPane } from "./AssetDetailsPane";

jest.mock("react-router", () => ({ useNavigate: () => jest.fn() }));

const mockDownloadAsset = jest.fn();
jest.mock("../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
    fetchSubscriptionStatus: jest.fn().mockResolvedValue([false, { subscribed: false }]),
    createSubscription: jest.fn(),
    deleteSubscription: jest.fn(),
    fetchAssetVersions: jest.fn().mockResolvedValue([true, []]),
}));

jest.mock("../common/StatusMessage", () => ({
    useStatusMessage: () => ({ showMessage: jest.fn() }),
}));

// The compliance badge gates on this hook; the distribution-notice tests keep it denied.
let mockCanReadCompliance = false;
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => mockCanReadCompliance }),
}));

const mockFetchComplianceState = jest.fn();
jest.mock("../../services/ComplianceService", () => ({
    ...jest.requireActual("../../services/ComplianceService"),
    fetchComplianceState: (...args: any[]) => mockFetchComplianceState(...args),
}));

const baseAsset = {
    assetId: "a1",
    assetName: "Widget",
    description: "A widget",
    databaseId: "db1",
    // A preview exists, so the thumbnail is only withheld because of the flag.
    previewLocation: { Key: "preview/widget.png" },
};

const renderPane = (asset: any) =>
    render(
        <AssetDetailsPane
            asset={asset}
            databaseId="db1"
            onOpenUpdateAsset={jest.fn()}
            onOpenDeleteModal={jest.fn()}
        />
    );

describe("AssetDetailsPane distribution notice", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockCanReadCompliance = false;
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/preview.png"]);
    });

    it("shows the notice and no preview when isDistributable is false", async () => {
        renderPane({ ...baseAsset, isDistributable: false });

        expect(screen.getByText(/not distributable/i)).toBeInTheDocument();
        expect(screen.getByText(/turned off for this/i)).toBeInTheDocument();
        // No preview image, and the doomed download call is never made.
        expect(document.querySelector("img")).toBeNull();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
    });

    it("shows the preview and no notice when isDistributable is true", async () => {
        renderPane({ ...baseAsset, isDistributable: true });

        // Control: proves the notice assertion above is not passing because the pane renders nothing.
        await waitFor(() => expect(mockDownloadAsset).toHaveBeenCalled());
        expect(screen.queryByText(/turned off for this/i)).not.toBeInTheDocument();
    });

    it("treats an asset record without the field as distributable", async () => {
        // Older records predate isDistributable; undefined must not read as "not distributable".
        renderPane({ ...baseAsset });

        await waitFor(() => expect(mockDownloadAsset).toHaveBeenCalled());
        expect(screen.queryByText(/turned off for this/i)).not.toBeInTheDocument();
    });
});

describe("AssetDetailsPane compliance badge", () => {
    const stateRow = (complianceState: string) => ({
        databaseId: "db1",
        assetId: "a1",
        complianceState,
    });

    beforeEach(() => {
        jest.clearAllMocks();
        mockCanReadCompliance = true;
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/preview.png"]);
    });

    it("renders every state through the shared map, so exception reads Exception", async () => {
        mockFetchComplianceState.mockResolvedValue([true, stateRow("exception")]);
        renderPane({ ...baseAsset, isDistributable: true });

        expect(await screen.findByText("Exception")).toBeInTheDocument();
        expect(mockFetchComplianceState).toHaveBeenCalledWith("db1", "a1");
    });

    it("labels a quarantined asset as the shared map does", async () => {
        mockFetchComplianceState.mockResolvedValue([true, stateRow("quarantined")]);
        renderPane({ ...baseAsset, isDistributable: true });

        expect(await screen.findByText("Quarantined")).toBeInTheDocument();
    });

    it("falls back to Unknown for a state the map does not know", async () => {
        mockFetchComplianceState.mockResolvedValue([true, stateRow("bogus_state")]);
        renderPane({ ...baseAsset, isDistributable: true });

        expect(await screen.findByText("Unknown")).toBeInTheDocument();
    });

    it("does not read the state without the route", async () => {
        mockCanReadCompliance = false;
        renderPane({ ...baseAsset, isDistributable: true });

        await waitFor(() => expect(mockDownloadAsset).toHaveBeenCalled());
        expect(mockFetchComplianceState).not.toHaveBeenCalled();
    });
});
