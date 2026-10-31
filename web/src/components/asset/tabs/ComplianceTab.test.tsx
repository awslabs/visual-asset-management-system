/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Synonyms from "../../../synonyms";
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
        expect(
            await screen.findByText(`Exception granted for this ${Synonyms.asset}.`)
        ).toBeInTheDocument();
    });

    it("collects the release reason in a modal and sends it", async () => {
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            { evaluations: [], nextToken: undefined },
        ]);
        service().releaseFromQuarantine.mockResolvedValue([true, "Released from quarantine"]);

        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Release" }));

        // Opening the dialog releases nothing; the reason is confirmed first.
        const dialog = screen.getByRole("dialog", { name: "Release from quarantine" });
        expect(service().releaseFromQuarantine).not.toHaveBeenCalled();

        await userEvent.type(
            within(dialog).getByLabelText("Reason for release"),
            "Reviewed and cleared"
        );
        await userEvent.click(within(dialog).getByRole("button", { name: "Release asset" }));

        await waitFor(() => {
            expect(service().releaseFromQuarantine).toHaveBeenCalledWith(
                "db1",
                "asset-1",
                "Reviewed and cleared"
            );
        });
        expect(
            await screen.findByText(`The ${Synonyms.asset} was released from quarantine.`)
        ).toBeInTheDocument();
        expect(service().fetchComplianceState).toHaveBeenCalledTimes(2);
    });

    it("keeps the release dialog open and releases nothing without a reason", async () => {
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            { evaluations: [], nextToken: undefined },
        ]);

        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Release" }));
        const dialog = screen.getByRole("dialog", { name: "Release from quarantine" });

        await userEvent.click(within(dialog).getByRole("button", { name: "Release asset" }));

        expect(within(dialog).getByText("A reason is required.")).toBeInTheDocument();
        expect(service().releaseFromQuarantine).not.toHaveBeenCalled();
        expect(dialog.className).not.toMatch(/awsui_hidden/);
    });

    it("cancelling the release dialog releases nothing", async () => {
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            { evaluations: [], nextToken: undefined },
        ]);

        renderTab();
        await userEvent.click(await screen.findByRole("button", { name: "Release" }));
        const dialog = screen.getByRole("dialog", { name: "Release from quarantine" });
        await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

        expect(service().releaseFromQuarantine).not.toHaveBeenCalled();
        await waitFor(() => {
            expect(dialog.className).toMatch(/awsui_hidden/);
        });
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

    it("shows a failed pipeline execution's reason beside the generic violation", async () => {
        // The API returns the reason only to a caller who may read the execution.
        const reason = "ValueError: latin1.obj could not be read as OBJ text";
        service().fetchComplianceState.mockResolvedValue([
            true,
            {
                databaseId: "db1",
                assetId: "asset-1",
                complianceState: "quarantined",
                quarantineReason: "Pipeline execution FAILED",
                quarantineExecutionFailures: [
                    { ruleName: "converts", executionId: "e1", executionFailureReason: reason },
                ],
            },
        ]);
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            {
                evaluations: [
                    {
                        ...evaluation("ev-1", "std"),
                        result: "quarantined",
                        violations: ["Pipeline execution FAILED"],
                        ruleResults: JSON.stringify([
                            {
                                ruleName: "converts",
                                ruleType: "pipeline",
                                enforcement: "quarantine",
                                passed: false,
                                status: "evaluated",
                                message: "Pipeline execution FAILED",
                                executionId: "e1",
                                executionFailureReason: reason,
                            },
                        ]),
                    },
                ],
                nextToken: undefined,
            },
        ]);

        renderTab();

        const row = (await screen.findByText("Pipeline execution FAILED")).closest(
            "tr"
        ) as HTMLElement;
        expect(within(row).getByText(`Pipeline failure (converts): ${reason}`)).toBeInTheDocument();
        // The quarantined state lists the same reason under the current state.
        expect(screen.getAllByText(`Pipeline failure (converts): ${reason}`)).toHaveLength(2);
    });

    it("shows the generic violation alone when the reason is withheld", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            {
                databaseId: "db1",
                assetId: "asset-1",
                complianceState: "quarantined",
                quarantineReason: "Pipeline execution FAILED",
                quarantineExecutionFailures: [{ ruleName: "converts", executionId: "e1" }],
            },
        ]);
        service().fetchEvaluationHistory.mockResolvedValue([
            true,
            {
                evaluations: [
                    {
                        ...evaluation("ev-1", "std"),
                        result: "quarantined",
                        violations: ["Pipeline execution FAILED"],
                        ruleResults: JSON.stringify([
                            {
                                ruleName: "converts",
                                ruleType: "pipeline",
                                enforcement: "quarantine",
                                passed: false,
                                status: "evaluated",
                                message: "Pipeline execution FAILED",
                                executionId: "e1",
                            },
                        ]),
                    },
                ],
                nextToken: undefined,
            },
        ]);

        renderTab();

        expect(await screen.findByText("Pipeline execution FAILED")).toBeInTheDocument();
        expect(screen.queryByText(/Pipeline failure/)).not.toBeInTheDocument();
    });
});

