/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The delete dialog offers "Permanently delete ... instead of archiving" only when the page says the
 * role may permanently delete; archiving stays available either way.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import AssetDeleteModal from "./AssetDeleteModal";

jest.mock("../../services/APIService", () => ({
    archiveAssetDelete: jest.fn(),
    deleteAssetPermanentDelete: jest.fn(),
    archiveFile: jest.fn(),
    deleteFilePermanent: jest.fn(),
}));

const asset = { assetId: "a1", assetName: "Bracket", databaseId: "db1" };

const mount = (props: Record<string, unknown> = {}) =>
    render(
        <AssetDeleteModal
            visible={true}
            onDismiss={() => undefined}
            onSuccess={() => undefined}
            mode="asset"
            selectedAssets={[asset]}
            databaseId="db1"
            {...props}
        />
    );

describe("AssetDeleteModal permanent delete offer", () => {
    it("offers the permanent delete toggle by default", () => {
        mount();
        expect(screen.getByText(/Permanently delete .* instead of archiving/)).toBeInTheDocument();
    });

    it("offers only archiving when the role may not permanently delete", () => {
        mount({ canPermanentlyDelete: false });
        expect(screen.queryByText(/Permanently delete .* instead of archiving/)).toBeNull();
        expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
    });
});
