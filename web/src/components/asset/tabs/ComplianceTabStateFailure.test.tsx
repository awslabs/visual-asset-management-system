/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * D105: the asset Compliance tab's state section when the state request fails. It used to show
 * nothing (no state, no error, no way to retry), and a failed reload kept the state read before.
 * Now it says the state could not be loaded, with the reason and a Retry button, and shows no
 * state read before, for this asset or the previous one.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ComplianceTab } from "./ComplianceTab";
import * as permissions from "../../../features/orchestration/permissions/useAllowedRoutes";

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useNavigate: () => jest.fn(),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/ComplianceService", () => ({
    ...jest.requireActual("../../../services/ComplianceService"),
    fetchComplianceState: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
    evaluateAssetCompliance: jest.fn(),
    releaseFromQuarantine: jest.fn(),
    grantException: jest.fn(),
    revokeException: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../../services/ComplianceService");

const quarantined = [
    true,
    {
        databaseId: "db1",
        assetId: "asset-1",
        complianceState: "quarantined",
        schemaName: "previous-state-schema",
    },
];
const RETRY = { name: "Retry loading the compliance state" };

const deferred = () => {
    let resolve: (value: any) => void = () => undefined;
    const promise = new Promise((done) => {
        resolve = done;
    });
    return { promise, resolve };
};

/** Nothing of a state is shown: no state label, schema or state action. */
const expectNoState = () => {
    expect(screen.queryByText("Current State")).not.toBeInTheDocument();
    expect(screen.queryByText("previous-state-schema")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Grant Exception" })).not.toBeInTheDocument();
};

describe("ComplianceTab state section when the state request fails (D105)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: () => true,
            loading: false,
        });
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            { evaluations: [], nextToken: undefined },
        ]);
    });

    it("says the state could not be loaded, with the reason, and shows no state", async () => {
        service().fetchComplianceState.mockResolvedValue([false, "Failed to fetch"]);
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);

        expect(
            await screen.findByText("Could not load the compliance state: Failed to fetch")
        ).toBeInTheDocument();
        expect(screen.getByRole("button", RETRY)).toBeInTheDocument();
        expectNoState();
        // The history section still loads on its own.
        expect(screen.getByText(/No evaluations recorded/)).toBeInTheDocument();
    });

    it("gives the server's message, or none when there is none", async () => {
        service().fetchComplianceState.mockResolvedValueOnce([false, "Not Authorized"]);
        const { unmount } = render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        expect(
            await screen.findByText("Could not load the compliance state: Not Authorized")
        ).toBeInTheDocument();
        unmount();

        service().fetchComplianceState.mockResolvedValueOnce([false, ""]);
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        expect(await screen.findByText("Could not load the compliance state.")).toBeInTheDocument();
    });

    it("loads the state again on Retry and shows it once the read succeeds", async () => {
        service()
            .fetchComplianceState.mockResolvedValueOnce([false, "Failed to fetch"])
            .mockResolvedValue(quarantined);
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await userEvent.click(await screen.findByRole("button", RETRY));

        expect(await screen.findByRole("button", { name: "Release" })).toBeInTheDocument();
        expect(service().fetchComplianceState).toHaveBeenCalledTimes(2);
        expect(service().fetchComplianceState.mock.calls[1]).toEqual(["db1", "asset-1"]);
        expect(screen.getByText("previous-state-schema")).toBeInTheDocument();
        expect(screen.queryByText(/Could not load the compliance state/)).not.toBeInTheDocument();
        expect(screen.queryByRole("button", RETRY)).not.toBeInTheDocument();
    });

    it("clears the state read before when a later read fails", async () => {
        service().fetchComplianceState.mockResolvedValueOnce(quarantined);
        service().evaluateAssetCompliance.mockResolvedValue([true, { message: "ok" }]);
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        expect(await screen.findByText("previous-state-schema")).toBeInTheDocument();

        service().fetchComplianceState.mockResolvedValueOnce([false, "Failed to fetch"]);
        await userEvent.click(screen.getByRole("button", { name: "Evaluate Now" }));

        expect(
            await screen.findByText("Could not load the compliance state: Failed to fetch")
        ).toBeInTheDocument();
        expectNoState();
    });

    it("shows neither the previous asset's state nor its own when the next asset's read fails", async () => {
        service().fetchComplianceState.mockResolvedValueOnce(quarantined);
        const { rerender } = render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        expect(await screen.findByText("previous-state-schema")).toBeInTheDocument();

        const next = deferred();
        service().fetchComplianceState.mockReturnValueOnce(next.promise);
        rerender(<ComplianceTab databaseId="db2" assetId="asset-2" isActive />);
        await act(async () => next.resolve([false, "Failed to fetch"]));

        expect(
            await screen.findByText("Could not load the compliance state: Failed to fetch")
        ).toBeInTheDocument();
        expect(service().fetchComplianceState.mock.calls.at(-1)).toEqual(["db2", "asset-2"]);
        expectNoState();
    });
});
