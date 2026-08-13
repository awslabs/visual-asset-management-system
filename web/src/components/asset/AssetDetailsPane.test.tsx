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
