/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Compliance Status box lays its fields side by side (D187): Current State, Recorded State
 * (archived only), Assigned Schema and Last Evaluated are the columns of one text-grid column
 * layout, as the asset details spread their fields, each with its own small line beneath. The
 * longer notes sit in a full-width row under the columns, so a long note does not stretch one.
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import Synonyms from "../../../synonyms";
import { ComplianceTab } from "./ComplianceTab";

const mockColumnLayoutProps: any[] = [];
jest.mock("@cloudscape-design/components/column-layout", () => {
    const StandIn = (props: any) => {
        mockColumnLayoutProps.push(props);
        return <div data-testid={props["data-testid"]}>{props.children}</div>;
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
    fetchEvaluationHistory: jest.fn(async () => [true, { evaluations: [] }]),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../../services/ComplianceService");

const statusLayout = () => {
    const props = mockColumnLayoutProps.filter(
        (candidate) => candidate["data-testid"] === "compliance-status-fields"
    );
    expect(props.length).toBeGreaterThan(0);
    return props[props.length - 1];
};

const renderWith = async (state: Record<string, any>) => {
    service().fetchComplianceState.mockResolvedValue([
        true,
        { databaseId: "db1", assetId: "asset-1", ...state },
    ]);
    render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
    await screen.findByText("Current State");
};

beforeEach(() => {
    mockColumnLayoutProps.length = 0;
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("the Compliance Status box", () => {
    it("puts the state, schema and last evaluation side by side in a text grid", async () => {
        await renderWith({
            complianceState: "compliant",
            schemaName: "std",
            schemaSource: "database",
            lastEvaluatedAt: "2026-10-01T00:00:00Z",
        });
        const layout = statusLayout();
        expect(layout.variant).toBe("text-grid");
        expect(layout.columns).toBe(3);

        const fields = screen.getByTestId("compliance-status-fields");
        const labels = Array.from(fields.children).map(
            (field) => field.firstElementChild?.textContent
        );
        expect(labels).toEqual(["Current State", "Assigned Schema", "Last Evaluated"]);
        // The schema's source stays on its own small line under the schema, in the same column.
        const schemaField = within(fields).getByText("Assigned Schema").parentElement!;
        expect(schemaField.textContent).toContain("std");
        expect(within(schemaField).getByText(/database/i)).toBeInTheDocument();
        expect(screen.queryByTestId("compliance-status-notes")).not.toBeInTheDocument();
    });

    it("adds the recorded state as a fourth column for an archived asset", async () => {
        await renderWith({
            complianceState: "archived",
            recordedComplianceState: "quarantined",
            schemaName: "std",
            lastEvaluatedAt: "2026-10-01T00:00:00Z",
        });
        expect(statusLayout().columns).toBe(4);
        const fields = screen.getByTestId("compliance-status-fields");
        expect(within(fields).getByText("Recorded State")).toBeInTheDocument();
    });

    it("uses one column when the state is all the record holds", async () => {
        await renderWith({ complianceState: "pending_evaluation" });
        expect(statusLayout().columns).toBe(1);
    });

    it("puts the longer notes in a full-width row under the columns", async () => {
        await renderWith({
            complianceState: "quarantined",
            lastEvaluationStatus: "error",
            schemaName: "strict",
            lastEvaluatedAt: "2026-10-01T00:00:00Z",
            awaitingReevaluationSince: "2026-10-02T00:00:00Z",
            awaitingReevaluationSchema: "strict",
        });
        const fields = screen.getByTestId("compliance-status-fields");
        const notes = screen.getByTestId("compliance-status-notes");
        expect(fields.contains(notes)).toBe(false);
        expect(notes.textContent).toContain(
            `The last evaluation produced no verdict, so the state is the one the ${Synonyms.asset} held before it.`
        );
        expect(notes.textContent).toContain("Awaiting re-evaluation under strict");
        expect(fields.textContent).not.toContain("produced no verdict");
        expect(fields.textContent).not.toContain("Awaiting re-evaluation");
    });
});
