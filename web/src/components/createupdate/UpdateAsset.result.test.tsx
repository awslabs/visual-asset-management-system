/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Update Asset outcome. `updateAsset` resolves `[false, message]` for every rejected request (a
 * validation failure, a permission denial, a request the AWS WAF web ACL blocked) instead of
 * throwing, so the modal reads the result: a failure shows the error status with the message and does
 * not complete, and only an accepted update completes.
 */
import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { UpdateAsset } from "./UpdateAsset";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    fetchTagsForAsset: jest.fn(() => Promise.resolve([])),
    fetchTagTypesForAsset: jest.fn(() => Promise.resolve([])),
    updateAsset: jest.fn(),
}));
// The compliance schema field gates on this hook; a deny keeps these outcome tests focused.
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => false }),
}));

const { updateAsset } = jest.requireMock("../../services/APIService");

const BLOCKED = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";

const asset = {
    assetId: "asset1",
    assetName: "model",
    databaseId: "db1",
    description: "a model",
    isDistributable: true,
    tags: [],
};

const mount = () => {
    const onComplete = jest.fn();
    render(
        <MemoryRouter>
            <UpdateAsset asset={asset} isOpen={true} onClose={jest.fn()} onComplete={onComplete} />
        </MemoryRouter>
    );
    return { onComplete };
};

const submit = () =>
    fireEvent.click(screen.getByRole("button", { name: `Update ${Synonyms.Asset}` }));

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Update Asset outcome", () => {
    it("completes an update the API accepted", async () => {
        updateAsset.mockResolvedValue([true, "Asset updated successfully"]);
        const { onComplete } = mount();
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(updateAsset).toHaveBeenCalledWith(
            expect.objectContaining({ databaseId: "db1", assetId: "asset1" })
        );
    });

    it("shows a rejected update's message and does not complete", async () => {
        updateAsset.mockResolvedValue([false, BLOCKED]);
        const { onComplete } = mount();
        submit();
        expect(await screen.findByText(BLOCKED)).toBeInTheDocument();
        expect(screen.queryByText("Update complete")).not.toBeInTheDocument();
        expect(onComplete).not.toHaveBeenCalled();
    });

    it("clears a rejected update's message when the modal is closed and opened again", async () => {
        updateAsset.mockResolvedValue([false, BLOCKED]);
        const onClose = jest.fn();
        const modal = (isOpen: boolean) => (
            <MemoryRouter>
                <UpdateAsset
                    asset={asset}
                    isOpen={isOpen}
                    onClose={onClose}
                    onComplete={jest.fn()}
                />
            </MemoryRouter>
        );
        const { rerender } = render(modal(true));
        submit();
        expect(await screen.findByText(BLOCKED)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onClose).toHaveBeenCalled();
        rerender(modal(false));
        rerender(modal(true));
        expect(screen.queryByText(BLOCKED)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: `Update ${Synonyms.Asset}` })).toBeEnabled();
    });

    it("lets the user retry after a rejected update", async () => {
        updateAsset
            .mockResolvedValueOnce([false, "Asset name is invalid"])
            .mockResolvedValueOnce([true, "Asset updated successfully"]);
        const { onComplete } = mount();
        submit();
        expect(await screen.findByText("Asset name is invalid")).toBeInTheDocument();
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(screen.queryByText("Asset name is invalid")).not.toBeInTheDocument();
    });
});
