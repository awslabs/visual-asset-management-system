/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
    computeListRefetchInterval,
    computeRefetchInterval,
    qk,
    useExecutions,
    useTemplateMutations,
} from "./queries";
import * as executionService from "./executions";
import * as pipelineService from "./pipelines";

jest.mock("./executions");
jest.mock("./pipelines");

describe("computeRefetchInterval", () => {
    it("polls only while a row is non-terminal", () => {
        expect(computeRefetchInterval([{ executionStatus: "RUNNING" }])).toBe(5000);
        expect(computeRefetchInterval([{ executionStatus: "SUCCEEDED" }])).toBe(false);
        expect(computeRefetchInterval([])).toBe(false);
    });

    it("returns 5000 if any row is NEW", () => {
        expect(computeRefetchInterval([{ executionStatus: "NEW" }])).toBe(5000);
    });

    it("returns 5000 if any row in a mixed set is non-terminal", () => {
        expect(
            computeRefetchInterval([
                { executionStatus: "SUCCEEDED" },
                { executionStatus: "RUNNING" },
                { executionStatus: "FAILED" },
            ])
        ).toBe(5000);
    });

    it("returns false if all rows are terminal", () => {
        expect(
            computeRefetchInterval([
                { executionStatus: "SUCCEEDED" },
                { executionStatus: "FAILED" },
                { executionStatus: "ABORTED" },
            ])
        ).toBe(false);
    });
});

/**
 * The paged lists' cadence. A poll of an infinite query re-reads every loaded page in ONE pass, and
 * each of those requests re-runs the backend's per-row asset resolution and authorization — so a
 * fixed 5s cadence multiplies the server cost by the number of pages the reader has loaded. Spacing
 * the ticks by the page count holds the rate at one page per cadence at any depth.
 */
describe("computeListRefetchInterval", () => {
    it("keeps the base cadence for a single loaded page", () => {
        expect(computeListRefetchInterval([{ executionStatus: "RUNNING" }], 1)).toBe(5000);
    });

    it("spaces the cadence by the number of loaded pages", () => {
        const rows = [{ executionStatus: "RUNNING" }];
        expect(computeListRefetchInterval(rows, 5)).toBe(25000);
        expect(computeListRefetchInterval(rows, 10)).toBe(50000);
    });

    it("still stops entirely once every loaded row is terminal", () => {
        expect(computeListRefetchInterval([{ executionStatus: "SUCCEEDED" }], 10)).toBe(false);
        expect(computeListRefetchInterval([], 10)).toBe(false);
    });

    it("treats a page count of zero as one page rather than polling with no delay", () => {
        expect(computeListRefetchInterval([{ executionStatus: "NEW" }], 0)).toBe(5000);
    });
});

describe("qk (query key factory)", () => {
    it("generates stable keys for pipelines", () => {
        expect(qk.pipelines()).toEqual(["pipelines", null, null]);
        expect(qk.pipelines("db1")).toEqual(["pipelines", "db1", null]);
        expect(qk.pipelines("db1", { includeArchived: true })).toEqual([
            "pipelines",
            "db1",
            { includeArchived: true },
        ]);
    });

    it("generates stable keys for a single pipeline", () => {
        expect(qk.pipeline("db1", "p1")).toEqual(["pipeline", "db1", "p1"]);
    });

    it("generates stable keys for templates", () => {
        expect(qk.templates("db1", "p1")).toEqual(["templates", "db1", "p1"]);
    });

    it("generates stable keys for workflows", () => {
        expect(qk.workflows()).toEqual(["workflows", null, null]);
        expect(qk.workflows("db1")).toEqual(["workflows", "db1", null]);
    });

    it("generates stable keys for a single workflow", () => {
        expect(qk.workflow("db1", "w1")).toEqual(["workflow", "db1", "w1"]);
    });

    it("generates stable keys for triggers", () => {
        expect(qk.triggers("db1", "w1")).toEqual(["triggers", "db1", "w1"]);
    });

    it("generates stable keys for executions (all scopes)", () => {
        expect(qk.executions({ kind: "global" })).toEqual(["executions", { kind: "global" }, null]);
        expect(qk.executions({ kind: "workflow", databaseId: "db1", workflowId: "w1" })).toEqual([
            "executions",
            { kind: "workflow", databaseId: "db1", workflowId: "w1" },
            null,
        ]);
        expect(
            qk.executions(
                { kind: "asset", databaseId: "db1", assetId: "a1" },
                { status: "RUNNING" }
            )
        ).toEqual([
            "executions",
            { kind: "asset", databaseId: "db1", assetId: "a1" },
            { status: "RUNNING" },
        ]);
    });

    it("generates stable keys for a single execution", () => {
        expect(qk.execution("exec1")).toEqual(["execution", "exec1"]);
    });

    it("generates stable keys for allowedRoutes", () => {
        expect(qk.allowedRoutes()).toEqual(["allowedRoutes"]);
    });
});

