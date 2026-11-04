/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An evaluation closed without a verdict shows why, and no verdict (D125). The API closes an
 * evaluation a newer evaluation of the asset replaced while its pipeline rule waited (and one whose
 * asset or database went away while a rule was outstanding) with `closedReason` and a fixed
 * `errorMessage`. The asset Compliance tab's history row and the history dialog used to read that
 * row as "Compliant · Rule errors · Not evaluated: <rule>"; they now read "Not completed" with the
 * message, and no verdict, rule-error or waiting label.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import EvaluationHistoryModal from "./EvaluationHistoryModal";
import { ComplianceTab } from "../asset/tabs/ComplianceTab";
import { EvaluationRecord, evaluationClosedMessage } from "../../services/ComplianceService";

const mockTableProps: any[] = [];
jest.mock("@cloudscape-design/components/table", () => {
    const StandIn = (props: any) => {
        mockTableProps.push(props);
        return null;
    };
    return { __esModule: true, default: StandIn };
});

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useNavigate: () => jest.fn(),
}));

jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../../services/ComplianceService", () => ({
    ...jest.requireActual("../../services/ComplianceService"),
    fetchComplianceState: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../services/ComplianceService");

const lastTable = () => mockTableProps[mockTableProps.length - 1];
const column = (id: string) =>
    (lastTable().columnDefinitions as any[]).find((definition) => definition.id === id);

const SUPERSEDED_TEXT = "Not completed: a newer evaluation of the asset started";
const RULE_TEXT =
    "Not started: a newer evaluation of the asset began while the rule waited for its workflow";

/** The row the API writes for an evaluation a newer one replaced while its rule waited. */
const CLOSED: EvaluationRecord = {
    evaluationId: "e2",
    databaseId: "db1",
    assetId: "asset-1",
    schemaName: "s1",
    result: "error",
    verdict: "error",
    status: "error",
    closedReason: "superseded",
    errorMessage: SUPERSEDED_TEXT,
    hasRuleErrors: false,
    violations: [],
    evaluatedAt: "2026-11-04T10:00:00Z",
    ruleResults: JSON.stringify([
        { ruleName: "named", ruleType: "assetAttributes", passed: true, status: "evaluated" },
        {
            ruleName: "residual-bound",
            ruleType: "pipeline",
            passed: false,
            status: "error",
            message: RULE_TEXT,
        },
    ]),
    pipelineExecutions: [{ ruleName: "residual-bound", status: "completed" }],
};

beforeEach(() => {
    jest.clearAllMocks();
    mockTableProps.length = 0;
    service().fetchComplianceState.mockResolvedValue([
        true,
        { databaseId: "db1", assetId: "asset-1", complianceState: "pending_evaluation" },
    ]);
    service().fetchEvaluationHistory.mockResolvedValue([
        true,
        { evaluations: [], nextToken: undefined },
    ]);
});

describe("evaluationClosedMessage", () => {
    it("is the recorded message of a closed evaluation, and null otherwise", () => {
        expect(evaluationClosedMessage(CLOSED)).toBe(SUPERSEDED_TEXT);
        expect(evaluationClosedMessage({ ...CLOSED, errorMessage: undefined })).toBe(
            "Not completed"
        );
        expect(
            evaluationClosedMessage({ ...CLOSED, closedReason: undefined, verdict: "compliant" })
        ).toBeNull();
    });
});

describe("the asset Compliance tab shows a closed evaluation", () => {
    it("reads Not completed with the reason, and no verdict or rule-error label", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const result = render(<>{column("result").cell(CLOSED)}</>);
        expect(result.container.textContent).toBe("Not completed");
        const violations = render(<>{column("violations").cell(CLOSED)}</>);
        expect(violations.container.textContent).toBe(SUPERSEDED_TEXT);
        expect(violations.container.textContent).not.toContain("Not evaluated");
    });

    it("still shows a verdict and rule errors for an evaluation that reached a verdict", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const reached = {
            ...CLOSED,
            closedReason: undefined,
            errorMessage: undefined,
            verdict: "compliant" as const,
            status: "completed" as const,
            hasRuleErrors: true,
            errorRules: ["residual-bound"],
        };
        const result = render(<>{column("result").cell(reached)}</>);
        expect(result.container.textContent).toContain("Compliant");
        expect(result.container.textContent).toContain("Rule errors");
    });
});

describe("the evaluation history dialog shows a closed evaluation", () => {
    it("reads Not completed with the reason in the result column", async () => {
        render(
            <EvaluationHistoryModal
                visible
                databaseId="db1"
                assetId="asset-1"
                assetLabel="asset-1"
                onDismiss={() => undefined}
            />
        );
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const verdict = render(<>{column("verdict").cell(CLOSED)}</>);
        expect(verdict.container.textContent).toContain("Not completed");
        expect(verdict.container.textContent).toContain(SUPERSEDED_TEXT);
        expect(verdict.container.textContent).not.toMatch(/Compliant|Rule errors|Error/);
        // The rule keeps its own reason among the rule results.
        const rules = render(<>{column("rules").cell(CLOSED)}</>);
        expect(rules.container.textContent).toContain(RULE_TEXT);
    });
});
