/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An evaluation whose outcome a later release or exception grant outranked says so (D159). The API
 * records the verdict but leaves the asset's state as the decision left it, and marks the row
 * `notApplied` (`released` or `exceptionGranted`) with a `notAppliedMessage`. The asset Compliance
 * tab's history row and the history dialog show the verdict with "Not applied" and the message in
 * plain words; an evaluation that was applied shows neither.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import EvaluationHistoryModal from "./EvaluationHistoryModal";
import { ComplianceTab } from "../asset/tabs/ComplianceTab";
import { EvaluationRecord, evaluationNotAppliedMessage } from "../../services/ComplianceService";

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

const RELEASED_TEXT = "Not applied: the asset was released after this evaluation began";
const GRANTED_TEXT = "Not applied: an exception was granted after this evaluation began";

/** The row the API leaves for a quarantining evaluation a release made while it ran outranked. */
const NOT_APPLIED: EvaluationRecord = {
    evaluationId: "e3",
    databaseId: "db1",
    assetId: "asset-1",
    schemaName: "s1",
    result: "quarantined",
    verdict: "quarantined",
    status: "completed",
    hasRuleErrors: false,
    violations: ["residual 3 exceeds 1"],
    evaluatedAt: "2026-11-06T10:00:00Z",
    notApplied: "released",
    notAppliedMessage: RELEASED_TEXT,
};

const APPLIED: EvaluationRecord = {
    ...NOT_APPLIED,
    notApplied: undefined,
    notAppliedMessage: undefined,
};

beforeEach(() => {
    jest.clearAllMocks();
    mockTableProps.length = 0;
    service().fetchComplianceState.mockResolvedValue([
        true,
        { databaseId: "db1", assetId: "asset-1", complianceState: "compliant" },
    ]);
    service().fetchEvaluationHistory.mockResolvedValue([
        true,
        { evaluations: [], nextToken: undefined },
    ]);
});

describe("evaluationNotAppliedMessage", () => {
    it("is the recorded message of an outranked evaluation, and null otherwise", () => {
        expect(evaluationNotAppliedMessage(NOT_APPLIED)).toBe(RELEASED_TEXT);
        expect(
            evaluationNotAppliedMessage({
                ...NOT_APPLIED,
                notApplied: "exceptionGranted",
                notAppliedMessage: GRANTED_TEXT,
            })
        ).toBe(GRANTED_TEXT);
        expect(
            evaluationNotAppliedMessage({ ...NOT_APPLIED, notAppliedMessage: undefined })
        ).toMatch(/^Not applied: /);
        expect(evaluationNotAppliedMessage(APPLIED)).toBeNull();
    });
});

describe("the asset Compliance tab shows an outranked evaluation", () => {
    it("reads the verdict with Not applied and the reason in words", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const result = render(<>{column("result").cell(NOT_APPLIED)}</>);
        expect(result.container.textContent).toContain("Quarantined");
        expect(result.container.textContent).toContain("Not applied");
        const violations = render(<>{column("violations").cell(NOT_APPLIED)}</>);
        expect(violations.container.textContent).toContain(RELEASED_TEXT);
        expect(violations.container.textContent).toContain("residual 3 exceeds 1");
    });

    it("shows neither for an evaluation that was applied", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const result = render(<>{column("result").cell(APPLIED)}</>);
        expect(result.container.textContent).not.toContain("Not applied");
        const violations = render(<>{column("violations").cell(APPLIED)}</>);
        expect(violations.container.textContent).not.toContain("Not applied");
    });
});

describe("the evaluation history dialog shows an outranked evaluation", () => {
    it("reads the verdict with Not applied and the reason in the result column", async () => {
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
        const verdict = render(
            <>
                {column("verdict").cell({
                    ...NOT_APPLIED,
                    notApplied: "exceptionGranted",
                    notAppliedMessage: GRANTED_TEXT,
                })}
            </>
        );
        expect(verdict.container.textContent).toContain("Quarantined");
        expect(verdict.container.textContent).toContain("Not applied");
        expect(verdict.container.textContent).toContain(GRANTED_TEXT);
        const applied = render(<>{column("verdict").cell(APPLIED)}</>);
        expect(applied.container.textContent).not.toContain("Not applied");
    });
});