describe("useExecutions", () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    };

    beforeEach(() => {
        jest.clearAllMocks();
        (executionService.listExecutionsGlobal as jest.Mock).mockResolvedValue([
            true,
            { Items: [] },
        ]);
    });

    it("sends the workflow's database as workflowDatabaseId, the key the global list filters on", async () => {
        const { result } = renderHook(
            () => useExecutions({ kind: "workflow", databaseId: "db1", workflowId: "wf1" }),
            { wrapper }
        );

        await waitFor(() => expect(result.current.isSuccess).toBe(true));

        expect(executionService.listExecutionsGlobal).toHaveBeenCalledWith(
            expect.objectContaining({ workflowId: "wf1", workflowDatabaseId: "db1" })
        );
        const sent = (executionService.listExecutionsGlobal as jest.Mock).mock.calls[0][0];
        expect(sent.databaseId).toBeUndefined();
    });

    /**
     * The poll cadence is read off the CACHED query, the way the observer reads it, so the pacing is
     * asserted on what the hook actually wired rather than on the helper in isolation.
     */
    const listInterval = (qc: QueryClient, pages: any[]) => {
        const query: any = qc.getQueryCache().find({ queryKey: qk.executions({ kind: "global" }) });
        const interval = query.options.refetchInterval;
        return interval({ state: { data: { pages } } });
    };

    it("paces the poll by the number of loaded pages instead of re-reading them all every 5s", async () => {
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const Wrapper = ({ children }: any) => (
            <QueryClientProvider client={qc}>{children}</QueryClientProvider>
        );
        Wrapper.displayName = "QueryWrapper";
        const { result } = renderHook(() => useExecutions({ kind: "global" }), {
            wrapper: Wrapper,
        });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));

        const running = { Items: [{ executionStatus: "RUNNING" }] };
        // One page open: the full cadence.
        expect(listInterval(qc, [running])).toBe(5000);
        // Eight "Load more" clicks: one tick re-reads all nine pages, so it fires nine times less
        // often — the request rate stays one page per cadence.
        expect(listInterval(qc, Array(9).fill(running))).toBe(45000);
        // Every loaded row terminal: no polling at all, at any depth.
        expect(listInterval(qc, Array(9).fill({ Items: [{ executionStatus: "SUCCEEDED" }] }))).toBe(
            false
        );
    });

    it("leaves background polling off, so a hidden tab issues no list requests", async () => {
        // A tick only fetches when the document is visible unless refetchIntervalInBackground opts in.
        // A board left open behind another tab must cost nothing until it is looked at again.
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const Wrapper = ({ children }: any) => (
            <QueryClientProvider client={qc}>{children}</QueryClientProvider>
        );
        Wrapper.displayName = "QueryWrapper";
        const { result } = renderHook(() => useExecutions({ kind: "global" }), {
            wrapper: Wrapper,
        });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));

        const query: any = qc.getQueryCache().find({ queryKey: qk.executions({ kind: "global" }) });
        expect(query.options.refetchIntervalInBackground).toBeFalsy();
    });
});