describe("ComplianceTab schema source", () => {
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

    it.each([
        ["database", `Inherited from the ${Synonyms.database}`],
        ["asset", `Set on the ${Synonyms.asset}`],
    ])("says a %s binding is %s", async (schemaSource, text) => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            {
                databaseId: "db1",
                assetId: "asset-1",
                complianceState: "compliant",
                schemaName: "std",
                schemaSource,
            },
        ]);
        renderTab();
        expect(await screen.findByText("std")).toBeInTheDocument();
        expect(screen.getByText(text)).toBeInTheDocument();
    });
});

describe("ComplianceTab archived asset", () => {
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

    const archivedRecord = (recordedComplianceState: string, extra: object = {}) => ({
        databaseId: "db1",
        assetId: "asset-1",
        complianceState: "archived",
        recordedComplianceState,
        schemaName: "std",
        schemaSource: "database",
        ...extra,
    });

    it("says the asset is archived and not evaluated instead of Pending Evaluation", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            archivedRecord("pending_evaluation"),
        ]);
        renderTab();

        expect(await screen.findByText("Archived (not evaluated)")).toBeInTheDocument();
        expect(screen.getByText(`Archived ${Synonyms.asset}`)).toBeInTheDocument();
        expect(screen.getByText(/left out of the compliance overview/)).toBeInTheDocument();
        // The recorded state is shown apart, as the state that applies again on unarchive.
        expect(screen.getByText("Recorded State")).toBeInTheDocument();
        expect(screen.getByText("Pending Evaluation")).toBeInTheDocument();
        // Nothing is evaluated while the asset is archived, so the tab offers no evaluation.
        expect(screen.queryByRole("button", { name: "Evaluate Now" })).not.toBeInTheDocument();
    });

    it("offers no quarantine or exception action on an archived asset", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            archivedRecord("exception", {
                exceptionGranted: true,
                exceptionReason: "accepted",
                exceptionSchemaName: "std",
                exceptionSchemaVersion: 1,
            }),
        ]);
        renderTab();

        expect(await screen.findByText("Archived (not evaluated)")).toBeInTheDocument();
        expect(screen.getByText("Active exception")).toBeInTheDocument();
        for (const name of ["Release", "Grant Exception", "Revoke exception", "Evaluate Now"]) {
            expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
        }
    });

    it("shows a live asset's state with its actions and no archived notice", async () => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            { databaseId: "db1", assetId: "asset-1", complianceState: "pending_evaluation" },
        ]);
        renderTab();

        expect(await screen.findByText("Pending Evaluation")).toBeInTheDocument();
        expect(screen.queryByText(`Archived ${Synonyms.asset}`)).not.toBeInTheDocument();
        expect(screen.queryByText("Recorded State")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Evaluate Now" })).toBeInTheDocument();
    });
});

/**
 * An in-app navigation to another asset keeps the tab mounted with new ids. Nothing read for the
 * previous asset may be shown for the new one, or offered as an action on it.
 */
describe("ComplianceTab after navigating to another asset", () => {
    const deferred = () => {
        let resolve: (value: any) => void = () => undefined;
        const promise = new Promise((done) => {
            resolve = done;
        });
        return { promise, resolve };
    };
    const quarantined = [
        true,
        { databaseId: "db1", assetId: "asset-1", complianceState: "quarantined" },
    ];
    const compliant = [
        true,
        { databaseId: "db2", assetId: "asset-2", complianceState: "compliant" },
    ];

    beforeEach(() => {
        jest.clearAllMocks();
        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: () => true,
            loading: false,
        });
        service().fetchEvaluationHistory.mockImplementation(async (database: string) => [
            true,
            {
                evaluations:
                    database === "db1" ? [evaluation("ev-1", "previous-asset-schema")] : [],
                nextToken: undefined,
            },
        ]);
    });

    it("shows nothing read for the previous asset while the next one loads", async () => {
        service().fetchComplianceState.mockResolvedValueOnce(quarantined);
        const { rerender } = render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        expect(await screen.findByRole("button", { name: "Release" })).toBeInTheDocument();
        expect(screen.getByText("previous-asset-schema")).toBeInTheDocument();

        const next = deferred();
        service().fetchComplianceState.mockReturnValueOnce(next.promise);
        rerender(<ComplianceTab databaseId="db2" assetId="asset-2" isActive />);
        expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
        expect(screen.queryByText("previous-asset-schema")).not.toBeInTheDocument();

        await act(async () => next.resolve(compliant));
        expect(service().fetchComplianceState.mock.calls.at(-1)).toEqual(["db2", "asset-2"]);
        expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
    });

    it("drops a late response for the previous asset, also while the tab is closed", async () => {
        const late = deferred();
        service().fetchComplianceState.mockReturnValueOnce(late.promise);
        const { rerender } = render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(service().fetchComplianceState).toHaveBeenCalledTimes(1));

        rerender(<ComplianceTab databaseId="db2" assetId="asset-2" isActive={false} />);
        await act(async () => late.resolve(quarantined));
        expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
        expect(screen.queryByText("previous-asset-schema")).not.toBeInTheDocument();
    });
});
