/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Neither compliance template grants an asset page, so the database compliance overview carries
 * each asset's evaluation history (verdict, schema and version, rule results including errored
 * rules) and, for an asset in `exception`, a Revoke exception action with the same confirmation as
 * the asset tab. Each is offered only on its route: a read-only role sees History and not Revoke.
 * Asset names stay plain text for a caller without asset routes.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatabaseCompliancePage from "./DatabaseCompliance";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
    revokeException: jest.fn(),
}));

let mockAllowed: string[] = [];
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (method: string, path: string) => mockAllowed.includes(`${method} ${path}`),
    }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const HISTORY = "GET /compliance/evaluations/{databaseId}/{assetId}";
const REVOKE = "DELETE /compliance/quarantine/{databaseId}/{assetId}/exception";
const READ = ["GET /compliance/state/{databaseId}", "GET /compliance/bind/{databaseId}"];

beforeEach(() => {
    jest.clearAllMocks();
    mockAllowed = [...READ, HISTORY];
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        {
            databaseId: "db1",
            totalAssets: 2,
            summary: { compliant: 1, exception: 1 },
            assets: [
                {
                    databaseId: "db1",
                    assetId: "a-ok",
                    assetName: "ok-asset",
                    complianceState: "compliant",
                    schemaName: "std",
                },
                {
                    databaseId: "db1",
                    assetId: "a-exc",
                    assetName: "excepted-asset",
                    complianceState: "exception",
                    schemaName: "std",
                },
            ],
        },
    ]);
    service().fetchEvaluationHistory.mockResolvedValue([
        true,
        {
            evaluations: [
                {
                    evaluationId: "e2",
                    databaseId: "db1",
                    assetId: "a-exc",
                    schemaName: "std",
                    schemaVersion: 3,
                    result: "quarantined",
                    verdict: "quarantined",
                    exceptionApplied: true,
                    hasRuleErrors: true,
                    evaluatedAt: "2026-03-02T00:00:00Z",
                    ruleResults: JSON.stringify([
                        {
                            ruleName: "units-required",
                            passed: false,
                            status: "evaluated",
                            message: "missing fw_units",
                        },
                        {
                            ruleName: "convert",
                            passed: false,
                            status: "error",
                            message: "Pipeline execution could not be started",
                        },
                    ]),
                },
            ],
        },
    ]);
});

it("opens an asset's evaluation history with schema version and rule results", async () => {
    render(<DatabaseCompliancePage />);
    await screen.findByText("excepted-asset");
    await userEvent.click(
        screen.getByRole("button", { name: "Evaluation history of excepted-asset" })
    );
    await waitFor(() =>
        expect(service().fetchEvaluationHistory).toHaveBeenCalledWith(
            "db1",
            "a-exc",
            expect.objectContaining({ startingToken: undefined })
        )
    );
    expect(await screen.findByText("Evaluation history: excepted-asset")).toBeInTheDocument();
    expect(screen.getByText("std (version 3)")).toBeInTheDocument();
    expect(screen.getByText("Exception applied")).toBeInTheDocument();
    // Each rule result is its name, its outcome and its message beneath.
    const ruleLine = (name: string) =>
        [...document.querySelectorAll("[data-rule-result]")].find((el) =>
            el.textContent?.startsWith(name)
        ) as HTMLElement;
    expect(within(ruleLine("units-required")).getByText("Failed")).toBeInTheDocument();
    expect(within(ruleLine("units-required")).getByText("missing fw_units")).toBeInTheDocument();
    expect(within(ruleLine("convert")).getByText("Error (not evaluated)")).toBeInTheDocument();
    expect(
        within(ruleLine("convert")).getByText("Pipeline execution could not be started")
    ).toBeInTheDocument();
});

it("offers History and not Revoke to a read-only caller, and keeps names plain text", async () => {
    render(<DatabaseCompliancePage />);
    await screen.findByText("excepted-asset");
    expect(screen.getAllByRole("button", { name: /^Evaluation history of/ })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /^Revoke the exception/ })).toBeNull();
    expect(screen.getByText("excepted-asset").closest("a")).toBeNull();
});

it("offers Revoke exception only on an asset in exception, behind the confirmation", async () => {
    mockAllowed = [...READ, HISTORY, REVOKE];
    service().revokeException.mockResolvedValue([
        true,
        { message: "Exception revoked", databaseId: "db1", assetId: "a-exc" },
    ]);
    render(<DatabaseCompliancePage />);
    await screen.findByText("excepted-asset");
    expect(screen.getAllByRole("button", { name: /^Revoke the exception/ })).toHaveLength(1);
    await userEvent.click(
        screen.getByRole("button", { name: "Revoke the exception of excepted-asset" })
    );
    const dialog = (await screen.findByText("Revoke compliance exception")).closest(
        '[role="dialog"]'
    ) as HTMLElement;
    expect(dialog).not.toBeNull();
    expect(service().revokeException).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(service().revokeException).toHaveBeenCalledWith("db1", "a-exc"));
    expect(await screen.findByText("Exception revoked for excepted-asset.")).toBeInTheDocument();
});

it("offers no row actions to a caller with neither route", async () => {
    mockAllowed = [...READ];
    render(<DatabaseCompliancePage />);
    await screen.findByText("excepted-asset");
    expect(screen.queryByRole("button", { name: /^Evaluation history of/ })).toBeNull();
});
