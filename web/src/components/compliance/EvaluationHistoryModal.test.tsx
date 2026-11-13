/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The evaluation history dialog of the database overview keeps long rule messages within the
 * dialog: its table wraps (`wrapLines`), and each rule result is the rule name, a one-line status
 * label and the message beneath as text that wraps between words. The whole result used to be one
 * status indicator on one line (the table did not wrap), so a long message widened the table past
 * the dialog, 1364 px in a 1120 px dialog at 1280 px; wrapped, a status indicator's text breaks
 * between any two letters. jsdom does no layout: this pins what the browser lays out.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import EvaluationHistoryModal from "./EvaluationHistoryModal";

const mockTableProps: any[] = [];
jest.mock("@cloudscape-design/components/table", () => {
    const StandIn = (props: any) => {
        mockTableProps.push(props);
        return null;
    };
    return { __esModule: true, default: StandIn };
});

jest.mock("../../services/ComplianceService", () => ({
    ...jest.requireActual("../../services/ComplianceService"),
    fetchEvaluationHistory: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../services/ComplianceService");

const lastTable = () => mockTableProps[mockTableProps.length - 1];
const column = (id: string) =>
    (lastTable().columnDefinitions as any[]).find((definition) => definition.id === id);

const MESSAGE =
    "glb-present: no current file matches ^models/[a-z0-9_-]+\\.glb$ (checked 12 files)";

beforeEach(() => {
    jest.clearAllMocks();
    mockTableProps.length = 0;
    service().fetchEvaluationHistory.mockResolvedValue([
        true,
        { evaluations: [], nextToken: undefined },
    ]);
});

describe("the evaluation history dialog keeps long rule messages within it", () => {
    it("wraps its columns", async () => {
        render(
            <EvaluationHistoryModal
                visible
                databaseId="db1"
                assetId="a1"
                assetLabel="a1"
                onDismiss={() => undefined}
            />
        );
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        expect(lastTable().wrapLines).toBe(true);
    });

    it("shows a rule's outcome as a one-line label and its message as wrapping text", async () => {
        render(
            <EvaluationHistoryModal
                visible
                databaseId="db1"
                assetId="a1"
                assetLabel="a1"
                onDismiss={() => undefined}
            />
        );
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const { container } = render(
            <>
                {column("rules").cell({
                    ruleResults: JSON.stringify([
                        {
                            ruleName: "glb-present",
                            ruleType: "fileStructure",
                            passed: false,
                            status: "completed",
                            message: MESSAGE,
                        },
                    ]),
                })}
            </>
        );
        const line = container.querySelector("[data-rule-result]") as HTMLElement;
        expect(line).not.toBeNull();
        expect(line.textContent).toContain("glb-present");
        // The outcome is a one-line status label; the message is not inside a status indicator.
        const label = line.querySelector("[data-status-label]") as HTMLElement;
        expect(label.textContent?.trim()).toBe("Failed");
        const message = [...line.querySelectorAll("*")].find(
            (el) => el.textContent === MESSAGE
        ) as HTMLElement;
        expect(message).toBeDefined();
        expect(message.closest("[data-status-label]")).toBeNull();
        expect(message.closest("[class*='status-indicator']")).toBeNull();
    });

    it("shows a failed execution's reason beneath the rule's generic message", async () => {
        render(
            <EvaluationHistoryModal
                visible
                databaseId="db1"
                assetId="a1"
                assetLabel="a1"
                onDismiss={() => undefined}
            />
        );
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const reason = "ValueError: latin1.obj could not be read as OBJ text";
        const { container } = render(
            <>
                {column("rules").cell({
                    ruleResults: JSON.stringify([
                        {
                            ruleName: "converts",
                            ruleType: "pipeline",
                            passed: false,
                            status: "evaluated",
                            message: "Pipeline execution FAILED",
                            executionId: "e1",
                            executionFailureReason: reason,
                        },
                        {
                            ruleName: "withheld",
                            ruleType: "pipeline",
                            passed: false,
                            status: "evaluated",
                            message: "Pipeline execution FAILED",
                            executionId: "e2",
                        },
                    ]),
                })}
            </>
        );
        const reasons = container.querySelectorAll("[data-execution-failure-reason]");
        expect(reasons).toHaveLength(1);
        expect(reasons[0].textContent).toBe(`Pipeline failure: ${reason}`);
        expect(reasons[0].closest("[data-rule-result]")?.textContent).toContain("converts");
    });
});