describe("useTemplateMutations", () => {
    let client: QueryClient;
    let invalidated: any[][];

    const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    beforeEach(() => {
        jest.clearAllMocks();
        client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        invalidated = [];
        jest.spyOn(client, "invalidateQueries").mockImplementation((filters?: any) => {
            invalidated.push(filters?.queryKey);
            return Promise.resolve();
        });
    });

    it.each([
        ["createTemplate", { databaseId: "db1", pipelineId: "p1", body: {} as any }],
        ["updateTemplate", { databaseId: "db1", pipelineId: "p1", templateId: "t1", body: {} }],
        ["deleteTemplate", { databaseId: "db1", pipelineId: "p1", templateId: "t1" }],
    ])("%s invalidates the pipeline detail and list caches", async (name, vars) => {
        (pipelineService as any)[name].mockResolvedValue([true, {}]);
        const { result } = renderHook(() => useTemplateMutations(), { wrapper });

        await (result.current as any)[name].mutateAsync(vars);

        expect(invalidated).toEqual(
            expect.arrayContaining([
                qk.templates("db1", "p1"),
                qk.pipeline("db1", "p1"),
                ["pipelines"],
            ])
        );
    });
});

/**
 * The DETAIL page must auto-advance too.
 *
 * The list views poll every 5s while any row is non-terminal, but the detail query had no
 * refetchInterval — so opening a RUNNING execution to watch it finish showed a frozen status until the
 * page was reloaded. That is the one place someone is most likely to be sitting and waiting, so the
 * omission was the most visible.
 */
describe("useExecutionDetails polling", () => {
    const client = () =>
        new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });

    // Named so the react/display-name lint rule is satisfied (an anonymous arrow component is
    // undebuggable in a React tree).
    const wrapper = (qc: QueryClient) => {
        const Wrapper = ({ children }: any) => (
            <QueryClientProvider client={qc}>{children}</QueryClientProvider>
        );
        Wrapper.displayName = "QueryWrapper";
        return Wrapper;
    };

    beforeEach(() => jest.clearAllMocks());

    it("polls while the execution is still running", async () => {
        const { useExecutionDetails } = require("./queries");
        (executionService.getExecutionDetails as jest.Mock).mockResolvedValue([
            true,
            { workflowExecutionId: "e1", executionStatus: "RUNNING" },
        ]);
        const qc = client();
        const { result } = renderHook(() => useExecutionDetails("e1"), { wrapper: wrapper(qc) });
        await waitFor(() => expect(result.current.data).toBeDefined());

        // Read the resolved interval the same way React Query would.
        const query: any = qc.getQueryCache().find({ queryKey: qk.execution("e1") });
        const interval = query.options.refetchInterval;
        expect(typeof interval === "function" ? interval(query) : interval).toBe(5000);
    });

    it("stops polling once the execution reaches a terminal status", async () => {
        // Otherwise a finished execution would be re-requested every 5s for as long as the tab is open.
        const { useExecutionDetails } = require("./queries");
        (executionService.getExecutionDetails as jest.Mock).mockResolvedValue([
            true,
            { workflowExecutionId: "e2", executionStatus: "SUCCEEDED" },
        ]);
        const qc = client();
        const { result } = renderHook(() => useExecutionDetails("e2"), { wrapper: wrapper(qc) });
        await waitFor(() => expect(result.current.data).toBeDefined());

        const query: any = qc.getQueryCache().find({ queryKey: qk.execution("e2") });
        const interval = query.options.refetchInterval;
        expect(typeof interval === "function" ? interval(query) : interval).toBe(false);
    });

    it("does not poll before any data has arrived", async () => {
        const { useExecutionDetails } = require("./queries");
        (executionService.getExecutionDetails as jest.Mock).mockReturnValue(new Promise(() => {}));
        const qc = client();
        renderHook(() => useExecutionDetails("e3"), { wrapper: wrapper(qc) });

        const query: any = qc.getQueryCache().find({ queryKey: qk.execution("e3") });
        const interval = query.options.refetchInterval;
        expect(typeof interval === "function" ? interval(query) : interval).toBe(false);
    });

    it("uses the same cadence as the list views", () => {
        // One number, one helper — the detail page and the lists must not drift apart.
        expect(computeRefetchInterval([{ executionStatus: "RUNNING" }])).toBe(5000);
    });
});

/**
 * The paged detail-metadata read: the escalation path for a collection the details view returned
 * bounded. Every server page must be KEPT (the detail page renders their union), the token must
 * round-trip verbatim, and a last page — which carries no NextToken — must end the walk.
 */
