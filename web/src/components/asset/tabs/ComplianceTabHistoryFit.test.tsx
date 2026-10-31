/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset Compliance tab's Evaluation History fits the tab at 1280 px with the navigation open.
 * It was 956 px wide in an 873 px container and cut off the Execution column, because its cells
 * did not wrap: the violations of an evaluation were one comma-joined line. The table now wraps
 * (`wrapLines`), each violation is a line of its own that wraps between words, and the rules that
 * were not evaluated are error text rather than a status indicator, whose text breaks between any
 * two letters. jsdom does no layout: this pins what the browser lays out; the widths at 1280 /
 * 1440 / 1920 px were measured in a browser (the report's Round 9 section).
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { ComplianceTab } from "./ComplianceTab";

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

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../../../services/ComplianceService", () => ({
    ...jest.requireActual("../../../services/ComplianceService"),
    fetchComplianceState: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../../services/ComplianceService");

const lastTable = () => mockTableProps[mockTableProps.length - 1];
const column = (id: string) =>
    (lastTable().columnDefinitions as any[]).find((definition) => definition.id === id);

beforeEach(() => {
    jest.clearAllMocks();
    mockTableProps.length = 0;
    service().fetchComplianceState.mockResolvedValue([
        true,
        { databaseId: "db1", assetId: "asset-1", complianceState: "quarantined" },
    ]);
    service().fetchEvaluationHistory.mockResolvedValue([
        true,
        { evaluations: [], nextToken: undefined },
    ]);
});

describe("the asset Compliance tab's Evaluation History fits at 1280 px", () => {
    it("wraps its columns", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        expect(lastTable().wrapLines).toBe(true);
        expect((lastTable().columnDefinitions as any[]).map((c) => c.id)).toEqual([
            "evaluatedAt",
            "schemaName",
            "result",
            "violations",
            "executionId",
        ]);
    });

    it("lists each violation on a line of its own and the unevaluated rules as text", async () => {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const { container } = render(
            <>
                {column("violations").cell({
                    violations: ["has-parent: no parent", "tagged: missing required tags fin-ok"],
                    errorRules: ["residual-bound"],
                    hasRuleErrors: true,
                })}
            </>
        );
        const texts = [...container.querySelectorAll("span")].map((s) => s.textContent);
        expect(texts).toContain("has-parent: no parent");
        expect(texts).toContain("tagged: missing required tags fin-ok");
        expect(container.textContent).toContain("Not evaluated: residual-bound");
        // No status indicator: its text breaks between any two letters in a wrapping table.
        expect(container.querySelector("[class*='status-indicator']")).toBeNull();
    });
});
