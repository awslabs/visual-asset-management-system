/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The board must give the table a STABLE per-row identity.
 *
 * The board polls every 5s while any run is non-terminal and re-sorts non-terminal-first on every
 * response, so the row under an OPEN action menu can change position mid-interaction. Without a row
 * id, react-table keys rows by position and React reuses the positional subtree — including the
 * uncontrolled Radix menu inside it — so the still-open menu's Abort / Permanent-delete closures come
 * to belong to whichever execution landed on that index.
 *
 * This asserts the CONTRACT the board supplies rather than driving a menu through a rerender: that
 * per-row state genuinely follows a supplied `getRowId` across a reorder is proved once, at the
 * primitive, in DataTable.test.tsx ("keeps per-row state bound to the same row when getRowId is
 * supplied and rows reorder"). Re-proving it here would be a test of Radix's behaviour under jsdom.
 *
 * `DataTable` is therefore mocked to capture its props, as ExecutionsBoard.memoization.test.tsx does
 * for the same reason; the main suite renders the real table.
 */

import React from "react";
import { cleanup, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionsBoard from "./ExecutionsBoard";
import type { Execution } from "../types";

jest.mock("../api/queries", () => ({
    useExecutions: jest.fn(),
    useExecutionActions: jest.fn(() => ({
        abortExecution: { mutateAsync: jest.fn() },
        rerunExecution: { mutateAsync: jest.fn() },
        permanentDeleteExecution: { mutateAsync: jest.fn() },
    })),
    useExecutionDetails: jest.fn(),
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useDatabases: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined })),
}));

jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(() => ({ loading: false, can: () => true })),
}));

/** The props of every DataTable the board rendered, newest last. */
const tableProps: any[] = [];

jest.mock("../components/DataTable", () => ({
    __esModule: true,
    default: (props: any) => {
        tableProps.push(props);
        return <div data-testid="data-table" />;
    },
}));

const row = (id: string, status: string, start: string): Execution =>
    ({
        workflowExecutionId: id,
        workflowId: "wf-1",
        workflowDatabaseId: "db-1",
        executionStatus: status,
        triggeredByUserId: "user-1",
        triggerType: "manual",
        executionStartDate: start,
    } as Execution);

const ROWS = [
    row("exec-watched", "RUNNING", "2026-08-01T08:00:00Z"),
    row("exec-done", "SUCCEEDED", "2026-08-01T09:00:00Z"),
];

const renderBoard = (rows: Execution[], hasNextPage = false) => {
    const { useExecutions } = require("../api/queries");
    useExecutions.mockReturnValue({
        data: { pages: [{ Items: rows }], pageParams: [] },
        isLoading: false,
        error: null,
        fetchNextPage: jest.fn(),
        hasNextPage,
        isFetchingNextPage: false,
        refetch: jest.fn(),
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter>
                <ExecutionsBoard scope={{ kind: "global" }} />
            </MemoryRouter>
        </QueryClientProvider>
    );
    return tableProps[tableProps.length - 1];
};

describe("ExecutionsBoard row identity", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        tableProps.length = 0;
    });

    it("supplies a row id at all", () => {
        // Omitting it is the defect: react-table then falls back to the array index.
        expect(renderBoard(ROWS).getRowId).toBeInstanceOf(Function);
    });

    it("identifies a row by its execution id, not its position", () => {
        const { getRowId } = renderBoard(ROWS);

        expect(getRowId(ROWS[0], 0)).toBe("exec-watched");
        expect(getRowId(ROWS[1], 1)).toBe("exec-done");
    });

    it("gives a row the same id wherever the sort puts it", () => {
        // The point of the fix: the id is a property of the execution, so a re-sort cannot move it.
        const { getRowId } = renderBoard(ROWS);

        expect(getRowId(ROWS[0], 0)).toBe(getRowId(ROWS[0], 1));
        // Control: distinct executions must not collide onto one id, which would be as bad as an index.
        expect(getRowId(ROWS[0], 0)).not.toBe(getRowId(ROWS[1], 0));
    });

    it("qualifies the sort scope only while pages remain unloaded", () => {
        expect(renderBoard(ROWS, true).sortScopeNote).toEqual(
            expect.stringContaining("2 executions")
        );

        // Control: with every page loaded a sort IS global, so there is nothing to qualify.
        cleanup();
        tableProps.length = 0;
        expect(renderBoard(ROWS, false).sortScopeNote).toBeUndefined();
    });

    it("names the table", () => {
        expect(renderBoard(ROWS).ariaLabel).toBe("Executions");
    });
});