describe("useExecutionDetailMetadata", () => {
    const wrapper = (qc: QueryClient) => {
        const Wrapper = ({ children }: any) => (
            <QueryClientProvider client={qc}>{children}</QueryClientProvider>
        );
        Wrapper.displayName = "QueryWrapper";
        return Wrapper;
    };
    const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

    beforeEach(() => jest.clearAllMocks());

    it("requests the named collection with the module's page size", async () => {
        const { useExecutionDetailMetadata, DETAIL_METADATA_PAGE_SIZE } = require("./queries");
        (executionService.getExecutionDetailsMetadata as jest.Mock).mockResolvedValue([
            true,
            { Items: [{ assetId: "a1" }], collection: "inputDatabase" },
        ]);
        const { result } = renderHook(
            () => useExecutionDetailMetadata("e1", "inputDatabase", true),
            { wrapper: wrapper(client()) }
        );

        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(executionService.getExecutionDetailsMetadata).toHaveBeenCalledWith("e1", {
            collection: "inputDatabase",
            pageSize: String(DETAIL_METADATA_PAGE_SIZE),
        });
    });

    it("issues no request until the caller enables it", () => {
        const { useExecutionDetailMetadata } = require("./queries");
        renderHook(() => useExecutionDetailMetadata("e1", "input", false), {
            wrapper: wrapper(client()),
        });
        expect(executionService.getExecutionDetailsMetadata).not.toHaveBeenCalled();
    });

    it("keeps both pages and resumes from the previous page's NextToken", async () => {
        const { useExecutionDetailMetadata } = require("./queries");
        (executionService.getExecutionDetailsMetadata as jest.Mock)
            .mockResolvedValueOnce([
                true,
                { Items: [{ assetId: "a1" }], collection: "input", NextToken: "tok-2" },
            ])
            .mockResolvedValueOnce([true, { Items: [{ assetId: "a2" }], collection: "input" }]);

        const { result } = renderHook(() => useExecutionDetailMetadata("e1", "input", true), {
            wrapper: wrapper(client()),
        });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(result.current.hasNextPage).toBe(true);

        await result.current.fetchNextPage();
        await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));

        // The second request resumes at the token the first page returned, unaltered.
        expect(executionService.getExecutionDetailsMetadata).toHaveBeenLastCalledWith(
            "e1",
            expect.objectContaining({ startingToken: "tok-2" })
        );
        // Nothing is skipped or repeated: the union of the pages is the full row set.
        const rows = (result.current.data as any).pages.flatMap((p: any) => p.Items);
        expect(rows).toEqual([{ assetId: "a1" }, { assetId: "a2" }]);
    });

    it("treats a page without NextToken as the last one", async () => {
        // NextToken's PRESENCE is the only "there is more" signal, so its absence must end the walk.
        const { useExecutionDetailMetadata } = require("./queries");
        (executionService.getExecutionDetailsMetadata as jest.Mock).mockResolvedValue([
            true,
            { Items: [{ assetId: "a1" }], collection: "input" },
        ]);
        const { result } = renderHook(() => useExecutionDetailMetadata("e1", "input", true), {
            wrapper: wrapper(client()),
        });

        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(result.current.hasNextPage).toBe(false);
    });

    it("surfaces a rejected read as an error rather than an empty page", async () => {
        const { useExecutionDetailMetadata } = require("./queries");
        (executionService.getExecutionDetailsMetadata as jest.Mock).mockResolvedValue([
            false,
            "Forbidden",
        ]);
        const { result } = renderHook(() => useExecutionDetailMetadata("e1", "input", true), {
            wrapper: wrapper(client()),
        });

        await waitFor(() => expect(result.current.isError).toBe(true));
        expect((result.current.error as Error).message).toBe("Forbidden");
    });

    it("keys each collection separately so one does not answer another's read", () => {
        expect(qk.executionDetailMetadata("e1", "input")).toEqual([
            "executionDetailMetadata",
            "e1",
            "input",
        ]);
        expect(qk.executionDetailMetadata("e1", "output")).not.toEqual(
            qk.executionDetailMetadata("e1", "input")
        );
    });
});
