/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ComplianceTab } from "./ComplianceTab";
import * as permissions from "../../../features/orchestration/permissions/useAllowedRoutes";
import { COMPLIANCE_LISTING_PAGE_SIZE } from "../../../services/ComplianceService";

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

const evaluation = (evaluationId: string, schemaName: string) => ({
    evaluationId,
    databaseId: "db1",
    assetId: "asset-1",
    schemaName,
    result: "compliant",
    evaluatedAt: "2026-01-01T00:00:00Z",
});

const renderTab = () => render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);

describe("ComplianceTab", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: () => true,
            loading: false,
        });
        service().fetchComplianceState.mockResolvedValue([
            true,
            { databaseId: "db1", assetId: "asset-1", complianceState: "quarantined" },
        ]);
    });

    it("pages the evaluation history with the backend token", async () => {
        service().fetchEvaluationHistory.mockImplementation(
            async (_db: string, _asset: string, paging: any) => {
                if (paging.startingToken === "tok-2") {
                    return [
                        true,
                        {
                            evaluations: [evaluation("ev-2", "second-page-schema")],
                            nextToken: undefined,
                        },
                    ];
                }
                return [
                    true,
                    { evaluations: [evaluation("ev-1", "first-page-schema")], nextToken: "tok-2" },
                ];
            }
        );

        renderTab();
        await screen.findByText("first-page-schema");
        expect(screen.getByText("(1+)")).toBeInTheDocument();
        expect(service().fetchEvaluationHistory.mock.calls[0]).toEqual([
            "db1",
            "asset-1",
            { maxItems: COMPLIANCE_LISTING_PAGE_SIZE, startingToken: undefined },
        ]);

        await userEvent.click(screen.getByRole("button", { name: "Next page of evaluations" }));

        await screen.findByText("second-page-schema");
        expect(screen.queryByText("first-page-schema")).not.toBeInTheDocument();
        expect(service().fetchEvaluationHistory.mock.calls[1][2]).toEqual({
            maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
            startingToken: "tok-2",
        });
    });

    it("collects the exception reason in a modal and sends it", async () => {
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            { evaluations: [], nextToken: undefined },
        ]);
        service().grantException.mockResolvedValue([true, "Exception granted"]);

        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Grant Exception" }));

        await userEvent.type(screen.getByLabelText("Reason for exception"), "Accepted risk");
        await userEvent.click(screen.getByRole("button", { name: "Grant exception" }));

        await waitFor(() => {
            expect(service().grantException).toHaveBeenCalledWith(
                "db1",
                "asset-1",
                "Accepted risk"
            );
        });
        expect(await screen.findByText("Exception granted")).toBeInTheDocument();
    });
});

describe("ComplianceTab exception state", () => {
    const exceptionState = {
        databaseId: "db1",
        assetId: "asset-1",
        complianceState: "exception",
        schemaName: "std",
        lastEvaluatedAt: "2026-02-01T10:00:00Z",
        exceptionGranted: true,
        exceptionReason: "Accepted legacy scan",
        exceptionGrantedBy: "reviewer",
        exceptionGrantedAt: "2026-02-02T09:30:00Z",
        exceptionSchemaName: "std",
        exceptionSchemaVersion: 3,
    };

    beforeEach(() => {
        jest.clearAllMocks();
        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: () => true,
            loading: false,
        });
        service().fetchComplianceState.mockResolvedValue([true, exceptionState]);
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            {
                evaluations: [{ ...evaluation("ev-1", "std"), exceptionApplied: true }],
                nextToken: undefined,
            },
        ]);
    });

    it("renders the exception badge and details, and marks evaluations it applied to", async () => {
        renderTab();

        expect(await screen.findByText("Exception")).toBeInTheDocument();
        expect(screen.getByText("Active exception")).toBeInTheDocument();
        expect(screen.getByText("Accepted legacy scan")).toBeInTheDocument();
        expect(screen.getByText("reviewer")).toBeInTheDocument();
        expect(
            screen.getByText(new Date("2026-02-02T09:30:00Z").toLocaleString())
        ).toBeInTheDocument();
        expect(screen.getByText("std (version 3)")).toBeInTheDocument();
        expect(
            screen.getByText(new Date("2026-02-01T10:00:00Z").toLocaleString())
        ).toBeInTheDocument();
        expect(screen.getByText("Exception applied")).toBeInTheDocument();
        // Quarantine-only actions stay hidden while the asset is released.
        expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Grant Exception" })).not.toBeInTheDocument();
    });

    it("revokes the exception after confirmation and reloads the state", async () => {
        service().revokeException.mockResolvedValue([
            true,
            {
                message: "Exception revoked",
                databaseId: "db1",
                assetId: "asset-1",
                complianceState: "quarantined",
            },
        ]);

        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Revoke exception" }));

        const dialog = screen.getByRole("dialog", { name: "Revoke compliance exception" });
        expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
        expect(service().revokeException).not.toHaveBeenCalled();

        await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));

        await waitFor(() => {
            expect(service().revokeException).toHaveBeenCalledWith("db1", "asset-1");
        });
        expect(await screen.findByText("Exception revoked")).toBeInTheDocument();
        expect(service().fetchComplianceState).toHaveBeenCalledTimes(2);
    });

    it("cancelling the confirmation revokes nothing", async () => {
        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Revoke exception" }));
        const dialog = screen.getByRole("dialog", { name: "Revoke compliance exception" });
        await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

        expect(service().revokeException).not.toHaveBeenCalled();
        // Cloudscape keeps a dismissed modal mounted and marks it hidden.
        await waitFor(() => {
            expect(dialog.className).toMatch(/awsui_hidden/);
        });
    });

    it("surfaces the backend refusal when no exception is active", async () => {
        service().revokeException.mockResolvedValue([false, "No exception is active"]);

        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Revoke exception" }));
        const dialog = screen.getByRole("dialog", { name: "Revoke compliance exception" });
        await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));

        expect(await screen.findByText("No exception is active")).toBeInTheDocument();
    });

    it("hides the revoke action without the DELETE route", async () => {
        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: (method: string) => method !== "DELETE",
            loading: false,
        });

        renderTab();
        await screen.findByText("Active exception");
        expect(screen.queryByRole("button", { name: "Revoke exception" })).not.toBeInTheDocument();
    });
});

