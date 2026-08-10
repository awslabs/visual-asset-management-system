/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The board's column definitions must survive a re-render unchanged.
 *
 * `DataTable` feeds `columns` into `useReactTable`, which rebuilds its column model and unmounts every
 * visible cell whenever that array's identity changes. The board polls every 5 seconds while any run is
 * non-terminal — exactly when an operator is watching — so a fresh array per render closes the open row
 * action menu mid-interaction and remounts up to 50 rows x ~11 columns each tick. The prop is therefore
 * asserted by REFERENCE: equal content with a new identity is the defect.
 *
 * The two unstable inputs are reproduced the way the real ones behave: `can` is a fresh closure on every
 * render of `useAllowedRoutes`, and react-query hands back a new mutation wrapper object each render
 * while its `mutateAsync` stays stable.
 *
 * `DataTable` is mocked here rather than in the main suite, which asserts on real rendered rows.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionsBoard from "./ExecutionsBoard";
import { ToastProvider } from "../components/ToastProvider";

jest.mock("../api/queries", () => ({
    useExecutions: jest.fn(),
    useExecutionActions: jest.fn(),
    useExecutionDetails: jest.fn(),
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useDatabases: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined })),
}));

jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));

/** Every `columns` array the board handed the table, in render order. */
const tableColumns: any[] = [];

jest.mock("../components/DataTable", () => ({
    __esModule: true,
    default: ({ columns }: any) => {
        tableColumns.push(columns);
        return <div data-testid="data-table" />;
    },
}));

const ROWS = [
    {
        workflowExecutionId: "E-running",
        workflowId: "wf-1",
        workflowDatabaseId: "db-1",
        executionStatus: "RUNNING",
        executionStartDate: "2026-08-01T10:00:00Z",
    },
];

/** Re-renders the board without changing the response, the way a poll of an unchanged list does. */
const Harness: React.FC = () => {
    const [tick, setTick] = React.useState(0);
    return (
        <>
            <button onClick={() => setTick(tick + 1)}>poll</button>
            <ExecutionsBoard scope={{ kind: "global" }} />
        </>
    );
};

describe("ExecutionsBoard column identity", () => {
    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        jest.clearAllMocks();
        tableColumns.length = 0;

        const { useExecutions, useExecutionActions } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        // A fresh closure per render, as the real hook returns.
        useAllowedRoutes.mockImplementation(() => ({
            loading: false,
            can: (_method: string, _path: string) => true,
        }));

        // A new wrapper object per render with a stable mutateAsync, as react-query returns.
        const rerun = jest.fn().mockResolvedValue({ executionId: "E-new" });
        const abort = jest.fn();
        const del = jest.fn();
        useExecutionActions.mockImplementation(() => ({
            abortExecution: { mutateAsync: abort },
            rerunExecution: { mutateAsync: rerun },
            permanentDeleteExecution: { mutateAsync: del },
        }));

        // The same page object on every call — the identity a poll of an unchanged list preserves.
        const data = { pages: [{ Items: ROWS }], pageParams: [] };
        useExecutions.mockReturnValue({
            data,
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
            refetch: jest.fn(),
            isFetching: false,
        });
    });

    it("hands the table the same columns across a re-render", async () => {
        render(
            <QueryClientProvider client={queryClient}>
                <ToastProvider>
                    <MemoryRouter>
                        <Harness />
                    </MemoryRouter>
                </ToastProvider>
            </QueryClientProvider>
        );

        expect(screen.getByTestId("data-table")).toBeInTheDocument();
        const first = tableColumns[tableColumns.length - 1];
        tableColumns.length = 0;

        await userEvent.click(screen.getByRole("button", { name: "poll" }));

        expect(tableColumns.length).toBeGreaterThan(0);
        tableColumns.forEach((columns) => expect(columns).toBe(first));
    });

    it("keeps the row actions wired to the current permission result", async () => {
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        // Reading `can` through a ref must not pin the FIRST render's answer: a board rendered while
        // the routes were still loading would otherwise hide every gated action for good.
        let allowed = false;
        useAllowedRoutes.mockImplementation(() => ({
            loading: false,
            can: () => allowed,
        }));

        render(
            <QueryClientProvider client={queryClient}>
                <ToastProvider>
                    <MemoryRouter>
                        <Harness />
                    </MemoryRouter>
                </ToastProvider>
            </QueryClientProvider>
        );

        const columns: any[] = tableColumns[tableColumns.length - 1];
        const actions = columns.find((c) => c.header === "Actions");
        const cellOf = () => actions.cell({ row: { original: ROWS[0] } });
        expect(cellOf().props.children.props.can("POST", "/anything")).toBe(false);

        allowed = true;
        await userEvent.click(screen.getByRole("button", { name: "poll" }));
        expect(cellOf().props.children.props.can("POST", "/anything")).toBe(true);
    });
});
