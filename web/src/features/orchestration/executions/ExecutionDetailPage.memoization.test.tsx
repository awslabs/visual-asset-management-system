/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The detail page's table inputs must survive a re-render unchanged.
 *
 * The page polls while a run is in flight, and `DataTable` feeds `columns` and `rows` straight into
 * `useReactTable`, which rebuilds its column model and re-renders every visible cell whenever either
 * array identity changes. A real asset carries thousands of files and hundreds of metadata entries per
 * record, so rebuilding the arrays on every render costs on the order of 10^5 fresh row objects per
 * tick across three tables. The props are therefore asserted by REFERENCE, not by content: equal
 * content with a new identity is exactly the defect.
 *
 * `DataTable` is mocked here rather than in the main suite, which asserts on real rendered rows.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionDetailPage from "./ExecutionDetailPage";

jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

jest.mock("../api/queries", () => ({
    useExecutionDetails: jest.fn(),
    useExecutionDetailMetadata: jest.fn(),
}));

jest.mock("../api/executions", () => ({ getExecutionLogs: jest.fn() }));

jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));

/** Every `columns`/`rows` pair the page handed a table, in render order. */
const tableProps: Array<{ columns: any; rows: any }> = [];

jest.mock("../components/DataTable", () => ({
    __esModule: true,
    default: ({ columns, rows }: any) => {
        tableProps.push({ columns, rows });
        return <div data-testid="data-table" />;
    },
}));

/** Detail response with rows in every table both the Inputs and Outputs tabs render. */
const EXECUTION = {
    workflowExecutionId: "e-memo",
    workflowId: "wf-1",
    workflowDatabaseId: "db-1",
    executionStatus: "RUNNING",
    inputFiles: [{ databaseId: "db-1", assetId: "a1", inputAssetFileKey: "/a1/f.glb" }],
    inputMetadata: [{ pipelineId: "p1", assetId: "a1", filePath: "/f.glb", metadata: { k: "v" } }],
    inputDatabaseMetadata: [{ pipelineId: "p1", databaseId: "src-db", metadata: { p: "x" } }],
    outputs: {
        files: [
            {
                relativeFilePath: "/out.glb",
                pipelineId: "p1",
                databaseId: "db-1",
                assetId: "a1",
            },
        ],
        metadata: [
            {
                targetFilePath: "/out.glb",
                metadataKey: "previewGenerated",
                metadataValue: "true",
                pipelineId: "p1",
            },
        ],
    },
};

/**
 * Re-renders the page without changing the response, the way a poll that found the run unchanged does:
 * TanStack's structural sharing keeps the previous data identity, so only the render repeats.
 */
const Harness: React.FC = () => {
    const [tick, setTick] = React.useState(0);
    return (
        <>
            <button onClick={() => setTick(tick + 1)}>poll</button>
            <ExecutionDetailPage executionId="e-memo" />
        </>
    );
};

describe("ExecutionDetailPage table inputs", () => {
    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        jest.clearAllMocks();
        tableProps.length = 0;
        const { useExecutionDetails, useExecutionDetailMetadata } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        // The SAME object on every call — the identity a poll of an unchanged run preserves.
        useExecutionDetails.mockReturnValue({ data: EXECUTION, isLoading: false, error: null });
        useExecutionDetailMetadata.mockReturnValue({
            data: undefined,
            isLoading: false,
            isError: false,
            error: null,
            hasNextPage: false,
            isFetchingNextPage: false,
            fetchNextPage: jest.fn(),
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
    });

    it("hands every table the same columns and rows across a re-render", async () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <Harness />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Input files + database metadata + asset/file metadata.
        expect(screen.getAllByTestId("data-table")).toHaveLength(3);
        const first = tableProps.splice(0, tableProps.length);
        expect(first).toHaveLength(3);

        await userEvent.click(screen.getByRole("button", { name: "poll" }));

        const second = tableProps.splice(0, tableProps.length);
        expect(second).toHaveLength(3);
        second.forEach((props, i) => {
            expect(props.columns).toBe(first[i].columns);
            expect(props.rows).toBe(first[i].rows);
        });
    });

    it("hands the output tables the same columns across a re-render", async () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <Harness />
                </MemoryRouter>
            </QueryClientProvider>
        );
        tableProps.length = 0;

        await userEvent.click(screen.getByRole("tab", { name: /Outputs/i }));
        const first = tableProps.splice(0, tableProps.length);
        expect(first.length).toBeGreaterThan(0);

        await userEvent.click(screen.getByRole("button", { name: "poll" }));
        const second = tableProps.splice(0, tableProps.length);
        expect(second).toHaveLength(first.length);
        second.forEach((props, i) => expect(props.columns).toBe(first[i].columns));
    });
});