describe("ComplianceTab evaluation errors", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: () => true,
            loading: false,
        });
    });

    it("flags a record whose last evaluation produced no verdict beside its unchanged state", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            {
                databaseId: "db1",
                assetId: "asset-1",
                complianceState: "compliant",
                lastEvaluationStatus: "error",
            },
        ]);
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            {
                evaluations: [{ ...evaluation("ev-1", "std"), result: "error", status: "error" }],
                nextToken: undefined,
            },
        ]);

        renderTab();

        expect(await screen.findByText("Evaluation error")).toBeInTheDocument();
        // The state badge is the state the asset held before the errored evaluation.
        expect(screen.getByText("Compliant")).toBeInTheDocument();
        expect(screen.getByText(/produced no verdict/)).toBeInTheDocument();
        // The history row shows the evaluation's own error verdict.
        expect(screen.getByText("Error")).toBeInTheDocument();
    });

    it("shows no error indicator for a completed last evaluation", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            {
                databaseId: "db1",
                assetId: "asset-1",
                complianceState: "compliant",
                lastEvaluationStatus: "completed",
            },
        ]);
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            { evaluations: [evaluation("ev-1", "std")], nextToken: undefined },
        ]);

        renderTab();
        await screen.findByText("std");

        expect(screen.queryByText("Evaluation error")).not.toBeInTheDocument();
        expect(screen.queryByText("Rule errors")).not.toBeInTheDocument();
        expect(screen.queryByText(/Not evaluated/)).not.toBeInTheDocument();
    });

    it("lists errored rules apart from the failed ones on an evaluation row", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            {
                databaseId: "db1",
                assetId: "asset-1",
                complianceState: "non_compliant",
                lastEvaluationStatus: "completed",
            },
        ]);
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            {
                evaluations: [
                    {
                        ...evaluation("ev-1", "std"),
                        result: "non_compliant",
                        status: "completed",
                        violations: ["owner missing"],
                        hasRuleErrors: true,
                        errorRules: ["geometry-check"],
                    },
                ],
                nextToken: undefined,
            },
        ]);

        renderTab();
        const row = (await screen.findByText("owner missing")).closest("tr") as HTMLElement;

        expect(within(row).getByText("Rule errors")).toBeInTheDocument();
        expect(within(row).getByText("Not evaluated: geometry-check")).toBeInTheDocument();
        // The verdict came from the remaining rules, so the row is Non-Compliant, not Error.
        expect(within(row).getAllByText("Non-Compliant")).toHaveLength(1);
        // A completed evaluation with rule errors leaves the state indicator alone.
        expect(screen.queryByText("Evaluation error")).not.toBeInTheDocument();
    });

    it("reads errored rules out of JSON-encoded rule results when errorRules is absent", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            { databaseId: "db1", assetId: "asset-1", complianceState: "compliant" },
        ]);
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            {
                evaluations: [
                    {
                        ...evaluation("ev-1", "std"),
                        violations: [],
                        ruleResults: JSON.stringify([
                            {
                                ruleName: "converts",
                                ruleType: "pipeline",
                                enforcement: "quarantine",
                                passed: false,
                                status: "error",
                                message: "Input selection did not match exactly one file",
                            },
                            {
                                ruleName: "owner",
                                ruleType: "metadata",
                                enforcement: "warn",
                                passed: true,
                            },
                        ]),
                    },
                ],
                nextToken: undefined,
            },
        ]);

        renderTab();

        expect(await screen.findByText("Not evaluated: converts")).toBeInTheDocument();
        expect(screen.getByText("Rule errors")).toBeInTheDocument();
    });
});
