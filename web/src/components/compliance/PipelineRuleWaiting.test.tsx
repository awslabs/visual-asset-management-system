/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A pipeline rule whose workflow's concurrency lock another execution holds waits for that
 * execution to end (D113). Readers see it waiting: the asset Compliance tab's history row and the
 * database overview's history dialog say "Waiting for a running execution of its workflow" with the
 * rule's name, and Evaluate Now says how many rules wait. Nothing names the holding execution: it
 * may be another user's run that the reader may not open. jsdom does no layout: the 1440 and 390 px
 * checks are for the live round.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import EvaluationHistoryModal from "./EvaluationHistoryModal";
import { ComplianceTab, evaluationStartedMessage } from "../asset/tabs/ComplianceTab";
import {
    EvaluationRecord,
    PIPELINE_RULE_WAITING_MESSAGE,
    schemaSaveWarnings,
    waitingRuleNames,
} from "../../services/ComplianceService";

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

/** A pending evaluation: one rule launched, one waiting for a running execution of its workflow. */
const WAITING: EvaluationRecord = {
    evaluationId: "e2",
    databaseId: "db1",
    assetId: "asset-1",
    schemaName: "s1",
    result: "pending_pipeline",
    verdict: "pending_pipeline",
    status: "pending_pipeline",
    evaluatedAt: "2026-11-04T10:00:00Z",
    ruleResults: JSON.stringify([
        { ruleName: "named", ruleType: "assetAttributes", passed: true, status: "evaluated" },
    ]),
    pipelineExecutions: [
        {
            ruleName: "residual-bound",
            executionId: "e1000000000000000000000000000001",
            status: "pending",
        },
        { ruleName: "residual-tight", status: "waiting" },
    ],
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

describe("waitingRuleNames", () => {
    it("names the waiting and launching rules of a pending evaluation", () => {
        expect(waitingRuleNames(WAITING)).toEqual(["residual-tight"]);
        expect(
            waitingRuleNames({
                ...WAITING,
                pipelineExecutions: [{ ruleName: "residual-tight", status: "launching" }],
            })
        ).toEqual(["residual-tight"]);
    });

    it("names none once the evaluation completed, or when it lists no executions", () => {
        expect(
            waitingRuleNames({ ...WAITING, status: "completed", verdict: "quarantined" })
        ).toEqual([]);
        expect(waitingRuleNames({ ...WAITING, pipelineExecutions: undefined })).toEqual([]);
    });
});

describe("the asset Compliance tab shows a waiting rule", () => {
    it("labels the history row Waiting and names the rule with the fixed text", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const result = render(<>{column("result").cell(WAITING)}</>);
        expect(result.container.textContent).toContain("Pipeline Running");
        expect(result.container.textContent).toContain("Waiting");
        const violations = render(<>{column("violations").cell(WAITING)}</>);
        expect(violations.container.textContent).toContain(
            `${PIPELINE_RULE_WAITING_MESSAGE}: residual-tight`
        );
        // Nothing about the execution that holds the workflow.
        expect(violations.container.textContent).not.toMatch(/e1000|held by|user/i);
    });

    it("shows no waiting text for a completed evaluation", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const done = { ...WAITING, status: "completed" as const, verdict: "compliant" as const };
        const { container } = render(<>{column("violations").cell(done)}</>);
        expect(container.textContent).not.toContain(PIPELINE_RULE_WAITING_MESSAGE);
    });

    it("says how many rules wait when Evaluate Now starts an evaluation", () => {
        const base = {
            message: "Evaluation completed",
            evaluationId: "e2",
            schemaName: "s1",
            verdict: "pending_pipeline" as const,
            complianceState: "pending_evaluation" as const,
            ruleResults: [],
            pipelineRulesPending: 2,
        };
        expect(evaluationStartedMessage({ ...base, pipelineRulesWaiting: 0 })).toBe(
            "Evaluation triggered successfully"
        );
        expect(evaluationStartedMessage({ ...base, pipelineRulesWaiting: 1 })).toBe(
            "Evaluation triggered successfully. 1 pipeline rule waits for a running execution of the workflow and starts when it ends."
        );
        expect(evaluationStartedMessage({ ...base, pipelineRulesWaiting: 2 })).toContain(
            "2 pipeline rules wait"
        );
        expect(evaluationStartedMessage(base)).toBe("Evaluation triggered successfully");
    });
});

describe("the evaluation history dialog shows a waiting rule", () => {
    it("lists the waiting rule beside the results with a Waiting label and the fixed text", async () => {
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
        const { container } = render(<>{column("rules").cell(WAITING)}</>);
        const line = container.querySelector("[data-rule-waiting]") as HTMLElement;
        expect(line).not.toBeNull();
        expect(line.textContent).toContain("residual-tight");
        expect(line.querySelector("[data-status-label]")?.textContent?.trim()).toBe("Waiting");
        expect(line.textContent).toContain(PIPELINE_RULE_WAITING_MESSAGE);
        expect(container.querySelectorAll("[data-rule-result]")).toHaveLength(1);
        const verdict = render(<>{column("verdict").cell(WAITING)}</>);
        expect(verdict.container.textContent).toContain("Waiting");
    });
});

describe("schemaSaveWarnings", () => {
    it("reads the warnings of a save's answer", () => {
        const warning = { code: "pipelineRulesShareWorkflow", message: "Pipeline rules a, b ..." };
        expect(
            schemaSaveWarnings({ message: "ok", schemaName: "s1", warnings: [warning] })
        ).toEqual([warning]);
        expect(schemaSaveWarnings({ message: "ok", schemaName: "s1" })).toEqual([]);
        expect(schemaSaveWarnings("refused")).toEqual([]);
        expect(schemaSaveWarnings(undefined)).toEqual([]);
    });
});
