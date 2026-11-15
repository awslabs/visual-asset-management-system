/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An action the API refuses on the asset Compliance tab is followed by a fresh read of the state
 * and the history, as a successful one is (D162). With one quarantined asset open in two tabs, the
 * first released it and the second's Release answered "Asset is not quarantined"; the second tab
 * then went on showing Quarantined with Release and Grant Exception until it was reloaded, because
 * it read the state again only after a success. The same held for the D159 answer ("record changed
 * ... try again"), a refused exception grant or revoke, and a refused Evaluate Now. Now the refusal
 * stays on the tab, and the state, the actions offered and the asset header's badge follow the
 * record as the API holds it after the refusal.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Synonyms from "../../../synonyms";
import { ComplianceTab } from "./ComplianceTab";

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useNavigate: () => jest.fn(),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
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

const RECORD_CHANGED =
    "The asset's compliance record changed while the request was made; try again";

const record = (complianceState: string, extra: Record<string, unknown> = {}) => ({
    databaseId: "db1",
    assetId: "asset-1",
    complianceState,
    schemaName: "std",
    schemaSource: "database",
    ...extra,
});
const QUARANTINED = record("quarantined", { quarantineReason: "metadata missing" });
const COMPLIANT = record("compliant", { exceptionGranted: false });
const EXCEPTION = record("exception", {
    exceptionGranted: true,
    exceptionReason: "Accepted risk",
    exceptionGrantedBy: "other-user",
    exceptionGrantedAt: "2026-11-07T02:00:00Z",
    exceptionSchemaName: "std",
    exceptionSchemaVersion: 1,
});

// The state section's current-state badge (the Assigned Schema and history sit beside it).
const currentState = () =>
    (screen.getByText("Current State").parentElement as HTMLElement).textContent || "";

// The state each read answers, in order; the last one repeats.
const statesRead = (...states: object[]) => {
    let reads = 0;
    service().fetchComplianceState.mockImplementation(async () => {
        const state = states[Math.min(reads, states.length - 1)];
        reads += 1;
        return [true, state];
    });
};

const announced: any[] = [];
let stopListening: () => void = () => undefined;

beforeEach(() => {
    jest.clearAllMocks();
    announced.length = 0;
    stopListening = service().onComplianceStateAnnounced((...args: any[]) => announced.push(args));
    service().fetchEvaluationHistory.mockResolvedValue([
        true,
        { evaluations: [], nextToken: undefined },
    ]);
});

afterEach(() => stopListening());

const renderTab = async () => {
    render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
    await waitFor(() => expect(service().fetchEvaluationHistory).toHaveBeenCalledTimes(1));
};

// The tab has read the state and the history again after the action, and the read has settled.
const reread = async () => {
    await waitFor(() => expect(service().fetchComplianceState).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(service().fetchEvaluationHistory).toHaveBeenCalledTimes(2));
};

const confirmIn = async (dialogName: string, reasonLabel: string | null, confirm: string) => {
    const dialog = screen.getByRole("dialog", { name: dialogName });
    if (reasonLabel) {
        await userEvent.type(within(dialog).getByLabelText(reasonLabel), "Reviewed");
    }
    await userEvent.click(within(dialog).getByRole("button", { name: confirm }));
};

describe("ComplianceTab after a refused action (D162)", () => {
    it("reads the state again after a refused release and offers only what the record allows", async () => {
        // Another tab released the asset; this one still shows it quarantined.
        statesRead(QUARANTINED, COMPLIANT);
        service().releaseFromQuarantine.mockResolvedValue([false, "Asset is not quarantined"]);

        await renderTab();
        expect(currentState()).toContain("Quarantined");
        await userEvent.click(screen.getByRole("button", { name: "Release" }));
        await confirmIn(
            "Release from quarantine",
            "Reason for release",
            `Release ${Synonyms.asset}`
        );

        await reread();
        // The refusal stays, beside the state the API now holds.
        expect(screen.getByText("Asset is not quarantined")).toBeInTheDocument();
        await waitFor(() => expect(currentState()).toContain("Compliant"));
        expect(currentState()).not.toContain("Quarantined");
        expect(screen.queryByRole("button", { name: "Release" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Grant Exception" })).toBeNull();
        expect(screen.getByRole("button", { name: "Evaluate Now" })).toBeEnabled();
        // The asset header's badge follows the state read after the refusal (D131).
        expect(announced[announced.length - 1]).toEqual(["db1", "asset-1", COMPLIANT]);
    });

    it("reads the state again after a release refused because the record changed (D159)", async () => {
        // The record changed while the request was made: an exception was granted meanwhile.
        statesRead(QUARANTINED, EXCEPTION);
        service().releaseFromQuarantine.mockResolvedValue([false, RECORD_CHANGED]);

        await renderTab();
        await userEvent.click(screen.getByRole("button", { name: "Release" }));
        await confirmIn(
            "Release from quarantine",
            "Reason for release",
            `Release ${Synonyms.asset}`
        );

        await reread();
        expect(screen.getByText(RECORD_CHANGED)).toBeInTheDocument();
        await waitFor(() => expect(currentState()).toContain("Exception"));
        expect(screen.queryByRole("button", { name: "Release" })).toBeNull();
        expect(screen.getByRole("button", { name: "Revoke exception" })).toBeInTheDocument();
        expect(screen.getByText("Active exception")).toBeInTheDocument();
        expect(announced[announced.length - 1]).toEqual(["db1", "asset-1", EXCEPTION]);
    });

    it("reads the state again after a refused exception grant", async () => {
        statesRead(QUARANTINED, COMPLIANT);
        service().grantException.mockResolvedValue([false, "Asset is not quarantined"]);

        await renderTab();
        await userEvent.click(screen.getByRole("button", { name: "Grant Exception" }));
        await confirmIn("Grant compliance exception", "Reason for exception", "Grant exception");

        await reread();
        expect(screen.getByText("Asset is not quarantined")).toBeInTheDocument();
        await waitFor(() => expect(currentState()).toContain("Compliant"));
        expect(screen.queryByRole("button", { name: "Grant Exception" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Release" })).toBeNull();
    });

    it("reads the state again after a refused exception revoke", async () => {
        // Another user revoked the exception; the asset went back to its verdict.
        statesRead(EXCEPTION, QUARANTINED);
        service().revokeException.mockResolvedValue([false, "No exception is active"]);

        await renderTab();
        await userEvent.click(screen.getByRole("button", { name: "Revoke exception" }));
        await confirmIn("Revoke compliance exception", null, "Revoke");

        await reread();
        expect(screen.getByText("No exception is active")).toBeInTheDocument();
        await waitFor(() => expect(currentState()).toContain("Quarantined"));
        expect(screen.queryByRole("button", { name: "Revoke exception" })).toBeNull();
        expect(screen.queryByText("Active exception")).toBeNull();
        expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument();
    });

    it("reads the state again after a refused Evaluate Now", async () => {
        statesRead(record("pending_evaluation"), record("non_compliant"));
        service().evaluateAssetCompliance.mockResolvedValue([
            false,
            "No schema specified and the asset has no bound schema",
        ]);

        await renderTab();
        await userEvent.click(screen.getByRole("button", { name: "Evaluate Now" }));

        await reread();
        expect(
            screen.getByText("No schema specified and the asset has no bound schema")
        ).toBeInTheDocument();
        await waitFor(() => expect(currentState()).toContain("Non-Compliant"));
    });

    it("clears the refusal when the next action succeeds", async () => {
        statesRead(QUARANTINED, QUARANTINED, COMPLIANT);
        service()
            .releaseFromQuarantine.mockResolvedValueOnce([false, RECORD_CHANGED])
            .mockResolvedValueOnce([true, "Released"]);

        await renderTab();
        await userEvent.click(screen.getByRole("button", { name: "Release" }));
        await confirmIn(
            "Release from quarantine",
            "Reason for release",
            `Release ${Synonyms.asset}`
        );
        await reread();
        expect(screen.getByText(RECORD_CHANGED)).toBeInTheDocument();

        // The record still reads quarantined, so Release is offered again; the retry succeeds.
        await userEvent.click(screen.getByRole("button", { name: "Release" }));
        await confirmIn(
            "Release from quarantine",
            "Reason for release",
            `Release ${Synonyms.asset}`
        );
        expect(
            await screen.findByText(`The ${Synonyms.asset} was released from quarantine.`)
        ).toBeInTheDocument();
        expect(screen.queryByText(RECORD_CHANGED)).toBeNull();
        await waitFor(() => expect(currentState()).toContain("Compliant"));
    });
});
