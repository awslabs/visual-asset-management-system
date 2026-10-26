/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Actions column of the Quarantine, Cascade Approvals and Compliance Schemas tables has a fixed
 * width wide enough for its two buttons side by side. Without one the table squeezed the column until the Release
 * and Exception labels wrapped one letter per line. The table is replaced here by a stand-in that
 * records the column definitions each page hands it, so the pin is on the definition itself.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import ComplianceQuarantine, { QUARANTINE_ACTIONS_COLUMN_WIDTH } from "./ComplianceQuarantine";
import ComplianceCascades, { CASCADE_ACTIONS_COLUMN_WIDTH } from "./ComplianceCascades";
import ComplianceSchemas, { SCHEMA_ACTIONS_COLUMN_WIDTH } from "./ComplianceSchemas";

const mockTableProps: any[] = [];
jest.mock("@cloudscape-design/components/table", () => {
    const StandIn = (props: any) => {
        mockTableProps.push(props);
        return null;
    };
    return { __esModule: true, default: StandIn };
});

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
}));

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
    loader: { config: jest.fn() },
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const actionsColumn = () => {
    const props = mockTableProps[mockTableProps.length - 1];
    const columns = props.columnDefinitions as any[];
    const actions = columns.filter((column) => column.id === "actions");
    expect(actions).toHaveLength(1);
    return actions[0];
};

describe("compliance table action columns", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockTableProps.length = 0;
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [], nextToken: undefined },
        ]);
        service().fetchCascades.mockResolvedValue([true, { cascades: [] }]);
        service().fetchComplianceSchemas.mockResolvedValue([true, []]);
    });

    it("gives the Quarantine actions column a fixed width for both buttons", async () => {
        render(<ComplianceQuarantine />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const column = actionsColumn();
        expect(column.width).toBe(QUARANTINE_ACTIONS_COLUMN_WIDTH);
        expect(column.minWidth).toBe(QUARANTINE_ACTIONS_COLUMN_WIDTH);
        // Two normal buttons ("Release", "Exception") and the gap between them need about 220 px.
        expect(QUARANTINE_ACTIONS_COLUMN_WIDTH).toBeGreaterThanOrEqual(220);
    });

    it("gives the Cascade Approvals actions column a fixed width for both buttons", async () => {
        render(<ComplianceCascades />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const column = actionsColumn();
        expect(column.width).toBe(CASCADE_ACTIONS_COLUMN_WIDTH);
        expect(column.minWidth).toBe(CASCADE_ACTIONS_COLUMN_WIDTH);
        // "Approve" and "Reject" side by side need about 200 px.
        expect(CASCADE_ACTIONS_COLUMN_WIDTH).toBeGreaterThanOrEqual(200);
    });

    it("gives the Compliance Schemas actions column a fixed width for both buttons", async () => {
        render(<ComplianceSchemas />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const column = actionsColumn();
        expect(column.width).toBe(SCHEMA_ACTIONS_COLUMN_WIDTH);
        expect(column.minWidth).toBe(SCHEMA_ACTIONS_COLUMN_WIDTH);
        // Two link buttons ("Edit", "Sweep") and the gap between them need about 160 px.
        expect(SCHEMA_ACTIONS_COLUMN_WIDTH).toBeGreaterThanOrEqual(160);
    });
});
