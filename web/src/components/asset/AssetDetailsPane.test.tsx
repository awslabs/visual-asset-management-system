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
import { act, render, screen, waitFor } from "@testing-library/react";
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
            assetId="a1"
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

    it("treats a fetched asset record without the field as not distributable", async () => {
        // The download, stream and preview APIs refuse a record without isDistributable, so the
        // pane does not offer the preview they would refuse.
        renderPane({ ...baseAsset });

        expect(await screen.findByText(/turned off for this/i)).toBeInTheDocument();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
    });

    it("shows no notice for the empty record held before the asset is fetched", () => {
        renderPane({});
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

/**
 * After an in-app navigation the route names the new asset at once, while ViewAsset still passes the
 * previous asset's record until the new one is fetched. The badge follows the route.
 */
describe("AssetDetailsPane compliance badge after navigating to another asset", () => {
    const stateRow = (databaseId: string, assetId: string, complianceState: string) => ({
        databaseId,
        assetId,
        complianceState,
    });
    const deferred = () => {
        let resolve: (value: any) => void = () => undefined;
        const promise = new Promise((done) => {
            resolve = done;
        });
        return { promise, resolve };
    };
    const pane = (databaseId: string, assetId: string, asset: any) => (
        <AssetDetailsPane
            asset={asset}
            databaseId={databaseId}
            assetId={assetId}
            onOpenUpdateAsset={jest.fn()}
            onOpenDeleteModal={jest.fn()}
        />
    );
    const previous = { ...baseAsset, assetId: "old", databaseId: "db1", isDistributable: true };

    beforeEach(() => {
        jest.clearAllMocks();
        mockCanReadCompliance = true;
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/preview.png"]);
    });

    it("requests the state of the route's asset, not of the record still shown", async () => {
        mockFetchComplianceState.mockResolvedValue([true, stateRow("db2", "new", "compliant")]);
        render(pane("db2", "new", previous));

        expect(await screen.findByText("Compliant")).toBeInTheDocument();
        expect(mockFetchComplianceState.mock.calls).toEqual([["db2", "new"]]);
    });

    it("clears the previous asset's badge until the new asset's state arrives", async () => {
        mockFetchComplianceState.mockResolvedValueOnce([
            true,
            stateRow("db1", "old", "quarantined"),
        ]);
        const { rerender } = render(pane("db1", "old", previous));
        expect(await screen.findByText("Quarantined")).toBeInTheDocument();

        const next = deferred();
        mockFetchComplianceState.mockReturnValueOnce(next.promise);
        rerender(pane("db2", "new", previous));
        expect(screen.queryByText("Quarantined")).not.toBeInTheDocument();

        await act(async () => next.resolve([true, stateRow("db2", "new", "compliant")]));
        expect(screen.getByText("Compliant")).toBeInTheDocument();
        expect(mockFetchComplianceState.mock.calls).toEqual([
            ["db1", "old"],
            ["db2", "new"],
        ]);
    });

    it("drops a late response for the previous asset", async () => {
        const late = deferred();
        mockFetchComplianceState.mockReturnValueOnce(late.promise);
        const { rerender } = render(pane("db1", "old", previous));

        mockFetchComplianceState.mockResolvedValueOnce([true, stateRow("db2", "new", "compliant")]);
        rerender(pane("db2", "new", previous));
        expect(await screen.findByText("Compliant")).toBeInTheDocument();

        await act(async () => late.resolve([true, stateRow("db1", "old", "quarantined")]));
        expect(screen.queryByText("Quarantined")).not.toBeInTheDocument();
        expect(screen.getByText("Compliant")).toBeInTheDocument();
    });
});

/**
 * The tag line labels each tag with its tag type from the list ViewAsset caches in localStorage. That
 * fetch runs in the background, so a page opened in a fresh browser renders before anything is cached.
 */
describe("AssetDetailsPane tags", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockCanReadCompliance = false;
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/preview.png"]);
    });
    afterEach(() => localStorage.clear());

    const taggedAsset = { ...baseAsset, isDistributable: true, tags: ["red", "blue"] };

    it("labels each tag with its cached tag type", async () => {
        localStorage.setItem(
            "tagTypes",
            JSON.stringify([{ tagTypeName: "Color", required: "True", tags: ["red"] }])
        );
        renderPane(taggedAsset);

        expect(screen.getByText("red (Color [R]), blue")).toBeInTheDocument();
        await waitFor(() => expect(mockDownloadAsset).toHaveBeenCalled());
    });

    it.each([
        ["before any tag types are cached", null],
        ["when the cached value is a failed fetch", "false"],
    ])("renders the bare tags %s", async (_label, stored) => {
        if (stored !== null) localStorage.setItem("tagTypes", stored);
        renderPane(taggedAsset);

        expect(screen.getByText("red, blue")).toBeInTheDocument();
        await waitFor(() => expect(mockDownloadAsset).toHaveBeenCalled());
    });
});
