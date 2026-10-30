/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset ID label follows the configured asset synonym.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { AssetDetailsPane } from "./AssetDetailsPane";

jest.mock("../../synonyms", () => ({
    __esModule: true,
    default: {
        Asset: "Model",
        Assets: "Models",
        asset: "model",
        assets: "models",
        Database: "Project",
        Databases: "Projects",
        database: "project",
        databases: "projects",
        Comment: "Comment",
        Comments: "Comments",
        comment: "comment",
        comments: "comments",
    },
}));
jest.mock("react-router", () => ({ useNavigate: () => jest.fn() }));
jest.mock("../../services/APIService", () => ({
    downloadAsset: jest.fn().mockResolvedValue([true, "https://example.test/preview.png"]),
    fetchSubscriptionStatus: jest.fn().mockResolvedValue([false, { subscribed: false }]),
    createSubscription: jest.fn(),
    deleteSubscription: jest.fn(),
    fetchAssetVersions: jest.fn().mockResolvedValue([true, []]),
    // The pane reads the allowed API routes before it fetches the compliance badge.
    fetchAllowedApiRoutes: jest.fn().mockResolvedValue([true, { routes: [], userId: "u" }]),
}));
jest.mock("../common/StatusMessage", () => ({
    useStatusMessage: () => ({ showMessage: jest.fn() }),
}));

describe("AssetDetailsPane synonyms", () => {
    it("labels the asset ID with the asset synonym", () => {
        render(
            <AssetDetailsPane
                asset={{
                    assetId: "a1",
                    assetName: "Widget",
                    description: "A widget",
                    databaseId: "db1",
                    isDistributable: true,
                }}
                databaseId="db1"
                assetId="a1"
                onOpenUpdateAsset={jest.fn()}
                onOpenDeleteModal={jest.fn()}
            />
        );
        expect(screen.getByText("Model Id")).toBeInTheDocument();
        expect(screen.queryByText("Asset Id")).toBeNull();
    });
});
